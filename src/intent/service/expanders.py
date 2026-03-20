"""Intent surface expander and adjudicator dispatchers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from dispatch.types import ALIGNMENT_CHANGED_PENDING

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        AgentDispatcher,
        Communicator,
        LogService,
        ModelPolicyService,
        PromptGuard,
        SignalReader,
        TaskRouterService,
    )
    from intent.service.philosophy_grounding import PhilosophyGrounding



# -- Pure prompt composers (no Services usage) -----------------------------

def _compose_problem_expander_text(
    section_number: str,
    surfaces_path: Path,
    problem_path: Path,
    rubric_path: Path,
    delta_path: Path,
) -> str:
    """Return the full prompt text for the problem expander."""
    return f"""# Task: Expand Problem Definition for Section {section_number}

## Files to Read
1. Intent surfaces (problem portion): `{surfaces_path}`
2. Current problem definition: `{problem_path}`
3. Current problem alignment rubric: `{rubric_path}`

## Instructions
Validate each problem surface and integrate validated ones into the
living problem definition and rubric.
For each surface:
1. Is it already covered by an existing axis? → discard (already_covered)
2. Is it real and in scope? → integrate (expand existing axis or add new)
3. Is it out of scope? → discard (out_of_scope)

## Output
1. Update `{problem_path}` — append to existing axes or add new §AN sections
2. Update `{rubric_path}` — extend axis reference table if new axes added
3. Write delta signal to `{delta_path}`:
```json
{{
  "section": "{section_number}",
  "applied": {{
    "problem_definition_updated": true|false,
    "problem_rubric_updated": true|false
  }},
  "discarded_surface_ids": ["P-{section_number}-NNNN"],
  "applied_surface_ids": ["P-{section_number}-NNNN"],
  "new_axes": ["A7"],
  "restart_required": true|false,
  "restart_reason": "..."
}}
```

Set restart_required=true if new axes were added or existing axes
materially changed (new constraints, new success criteria).
"""


def _compose_philosophy_expander_text(
    section_number: str,
    surfaces_path: Path,
    philosophy_path: Path,
    source_map_path: Path,
    decisions_path: Path,
    delta_path: Path,
) -> str:
    """Return the full prompt text for the philosophy expander."""
    return f"""# Task: Expand Philosophy for Section {section_number}

## Files to Read
1. Intent surfaces (philosophy portion): `{surfaces_path}`
2. Current operational philosophy: `{philosophy_path}`
3. Philosophy source map (provenance): `{source_map_path}`

## Instructions
Validate each philosophy surface and classify it:

1. **Absorbable clarification** — existing principle already implies this
   → Update philosophy.md with clarification. No user gate.
2. **Source-grounded omission** — present in authorized source material
   but missed during distillation. Must cite specific passage in source.
   → Add to philosophy.md with source-map provenance entry.
3. **New root candidate** — philosophy is genuinely silent AND not
   traceable to authorized sources. This is a root-level scope change.
   → Do NOT add. Write to decisions file. User gate required.
4. **Tension** — two principles conflict in this context
   → Write to decisions file. User gate required.
5. **Contradiction** — principles cannot coexist
   → Write to decisions file. User gate required.

## Output
1. Update `{philosophy_path}` for absorbable and source-grounded omissions
2. Update `{source_map_path}` with provenance for any new principles
3. Write `{decisions_path}` ONLY if user decisions are needed
4. Write delta signal to `{delta_path}`:
```json
{{
  "section": "{section_number}",
  "applied": {{
    "philosophy_updated": true|false
  }},
  "applied_surface_ids": ["F-{section_number}-NNNN"],
  "discarded_surface_ids": [],
  "needs_user_input": true|false,
  "restart_required": true|false
}}
```
"""


def _compose_recurrence_adjudication_text(
    section_number: str,
    ids_list: str,
    recurrence_path: Path,
    adjudication_path: Path,
) -> str:
    """Return the full prompt text for recurrence adjudication."""
    return f"""# Task: Adjudicate Surface Recurrence for Section {section_number}

## Context
These previously-discarded surfaces have resurfaced during alignment:
{ids_list}

Read the recurrence signal at: `{recurrence_path}`

Each entry includes the surface's original description, evidence, and
when it was last seen. Decide for each surface whether it should be
reopened (the discard was premature or conditions changed) or kept
discarded (it is genuinely resolved or irrelevant).

