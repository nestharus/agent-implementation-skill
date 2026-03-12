"""Intent surface expander and adjudicator dispatchers."""

from __future__ import annotations

from pathlib import Path

from containers import Services
from signals.repository.artifact_io import write_json
from orchestrator.path_registry import PathRegistry
from intent.service.philosophy import validate_philosophy_grounding
from signals.service.communication import log
from taskrouter import agent_for


def run_problem_expander(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    *,
    pending_surfaces_path: Path | None = None,
    remaining_axis_budget: int = 6,
) -> dict | None:
    """Dispatch problem-expander and return its delta."""
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

    axis_budget_note = ""
    if remaining_axis_budget < 6:
        axis_budget_note = (
            f"\n**Axis budget**: {remaining_axis_budget} new axes remaining. "
            f"Prefer expanding existing axes over adding new ones when "
            f"possible.\n"
        )

    expand_prompt_text = f"""# Task: Expand Problem Definition for Section {section_number}

## Files to Read
1. Intent surfaces (problem portion): `{surfaces_path}`
2. Current problem definition: `{problem_path}`
3. Current problem alignment rubric: `{rubric_path}`

## Instructions
Validate each problem surface and integrate validated ones into the
living problem definition and rubric.
{axis_budget_note}
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
    if not Services.prompt_guard().write_validated(expand_prompt_text, prompt_path):
        return None
    Services.communicator().log_artifact(planspace, f"prompt:problem-expand-{section_number}")

    result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy,"intent_problem_expander"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file=agent_for("intent.problem_expander"),
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return None

    return Services.signals().read(delta_path)


def run_philosophy_expander(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    *,
    pending_surfaces_path: Path | None = None,
) -> dict | None:
    """Dispatch philosophy-expander and return its delta."""
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

    phil_expand_text = f"""# Task: Expand Philosophy for Section {section_number}

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
    if not Services.prompt_guard().write_validated(phil_expand_text, prompt_path):
        return None
    Services.communicator().log_artifact(planspace, f"prompt:philosophy-expand-{section_number}")

    result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy,"intent_philosophy_expander"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file=agent_for("intent.philosophy_expander"),
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return None

    delta = Services.signals().read(delta_path)
    if delta and delta.get("applied", {}).get("philosophy_updated"):
        grounding_ok = validate_philosophy_grounding(
            philosophy_path,
            source_map_path,
            artifacts,
        )
        if not grounding_ok:
            log(f"Section {section_number}: philosophy expansion broke "
                f"grounding — expansion accepted but grounding warning "
                f"emitted (fail-closed)")

    return delta


def adjudicate_recurrence(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    recurrences: list[dict],
) -> list[str]:
    """Dispatch adjudicator to decide on discarded surfaces that resurfaced."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    signals_dir = paths.signals_dir()
    signals_dir.mkdir(parents=True, exist_ok=True)

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
    write_json(recurrence_path, recurrence_signal)

    adjudication_path = (
        signals_dir / f"intent-recurrence-adjudication-{section_number}.json"
    )
    prompt_path = artifacts / f"recurrence-adjudicate-{section_number}-prompt.md"
    output_path = artifacts / f"recurrence-adjudicate-{section_number}-output.md"

    ids_list = ", ".join(recurrence["id"] for recurrence in recurrences)
    recurrence_prompt_text = f"""# Task: Adjudicate Surface Recurrence for Section {section_number}

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
    if not Services.prompt_guard().write_validated(recurrence_prompt_text, prompt_path):
        return []
    Services.communicator().log_artifact(planspace, f"prompt:recurrence-adjudicate-{section_number}")

    Services.dispatcher().dispatch(
        Services.policies().resolve(policy,"intent_recurrence_adjudicator"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file=agent_for("intent.recurrence_adjudicator"),
    )

    result = Services.signals().read(adjudication_path)
    if result:
        reopen = result.get("reopen_ids", [])
        if reopen:
            log(f"Section {section_number}: adjudicator reopened "
                f"{len(reopen)} surface(s): {reopen}")
        return reopen

    log(f"Section {section_number}: recurrence adjudication signal "
        f"missing — keeping surfaces discarded (fail-closed)")
    return []
