"""Output adjudicator: dispatch state-adjudicator to classify ambiguous agent output."""

import json
from pathlib import Path

from containers import Services
from pipeline.template import render_template
from orchestrator.path_registry import PathRegistry
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE, SIGNAL_NEED_DECISION


def _compose_adjudication_text(output_path: Path) -> str:
    """Build the dynamic prompt body for output adjudication."""
    return f"""# Classify Agent Output

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

    paths = PathRegistry(planspace)
    adj_prompt = paths.adjudicate_prompt()
    adj_output = paths.adjudicate_output()

    dynamic_body = _compose_adjudication_text(output_path)
    violations = Services.prompt_guard().validate_dynamic(dynamic_body)
    if violations:
        Services.logger().log(f"  ERROR: adjudicate prompt blocked — dynamic violations: {violations}")
        return None, ""
    adj_prompt.write_text(
        render_template(
            "adjudicate", dynamic_body,
            file_paths=[str(output_path)],
        ),
        encoding="utf-8",
    )

    result = Services.dispatcher().dispatch(
        model, adj_prompt, adj_output,
        planspace, parent, codespace=codespace,
        agent_file=Services.task_router().agent_for("staleness.state_adjudicate"),
    )
    if result == ALIGNMENT_CHANGED_PENDING:
        return None, ALIGNMENT_CHANGED_PENDING

    # Parse JSON from adjudicator output
    try:
        json_start = result.output.find("{")
        json_end = result.output.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(result.output[json_start:json_end + 1])
            state = data.get("state", "").lower()
            detail = data.get("detail", "")
            if state in ("underspecified", "underspec"):
                return "underspec", detail
            if state == SIGNAL_NEED_DECISION:
                return SIGNAL_NEED_DECISION, detail
            if state == "dependency":
                return "dependency", detail
            if state == "loop_detected":
                return "loop_detected", detail
            if state == SIGNAL_NEEDS_PARENT:
                return SIGNAL_NEEDS_PARENT, detail
            if state in (SIGNAL_OUT_OF_SCOPE, "out-of-scope"):
                return SIGNAL_OUT_OF_SCOPE, detail
    except (json.JSONDecodeError, KeyError) as exc:
        print(
            f"[ADJUDICATOR][WARN] Malformed adjudicator verdict JSON "
            f"({exc}) — treating as unrecognized signal",
        )
    return None, ""