## Output
Write a JSON signal to: `{adjudication_path}`
```json
{{
  "section": "{section_number}",
  "reopen_ids": [],
  "keep_discarded_ids": [],
  "reason": "..."
}}
```
"""


class Expanders:
    """Intent surface expander and adjudicator dispatchers."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        dispatcher: AgentDispatcher,
        grounding: PhilosophyGrounding,
        logger: LogService,
        policies: ModelPolicyService,
        prompt_guard: PromptGuard,
        signals: SignalReader,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._dispatcher = dispatcher
        self._grounding = grounding
        self._logger = logger
        self._policies = policies
        self._prompt_guard = prompt_guard
        self._signals = signals
        self._task_router = task_router

    def run_problem_expander(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        *,
        pending_surfaces_path: Path | None = None,
    ) -> dict | None:
        """Dispatch problem-expander and return its delta."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        artifacts = paths.artifacts
        intent_sec = paths.intent_section_dir(section_number)

        surfaces_path = (
            pending_surfaces_path
            if pending_surfaces_path is not None
            else paths.intent_surfaces_signal(section_number)
        )
        problem_path = intent_sec / "problem.md"
        rubric_path = intent_sec / "problem-alignment.md"
        delta_path = paths.intent_delta_signal(section_number)

        prompt_path = artifacts / f"problem-expand-{section_number}-prompt.md"
        output_path = artifacts / f"problem-expand-{section_number}-output.md"

        expand_prompt_text = _compose_problem_expander_text(
            section_number, surfaces_path, problem_path, rubric_path,
            delta_path,
        )
        if not self._prompt_guard.write_validated(expand_prompt_text, prompt_path):
            return None
        self._communicator.log_artifact(planspace, f"prompt:problem-expand-{section_number}")

        result = self._dispatcher.dispatch(
            self._policies.resolve(policy,"intent_problem_expander"),
            prompt_path,
            output_path,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("intent.problem_expander"),
        )

        if result == ALIGNMENT_CHANGED_PENDING:
            return None

        return self._signals.read(delta_path)

    def run_philosophy_expander(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        *,
        pending_surfaces_path: Path | None = None,
    ) -> dict | None:
        """Dispatch philosophy-expander and return its delta."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        artifacts = paths.artifacts
        intent_global = paths.intent_global_dir()

        surfaces_path = (
            pending_surfaces_path
            if pending_surfaces_path is not None
            else paths.intent_surfaces_signal(section_number)
        )
        philosophy_path = intent_global / "philosophy.md"
        source_map_path = intent_global / "philosophy-source-map.json"
        decisions_path = paths.philosophy_decisions()
        delta_path = paths.intent_delta_signal(section_number)

        prompt_path = artifacts / f"philosophy-expand-{section_number}-prompt.md"
        output_path = artifacts / f"philosophy-expand-{section_number}-output.md"

        phil_expand_text = _compose_philosophy_expander_text(
            section_number, surfaces_path, philosophy_path, source_map_path,
            decisions_path, delta_path,
        )
        if not self._prompt_guard.write_validated(phil_expand_text, prompt_path):
            return None
        self._communicator.log_artifact(planspace, f"prompt:philosophy-expand-{section_number}")

        result = self._dispatcher.dispatch(
            self._policies.resolve(policy,"intent_philosophy_expander"),
            prompt_path,
            output_path,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("intent.philosophy_expander"),
        )

        if result == ALIGNMENT_CHANGED_PENDING:
            return None

        delta = self._signals.read(delta_path)
        if delta and delta.get("applied", {}).get("philosophy_updated"):
            grounding_ok = self._grounding.validate_philosophy_grounding(
                philosophy_path,
                source_map_path,
                artifacts,
            )
            if not grounding_ok:
                self._logger.log(f"Section {section_number}: philosophy expansion broke "
                    f"grounding — expansion accepted but grounding warning "
                    f"emitted (fail-closed)")

        return delta

    def adjudicate_recurrence(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        recurrences: list[dict],
    ) -> list[str]:
        """Dispatch adjudicator to decide on discarded surfaces that resurfaced."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        artifacts = paths.artifacts
        signals_dir = paths.signals_dir()

        recurrence_signal = {
            "section": section_number,
            "discarded_resurfaced": [
                {
                    "id": recurrence["id"],
                    "kind": recurrence.get("kind", "unknown"),
                    "notes": recurrence.get("notes", ""),
                    "description": recurrence.get("description", ""),
                    "evidence": recurrence.get("evidence", ""),
                    "last_seen": recurrence.get("last_seen", {}),
                }
                for recurrence in recurrences
            ],
        }
        recurrence_path = (
            signals_dir / f"intent-surface-recurrence-{section_number}.json"
        )
        self._artifact_io.write_json(recurrence_path, recurrence_signal)

        adjudication_path = (
            signals_dir / f"intent-recurrence-adjudication-{section_number}.json"
        )
        prompt_path = artifacts / f"recurrence-adjudicate-{section_number}-prompt.md"
        output_path = artifacts / f"recurrence-adjudicate-{section_number}-output.md"

        ids_list = ", ".join(recurrence["id"] for recurrence in recurrences)
        recurrence_prompt_text = _compose_recurrence_adjudication_text(
            section_number, ids_list, recurrence_path, adjudication_path,
        )
        if not self._prompt_guard.write_validated(recurrence_prompt_text, prompt_path):
            return []
        self._communicator.log_artifact(planspace, f"prompt:recurrence-adjudicate-{section_number}")

        self._dispatcher.dispatch(
            self._policies.resolve(policy,"intent_recurrence_adjudicator"),
            prompt_path,
            output_path,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("intent.recurrence_adjudicator"),
        )

        result = self._signals.read(adjudication_path)
        if result:
            reopen = result.get("reopen_ids", [])
            if reopen:
                self._logger.log(f"Section {section_number}: adjudicator reopened "
                    f"{len(reopen)} surface(s): {reopen}")
            return reopen

        self._logger.log(f"Section {section_number}: recurrence adjudication signal "
            f"missing — keeping surfaces discarded (fail-closed)")
        return []
