"""Output adjudicator: dispatch state-adjudicator to classify ambiguous agent output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pipeline.template import render_template
from orchestrator.path_registry import PathRegistry
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import (
    SIGNAL_DEPENDENCY,
    SIGNAL_LOOP_DETECTED,
    SIGNAL_NEED_DECISION,
    SIGNAL_NEEDS_PARENT,
    SIGNAL_OUT_OF_SCOPE,
    SIGNAL_UNDERSPEC,
)

if TYPE_CHECKING:
    from containers import AgentDispatcher, LogService, PromptGuard, TaskRouterService


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


class OutputAdjudicator:
    """Dispatches state-adjudicator to classify ambiguous agent output."""

    def __init__(
        self,
        prompt_guard: PromptGuard,
        logger: LogService,
        dispatcher: AgentDispatcher,
        task_router: TaskRouterService,
    ) -> None:
        self._prompt_guard = prompt_guard
        self._logger = logger
        self._dispatcher = dispatcher
        self._task_router = task_router

    def adjudicate_agent_output(
        self,
        output_path: Path, planspace: Path,
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
        violations = self._prompt_guard.validate_dynamic(dynamic_body)
        if violations:
            self._logger.log(f"  ERROR: adjudicate prompt blocked — dynamic violations: {violations}")
            return None, ""
        adj_prompt.write_text(
            render_template(
                "adjudicate", dynamic_body,
                file_paths=[str(output_path)],
            ),
            encoding="utf-8",
        )

        result = self._dispatcher.dispatch(
            model, adj_prompt, adj_output,
            planspace, codespace=codespace,
            agent_file=self._task_router.agent_for("staleness.state_adjudicate"),
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
                if state in ("underspecified", SIGNAL_UNDERSPEC):
                    return SIGNAL_UNDERSPEC, detail
                if state == SIGNAL_NEED_DECISION:
                    return SIGNAL_NEED_DECISION, detail
                if state == SIGNAL_DEPENDENCY:
                    return SIGNAL_DEPENDENCY, detail
                if state == SIGNAL_LOOP_DETECTED:
                    return SIGNAL_LOOP_DETECTED, detail
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


# ---------------------------------------------------------------------------
# Backward-compat wrappers
# ---------------------------------------------------------------------------

def _get_adjudicator() -> OutputAdjudicator:
    from containers import Services
    return OutputAdjudicator(
        prompt_guard=Services.prompt_guard(),
        logger=Services.logger(),
        dispatcher=Services.dispatcher(),
        task_router=Services.task_router(),
    )


def adjudicate_agent_output(
    output_path: Path, planspace: Path,
    codespace: Path | None = None,
    *,
    model: str,
) -> tuple[str | None, str]:
    """Dispatch state-adjudicator to classify ambiguous agent output."""
    return _get_adjudicator().adjudicate_agent_output(
        output_path, planspace, codespace, model=model,
    )
