from pathlib import Path

from staleness.service.alignment import (
    collect_modified_files as _collect_modified_files,
    extract_problems,
)
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.template import render_template
from signals.service.communication import log
from dispatch.engine.section_dispatch import dispatch_agent
from proposal.helpers.verdict_parsers import parse_alignment_verdict as _parse_alignment_verdict
from dispatch.service.prompt_safety import validate_dynamic_content
from orchestrator.service.pipeline_control import poll_control_messages
from orchestrator.types import Section
from taskrouter import agent_for


def collect_modified_files(
    planspace: Path, section: Section, codespace: Path,
) -> list[str]:
    """Collect modified files from the implementation report."""
    return _collect_modified_files(
        planspace,
        section,
        codespace,
        logger=log,
    )


def _extract_problems(
    result: str,
    output_path: Path | None = None,
    planspace: Path | None = None,
    parent: str | None = None,
    codespace: Path | None = None,
    *,
    adjudicator_model: str,
) -> str | None:
    """Extract problem list from an alignment check result.

    Returns the problems text if misaligned, ``None`` if aligned.
    Uses the structured JSON verdict (``aligned``, ``problems``)
    when available.  When no JSON verdict is found, dispatches a GLM
    adjudicator to classify the raw output — scripts never interpret
    meaning from text.
    """
    import json as _json

    # Primary: structured JSON verdict from alignment judge
    verdict = _parse_alignment_verdict(result)
    if verdict is not None:
        return extract_problems(verdict)

    # Fallback: dispatch GLM adjudicator to classify the alignment output.
    # Scripts must not interpret meaning from text — the adjudicator decides.
    if output_path is not None and planspace is not None and parent is not None:
        paths = PathRegistry(planspace)
        artifacts = paths.artifacts
        artifacts.mkdir(parents=True, exist_ok=True)
        adj_prompt = artifacts / "alignment-adjudicate-prompt.md"
        adj_output = artifacts / "alignment-adjudicate-output.md"
        dynamic_body = f"""# Classify Alignment Check Output

Read the alignment check output and determine whether the section is aligned.

## Alignment Output File
`{output_path}`

## Instructions

The alignment judge was expected to produce a structured JSON verdict
but did not.  Read its output and classify the result.

Reply with a JSON block:

```json
{{
  "aligned": true|false,
  "problems": ["list of problems if misaligned, empty if aligned"],
  "reason": "brief explanation of classification"
}}
```

- If the output indicates alignment / no issues → `"aligned": true`
- If the output identifies problems or misalignment → `"aligned": false`
  with the problems listed
- If you cannot determine the state → `"aligned": false` with a single
  problem: "Unable to determine alignment state from judge output"
"""
        # Validate dynamic body before wrapping in template
        violations = validate_dynamic_content(dynamic_body)
        if violations:
            log(f"Alignment adjudicate prompt safety violation: "
                f"{violations} — skipping dispatch")
            return None

        adj_prompt.write_text(
            render_template(
                "alignment-adjudicate", dynamic_body,
                file_paths=[str(output_path)],
            ),
            encoding="utf-8",
        )

        adj_result = dispatch_agent(
            adjudicator_model, adj_prompt, adj_output,
            planspace, parent, codespace=codespace,
            agent_file=agent_for("staleness.alignment_adjudicate"),
        )
        if adj_result and adj_result != "ALIGNMENT_CHANGED_PENDING":
            try:
                json_start = adj_result.find("{")
                json_end = adj_result.rfind("}")
                if json_start >= 0 and json_end > json_start:
                    data = _json.loads(adj_result[json_start:json_end + 1])
                    if data.get("aligned") is True:
                        return None
                    problems = data.get("problems")
                    if isinstance(problems, list) and problems:
                        return "\n".join(str(p) for p in problems)
                    if isinstance(problems, str) and problems.strip():
                        return problems.strip()
                    return ("Adjudicator classified as misaligned "
                            "(no detail)")
            except (_json.JSONDecodeError, KeyError):
                pass

    # No structured verdict and no adjudicator available — treat as
    # misaligned to avoid false convergence.
    return ("MISSING_JSON_VERDICT: alignment judge did not produce "
            "structured output and adjudicator was not available")


def _run_alignment_check_with_retries(
    section: Section, planspace: Path, codespace: Path, parent: str,
    sec_num: str,
    output_prefix: str = "align",
    max_retries: int = 2,
    *,
    model: str,
    adjudicator_model: str,
) -> str | None:
    """Run an alignment check with TIMEOUT retry logic.

    Dispatches the specified model for an implementation alignment check.
    If the agent times out, retries up to max_retries times. Returns the
    alignment result text, or None if all retries exhausted.
    """
    from dispatch.prompt.writers import write_impl_alignment_prompt

    paths = PathRegistry(planspace)
    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        ctrl = poll_control_messages(planspace, parent,
                                     current_section=sec_num)
        if ctrl == "alignment_changed":
            return "ALIGNMENT_CHANGED_PENDING"
        align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = paths.artifacts / f"{output_prefix}-{sec_num}-output.md"
        result = dispatch_agent(
            model, align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=sec_num,
            agent_file=agent_for("staleness.alignment_check"),
        )
        if result == "ALIGNMENT_CHANGED_PENDING":
            return result
        if not result.startswith("TIMEOUT:"):
            # Check for structured JSON verdict from alignment judge
            verdict = _parse_alignment_verdict(result)
            if verdict is not None and verdict.get("frame_ok") is False:
                # Structural failure — the alignment prompt frame was
                # invalid.  Do NOT retry; surface upward for parent
                # intervention.  Retrying the same broken frame wastes
                # cycles without fixing the root cause.
                log(f"  alignment judge reported invalid frame for "
                    f"section {sec_num} — structural failure, "
                    f"requires parent intervention")
                return "INVALID_FRAME"
            return result
        log(f"  alignment check for section {sec_num} timed out "
            f"(attempt {attempt}/{max_retries + 1})")
    return None
