from pathlib import Path

from staleness.service.alignment_collector import (
    collect_modified_files as _collect_modified_files,
    extract_problems,
)
from orchestrator.path_registry import PathRegistry
from pipeline.template import render_template
from containers import Services
from staleness.helpers.verdict_parsers import parse_alignment_verdict as _parse_alignment_verdict
from orchestrator.types import Section


def collect_modified_files(
    planspace: Path, section: Section, codespace: Path,
) -> list[str]:
    """Collect modified files from the implementation report."""
    return _collect_modified_files(
        planspace,
        section,
        codespace,
    )


def _build_adjudicator_prompt(output_path: Path) -> str:
    """Build the prompt for the alignment adjudicator fallback."""
    return f"""# Classify Alignment Check Output

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


def _parse_adjudicator_response(adj_result: str) -> str | None:
    """Parse the adjudicator's JSON response. Returns problems or None if aligned."""
    import json as _json
    if not adj_result or adj_result == "ALIGNMENT_CHANGED_PENDING":
        return None
    try:
        json_start = adj_result.find("{")
        json_end = adj_result.rfind("}")
        if json_start < 0 or json_end <= json_start:
            return None
        data = _json.loads(adj_result[json_start:json_end + 1])
        if data.get("aligned") is True:
            return None
        problems = data.get("problems")
        if isinstance(problems, list) and problems:
            return "\n".join(str(p) for p in problems)
        if isinstance(problems, str) and problems.strip():
            return problems.strip()
        return "Adjudicator classified as misaligned (no detail)"
    except (_json.JSONDecodeError, KeyError):
        return None


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
    """
    verdict = _parse_alignment_verdict(result)
    if verdict is not None:
        return extract_problems(verdict)

    if output_path is not None and planspace is not None and parent is not None:
        paths = PathRegistry(planspace)
        paths.artifacts.mkdir(parents=True, exist_ok=True)
        dynamic_body = _build_adjudicator_prompt(output_path)
        violations = Services.prompt_guard().validate_dynamic(dynamic_body)
        if violations:
            Services.logger().log(
                f"Alignment adjudicate prompt safety violation: "
                f"{violations} — skipping dispatch"
            )
            return None

        adj_prompt = paths.alignment_adjudicate_prompt()
        adj_prompt.write_text(
            render_template(
                "alignment-adjudicate", dynamic_body,
                file_paths=[str(output_path)],
            ),
            encoding="utf-8",
        )
        adj_result = Services.dispatcher().dispatch(
            adjudicator_model, adj_prompt, paths.alignment_adjudicate_output(),
            planspace, parent, codespace=codespace,
            agent_file=Services.task_router().agent_for("staleness.alignment_adjudicate"),
        )
        return _parse_adjudicator_response(adj_result)

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
        ctrl = Services.pipeline_control().poll_control_messages(
            planspace, parent, current_section=sec_num)
        if ctrl == "alignment_changed":
            return "ALIGNMENT_CHANGED_PENDING"
        align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = paths.artifacts / f"{output_prefix}-{sec_num}-output.md"
        result = Services.dispatcher().dispatch(
            model, align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=sec_num,
            agent_file=Services.task_router().agent_for("staleness.alignment_check"),
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
                Services.logger().log(f"  alignment judge reported invalid frame for "
                    f"section {sec_num} — structural failure, "
                    f"requires parent intervention")
                return "INVALID_FRAME"
            return result
        Services.logger().log(f"  alignment check for section {sec_num} timed out "
            f"(attempt {attempt}/{max_retries + 1})")
    return None
