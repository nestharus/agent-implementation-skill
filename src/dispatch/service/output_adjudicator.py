"""Output adjudicator: dispatch state-adjudicator to classify ambiguous agent output."""

import json
from pathlib import Path

from dispatch.prompt.template import render_template
from dispatch.service.prompt_safety import validate_dynamic_content
from orchestrator.path_registry import PathRegistry
from taskrouter import agent_for


def adjudicate_agent_output(
    output_path: Path, planspace: Path, parent: str,
    codespace: Path | None = None,
    *,
    model: str,
) -> tuple[str | None, str]:
    """Dispatch state-adjudicator to classify ambiguous agent output.

    Used when structured signal file is absent but output may contain
    signals. Returns (signal_type, detail) or (None, "").
    """
    # Lazy import to avoid circular dependency (dispatch_agent lives in
    # the same package and imports from many modules at module level).
    from dispatch.engine.section_dispatch import dispatch_agent

    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    artifacts.mkdir(parents=True, exist_ok=True)
    adj_prompt = artifacts / "adjudicate-prompt.md"
    adj_output = artifacts / "adjudicate-output.md"

    dynamic_body = f"""# Classify Agent Output

Read the agent output file and determine its state.

## Agent Output File
`{output_path}`

## Instructions

Classify the output into exactly one state. Reply with a JSON block:

```json
{{
  "state": "<STATE>",
  "detail": "<brief explanation>"
}}
```

States: ALIGNED, PROBLEMS, UNDERSPECIFIED, NEED_DECISION, DEPENDENCY,
LOOP_DETECTED, NEEDS_PARENT, OUT_OF_SCOPE, COMPLETED, UNKNOWN.
"""
    violations = validate_dynamic_content(dynamic_body)
    if violations:
        from signals.service.communication import log
        log(f"  ERROR: adjudicate prompt blocked — dynamic violations: {violations}")
        return None, ""
    adj_prompt.write_text(
        render_template(
            "adjudicate", dynamic_body,
            file_paths=[str(output_path)],
        ),
        encoding="utf-8",
    )

    result = dispatch_agent(
        model, adj_prompt, adj_output,
        planspace, parent, codespace=codespace,
        agent_file=agent_for("staleness.state_adjudicate"),
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return None, "ALIGNMENT_CHANGED_PENDING"

    # Parse JSON from adjudicator output
    try:
        json_start = result.find("{")
        json_end = result.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(result[json_start:json_end + 1])
            state = data.get("state", "").lower()
            detail = data.get("detail", "")
            if state in ("underspecified", "underspec"):
                return "underspec", detail
            if state == "need_decision":
                return "need_decision", detail
            if state == "dependency":
                return "dependency", detail
            if state == "loop_detected":
                return "loop_detected", detail
            if state == "needs_parent":
                return "needs_parent", detail
            if state in ("out_of_scope", "out-of-scope"):
                return "out_of_scope", detail
    except (json.JSONDecodeError, KeyError) as exc:
        print(
            f"[ADJUDICATOR][WARN] Malformed adjudicator verdict JSON "
            f"({exc}) — treating as unrecognized signal",
        )
    return None, ""
