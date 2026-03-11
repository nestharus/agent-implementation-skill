"""Intent surface orchestration helpers."""

from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import write_json
from lib.core.path_registry import PathRegistry
from lib.intent.philosophy_bootstrap import validate_philosophy_grounding
from prompt_safety import write_validated_prompt
from section_loop.communication import _log_artifact, log, mailbox_send
from lib.core.model_policy import resolve
from section_loop.dispatch import (
    dispatch_agent,
    read_agent_signal,
    read_model_policy,
)
from section_loop.intent.surfaces import (
    find_discarded_recurrences,
    load_intent_surfaces,
    load_surface_registry,
    mark_surfaces_applied,
    mark_surfaces_discarded,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from section_loop.pipeline_control import pause_for_parent


def build_pending_surface_payload(worklist: list[dict], surfaces: dict) -> dict:
    """Build the budgeted pending-surface payload for expanders."""
    budgeted_ids = {surface["id"] for surface in worklist}
    judge_problem = {
        surface.get("id"): surface
        for surface in surfaces.get("problem_surfaces", [])
    }
    judge_philosophy = {
        surface.get("id"): surface
        for surface in surfaces.get("philosophy_surfaces", [])
    }
    problem_surfaces: list[dict] = []
    philosophy_surfaces: list[dict] = []

    for entry in worklist:
        surface_id = entry["id"]
        if surface_id in judge_problem:
            if surface_id in budgeted_ids:
                problem_surfaces.append(judge_problem[surface_id])
        elif surface_id in judge_philosophy:
            if surface_id in budgeted_ids:
                philosophy_surfaces.append(judge_philosophy[surface_id])
        elif surface_id.startswith("P-"):
            problem_surfaces.append({
                "id": surface_id,
                "kind": entry.get("kind", ""),
                "axis_id": entry.get("axis_id", ""),
                "title": entry.get("notes", ""),
                "description": entry.get("description", ""),
                "evidence": entry.get("evidence", ""),
            })
        elif surface_id.startswith("F-"):
            philosophy_surfaces.append({
                "id": surface_id,
                "kind": entry.get("kind", ""),
                "axis_id": entry.get("axis_id", ""),
                "title": entry.get("notes", ""),
                "description": entry.get("description", ""),
                "evidence": entry.get("evidence", ""),
            })

    return {
        "problem_surfaces": problem_surfaces,
        "philosophy_surfaces": philosophy_surfaces,
    }


def run_expansion_cycle(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    budgets: dict | None = None,
) -> dict:
    """Run one expansion cycle: validate surfaces and expand definitions."""
    policy = read_model_policy(planspace)
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    budget_config = budgets or {}
    no_work = {
        "restart_required": False,
        "needs_user_input": False,
        "expansion_applied": False,
        "surfaces_found": 0,
    }

    surfaces = load_intent_surfaces(section_number, planspace)
    if not surfaces:
        return no_work

    registry = load_surface_registry(section_number, planspace)
    surfaces = normalize_surface_ids(surfaces, registry, section_number)

    new_surfaces, duplicate_ids = merge_surfaces_into_registry(
        registry,
        surfaces,
    )

    surfaces_path = paths.signals_dir() / f"intent-surfaces-{section_number}.json"
    write_json(surfaces_path, surfaces)

    if not new_surfaces:
        recurrences = find_discarded_recurrences(registry, duplicate_ids)
        if recurrences:
            reopened = adjudicate_recurrence(
                section_number,
                planspace,
                codespace,
                parent,
                policy,
                recurrences,
            )
            if reopened:
                for surface_id in reopened:
                    for entry in registry.get("surfaces", []):
                        if entry["id"] == surface_id:
                            entry["status"] = "pending"

    worklist = [
        surface for surface in registry.get("surfaces", [])
        if surface.get("status") == "pending"
    ]

    if not worklist:
        save_surface_registry(section_number, planspace, registry)
        return no_work

    max_surfaces = budget_config.get("max_new_surfaces_per_cycle", 8)
    if len(worklist) > max_surfaces:
        log(f"Section {section_number}: {len(worklist)} pending surfaces "
            f"exceeds budget of {max_surfaces} — processing oldest "
            f"{max_surfaces}")
        worklist = worklist[:max_surfaces]

    budgeted_surfaces = build_pending_surface_payload(worklist, surfaces)
    pending_surfaces_path = (
        paths.signals_dir() / f"intent-surfaces-pending-{section_number}.json"
    )
    write_json(pending_surfaces_path, budgeted_surfaces)

    axes_added = registry.get("axes_added_so_far", 0)
    max_axes = budget_config.get("max_new_axes_total", 6)
    remaining_axis_budget = max(0, max_axes - axes_added)

    delta = {
        "section": section_number,
        "applied": {
            "problem_definition_updated": False,
            "problem_rubric_updated": False,
            "philosophy_updated": False,
        },
        "discarded_surface_ids": [],
        "applied_surface_ids": [],
        "new_axes": [],
        "restart_required": False,
        "needs_user_input": False,
    }

    if budgeted_surfaces["problem_surfaces"]:
        problem_delta = run_problem_expander(
            section_number,
            planspace,
            codespace,
            parent,
            policy,
            pending_surfaces_path=pending_surfaces_path,
            remaining_axis_budget=remaining_axis_budget,
        )
        if problem_delta:
            proposed_axes = problem_delta.get("new_axes", [])
            if len(proposed_axes) > remaining_axis_budget:
                log(f"Section {section_number}: expander proposed "
                    f"{len(proposed_axes)} new axes (budget advisory: "
                    f"{remaining_axis_budget}) — accepting all")
            delta["applied"]["problem_definition_updated"] = (
                problem_delta.get("applied", {})
                .get("problem_definition_updated", False)
            )
            delta["applied"]["problem_rubric_updated"] = (
                problem_delta.get("applied", {})
                .get("problem_rubric_updated", False)
            )
            delta["applied_surface_ids"].extend(
                problem_delta.get("applied_surface_ids", []),
            )
            delta["discarded_surface_ids"].extend(
                problem_delta.get("discarded_surface_ids", []),
            )
            delta["new_axes"].extend(proposed_axes)
            if problem_delta.get("restart_required"):
                delta["restart_required"] = True
                delta["restart_reason"] = problem_delta.get(
                    "restart_reason",
                    "Problem definition expanded",
                )

    if budgeted_surfaces["philosophy_surfaces"]:
        philosophy_delta = run_philosophy_expander(
            section_number,
            planspace,
            codespace,
            parent,
            policy,
            pending_surfaces_path=pending_surfaces_path,
        )
        if philosophy_delta:
            delta["applied"]["philosophy_updated"] = (
                philosophy_delta.get("applied", {})
                .get("philosophy_updated", False)
            )
            delta["applied_surface_ids"].extend(
                philosophy_delta.get("applied_surface_ids", []),
            )
            delta["discarded_surface_ids"].extend(
                philosophy_delta.get("discarded_surface_ids", []),
            )
            if philosophy_delta.get("needs_user_input"):
                delta["needs_user_input"] = True
                delta["user_input_kind"] = "philosophy"
                delta["user_input_path"] = str(
                    artifacts / "intent" / "global" / "philosophy-decisions.md"
                )
                delta["restart_required"] = True

    mark_surfaces_applied(registry, delta["applied_surface_ids"])
    mark_surfaces_discarded(registry, delta["discarded_surface_ids"])
    registry["axes_added_so_far"] = axes_added + len(delta["new_axes"])
    save_surface_registry(section_number, planspace, registry)

    delta_path = artifacts / "signals" / f"intent-delta-{section_number}.json"
    write_json(delta_path, delta)

    expansion_applied = (
        delta["applied"]["problem_definition_updated"]
        or delta["applied"]["problem_rubric_updated"]
        or delta["applied"]["philosophy_updated"]
    )
    return {
        "restart_required": delta["restart_required"],
        "needs_user_input": delta.get("needs_user_input", False),
        "user_input_path": delta.get("user_input_path", ""),
        "expansion_applied": expansion_applied,
        "surfaces_found": len(worklist),
    }


def handle_user_gate(
    section_number: str,
    planspace: Path,
    parent: str,
    delta_result: dict,
) -> str | None:
    """Handle user gate pause if expansion needs a decision."""
    if not delta_result.get("needs_user_input"):
        return None

    artifacts = PathRegistry(planspace).artifacts
    signals_dir = artifacts / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    gate_kind = delta_result.get("user_input_kind", "unknown")
    input_path = delta_result.get(
        "user_input_path",
        "philosophy-decisions.md",
    )

    gate_messages = {
        "philosophy": {
            "detail": (
                f"Philosophy tension requires user direction: "
                f"see {input_path}"
            ),
            "needs": "User chooses stance for philosophical tension",
            "why_blocked": (
                "Cannot decide which principle to optimize "
                "without user priority"
            ),
            "pause_summary": "Philosophy tension requires user direction",
        },
        "axis_budget": {
            "detail": f"Axis budget exceeded — see {input_path}",
            "needs": "Decide which axes to accept within budget",
            "why_blocked": "Proposed axes exceed remaining axis budget",
            "pause_summary": "Axis budget exceeded",
        },
    }
    message = gate_messages.get(gate_kind, {
        "detail": f"User decision required: see {input_path}",
        "needs": "User direction needed",
        "why_blocked": f"Gate type: {gate_kind}",
        "pause_summary": f"{gate_kind} requires user direction",
    })

    blocker_path = (
        signals_dir / f"intent-expand-{section_number}-signal.json"
    )
    if not blocker_path.exists():
        write_json(blocker_path, {
            "state": "NEED_DECISION",
            "detail": message["detail"],
            "needs": message["needs"],
            "why_blocked": message["why_blocked"],
        })

    return pause_for_parent(
        planspace,
        parent,
        f"pause:need_decision:{section_number}:{message['pause_summary']}",
    )


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
    intent_sec = artifacts / "intent" / "sections" / f"section-{section_number}"
    signals_dir = artifacts / "signals"

    surfaces_path = (
        pending_surfaces_path
        if pending_surfaces_path is not None
        else signals_dir / f"intent-surfaces-{section_number}.json"
    )
    problem_path = intent_sec / "problem.md"
    rubric_path = intent_sec / "problem-alignment.md"
    delta_path = signals_dir / f"intent-delta-{section_number}.json"

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
    if not write_validated_prompt(expand_prompt_text, prompt_path):
        return None
    _log_artifact(planspace, f"prompt:problem-expand-{section_number}")

    result = dispatch_agent(
        resolve(policy, "intent_problem_expander"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file="problem-expander.md",
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return None

    return read_agent_signal(delta_path)


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
    signals_dir = artifacts / "signals"
    intent_global = artifacts / "intent" / "global"

    surfaces_path = (
        pending_surfaces_path
        if pending_surfaces_path is not None
        else signals_dir / f"intent-surfaces-{section_number}.json"
    )
    philosophy_path = intent_global / "philosophy.md"
    source_map_path = intent_global / "philosophy-source-map.json"
    decisions_path = intent_global / "philosophy-decisions.md"
    delta_path = signals_dir / f"intent-delta-{section_number}.json"

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
    if not write_validated_prompt(phil_expand_text, prompt_path):
        return None
    _log_artifact(planspace, f"prompt:philosophy-expand-{section_number}")

    result = dispatch_agent(
        resolve(policy, "intent_philosophy_expander"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file="philosophy-expander.md",
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return None

    delta = read_agent_signal(delta_path)
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
    signals_dir = artifacts / "signals"
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
    if not write_validated_prompt(recurrence_prompt_text, prompt_path):
        return []
    _log_artifact(planspace, f"prompt:recurrence-adjudicate-{section_number}")

    dispatch_agent(
        resolve(policy, "intent_recurrence_adjudicator"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file="recurrence-adjudicator.md",
    )

    result = read_agent_signal(adjudication_path)
    if result:
        reopen = result.get("reopen_ids", [])
        if reopen:
            log(f"Section {section_number}: adjudicator reopened "
                f"{len(reopen)} surface(s): {reopen}")
        return reopen

    log(f"Section {section_number}: recurrence adjudication signal "
        f"missing — keeping surfaces discarded (fail-closed)")
    return []
