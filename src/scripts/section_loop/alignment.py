from pathlib import Path

from .agent_templates import render_template
from .communication import log
from .dispatch import dispatch_agent
from .pipeline_control import poll_control_messages
from .types import Section


def collect_modified_files(
    planspace: Path, section: Section, codespace: Path,
) -> list[str]:
    """Collect modified file paths from the implementation report.

    Normalizes all paths to safe relative paths under ``codespace``.
    Absolute paths are converted to relative (if under codespace) or
    rejected. Paths containing ``..`` that escape codespace are rejected.
    """
    artifacts = planspace / "artifacts"
    modified_report = artifacts / f"impl-{section.number}-modified.txt"
    codespace_resolved = codespace.resolve()
    modified = set()
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                # Convert absolute to relative if under codespace
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    log(f"  WARNING: reported path outside codespace, "
                        f"skipping: {line}")
                    continue
            else:
                # Resolve relative path and ensure it stays under codespace
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    log(f"  WARNING: reported path escapes codespace, "
                        f"skipping: {line}")
                    continue
            modified.add(str(rel))
    return list(modified)


def _parse_alignment_verdict(result: str) -> dict | None:
    """Parse structured verdict from alignment judge output.

    Looks for a JSON block containing ``frame_ok``.  Returns the full
    dict (which may also contain ``aligned`` and ``problems``), or
    ``None`` if no JSON verdict is found.
    """
    import json as _json

    def _try_parse(text: str) -> dict | None:
        try:
            data = _json.loads(text)
            if isinstance(data, dict) and "frame_ok" in data:
                return data
        except _json.JSONDecodeError:
            pass
        return None

    # Single-line JSON
    for line in result.split("\n"):
        stripped = line.strip()
        if stripped.startswith("{") and "frame_ok" in stripped:
            parsed = _try_parse(stripped)
            if parsed:
                return parsed

    # Code-fenced JSON
    in_fence = False
    fence_lines: list[str] = []
    for line in result.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") and not in_fence:
            in_fence = True
            fence_lines = []
            continue
        if stripped.startswith("```") and in_fence:
            candidate = "\n".join(fence_lines)
            if "frame_ok" in candidate:
                parsed = _try_parse(candidate)
                if parsed:
                    return parsed
            in_fence = False
            continue
        if in_fence:
            fence_lines.append(line)
    return None


def _extract_problems(
    result: str,
    output_path: Path | None = None,
    planspace: Path | None = None,
    parent: str | None = None,
    codespace: Path | None = None,
    adjudicator_model: str = "glm",
) -> str | None:
    """Extract problem list from an alignment check result.

    Returns the problems text if misaligned, ``None`` if aligned.
    Uses the structured JSON verdict (``aligned``, ``problems``)
    when available.  When no JSON verdict is found, dispatches a GLM
    adjudicator to classify the raw output — scripts never interpret
    meaning from text.

    The ``adjudicator_model`` parameter defaults to ``"glm"`` but callers
    should pass ``policy["adjudicator"]`` for policy-driven selection.
    """
    import json as _json

    # Primary: structured JSON verdict from alignment judge
    verdict = _parse_alignment_verdict(result)
    if verdict is not None:
        if verdict.get("aligned", False):
            return None
        problems = verdict.get("problems")
        if isinstance(problems, list) and problems:
            return "\n".join(str(p) for p in problems)
        if isinstance(problems, str) and problems.strip():
            return problems.strip()
        return "Alignment judge reported misaligned (no details in verdict)"

    # Fallback: dispatch GLM adjudicator to classify the alignment output.
    # Scripts must not interpret meaning from text — the adjudicator decides.
    if output_path is not None and planspace is not None and parent is not None:
        artifacts = planspace / "artifacts"
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
            agent_file="alignment-output-adjudicator.md",
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
    model: str = "claude-opus",
    adjudicator_model: str = "glm",
) -> str | None:
    """Run an alignment check with TIMEOUT retry logic.

    Dispatches the specified model for an implementation alignment check.
    If the agent times out, retries up to max_retries times. Returns the
    alignment result text, or None if all retries exhausted.

    The ``model`` parameter defaults to ``"claude-opus"`` but callers
    should pass ``policy["alignment"]`` for policy-driven selection.
    The ``adjudicator_model`` defaults to ``"glm"`` but callers should
    pass ``policy["adjudicator"]``.
    """
    from .prompts import write_impl_alignment_prompt

    artifacts = planspace / "artifacts"
    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        ctrl = poll_control_messages(planspace, parent,
                                     current_section=sec_num)
        if ctrl == "alignment_changed":
            return "ALIGNMENT_CHANGED_PENDING"
        align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = artifacts / f"{output_prefix}-{sec_num}-output.md"
        result = dispatch_agent(
            model, align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=sec_num,
            agent_file="alignment-judge.md",
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
