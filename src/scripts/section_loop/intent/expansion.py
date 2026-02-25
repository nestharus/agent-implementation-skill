"""Expansion cycle: dispatch expanders, interpret deltas, decide restart."""

import json
from pathlib import Path

from ..communication import _log_artifact, log, mailbox_send
from ..dispatch import dispatch_agent, read_agent_signal, read_model_policy
from ..pipeline_control import pause_for_parent
from .surfaces import (
    load_intent_surfaces,
    load_surface_registry,
    mark_surfaces_applied,
    mark_surfaces_discarded,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
    surfaces_are_diminishing,
)


def run_expansion_cycle(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    budgets: dict | None = None,
) -> dict:
    """Run one expansion cycle: validate surfaces, expand definitions.

    Returns a dict with:
    - ``restart_required``: bool — whether the proposer should re-propose
    - ``needs_user_input``: bool — whether user decision is required
    - ``user_input_path``: str — path to decisions file if needed
    - ``expansion_applied``: bool — whether any definitions changed
    - ``surfaces_found``: int — how many new surfaces were processed
    """
    policy = read_model_policy(planspace)
    artifacts = planspace / "artifacts"
    _budgets = budgets or {}

    # Load surfaces signal written by intent-judge
    surfaces = load_intent_surfaces(section_number, planspace)
    if not surfaces:
        return {
            "restart_required": False,
            "needs_user_input": False,
            "expansion_applied": False,
            "surfaces_found": 0,
        }

    # Load and update surface registry
    registry = load_surface_registry(section_number, planspace)

    # Assign stable mechanical IDs before merge (P3/R52)
    surfaces = normalize_surface_ids(surfaces, registry, section_number)

    new_surfaces, duplicate_ids = merge_surfaces_into_registry(
        registry, surfaces,
    )

    # Rewrite surfaces file with normalized IDs so expanders see them
    surfaces_path = (
        planspace / "artifacts" / "signals"
        / f"intent-surfaces-{section_number}.json"
    )
    surfaces_path.write_text(json.dumps(surfaces, indent=2), encoding="utf-8")

    # Check diminishing returns
    if surfaces_are_diminishing(registry, new_surfaces, duplicate_ids):
        log(f"Section {section_number}: surfaces diminishing — "
            f"skipping expansion")
        save_surface_registry(section_number, planspace, registry)
        return {
            "restart_required": False,
            "needs_user_input": False,
            "expansion_applied": False,
            "surfaces_found": len(new_surfaces),
            "diminishing": True,
        }

    # Check budget
    max_surfaces = _budgets.get("max_new_surfaces_per_cycle", 8)
    if len(new_surfaces) > max_surfaces:
        log(f"Section {section_number}: {len(new_surfaces)} surfaces "
            f"exceeds budget of {max_surfaces} — truncating")
        new_surfaces = new_surfaces[:max_surfaces]

    # Dispatch problem expander (if problem surfaces exist)
    problem_surfaces = surfaces.get("problem_surfaces", [])
    philosophy_surfaces = surfaces.get("philosophy_surfaces", [])

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

    if problem_surfaces:
        problem_delta = _run_problem_expander(
            section_number, planspace, codespace, parent, policy,
        )
        if problem_delta:
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
            delta["new_axes"].extend(
                problem_delta.get("new_axes", []),
            )
            if problem_delta.get("restart_required"):
                delta["restart_required"] = True
                delta["restart_reason"] = problem_delta.get(
                    "restart_reason", "Problem definition expanded",
                )

    if philosophy_surfaces:
        philosophy_delta = _run_philosophy_expander(
            section_number, planspace, codespace, parent, policy,
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
                    artifacts / "intent" / "global"
                    / "philosophy-decisions.md"
                )
                delta["restart_required"] = True

    # Update registry with applied/discarded status
    mark_surfaces_applied(registry, delta["applied_surface_ids"])
    mark_surfaces_discarded(registry, delta["discarded_surface_ids"])
    save_surface_registry(section_number, planspace, registry)

    # Write the combined delta signal
    delta_path = (
        artifacts / "signals" / f"intent-delta-{section_number}.json"
    )
    delta_path.parent.mkdir(parents=True, exist_ok=True)
    delta_path.write_text(json.dumps(delta, indent=2), encoding="utf-8")

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
        "surfaces_found": len(new_surfaces),
    }


def handle_user_gate(
    section_number: str,
    planspace: Path,
    parent: str,
    delta_result: dict,
) -> str | None:
    """Handle user gate pause if expansion needs a decision.

    Returns the user's response message, or None if no gate needed.
    """
    if not delta_result.get("needs_user_input"):
        return None

    artifacts = planspace / "artifacts"
    signals_dir = artifacts / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    # Write blocker signal for rollup
    blocker = {
        "state": "NEED_DECISION",
        "detail": (
            f"Philosophy tension requires user direction: "
            f"see {delta_result.get('user_input_path', 'philosophy-decisions.md')}"
        ),
        "needs": "User chooses stance for philosophical tension",
        "why_blocked": (
            "Cannot decide which principle to optimize "
            "without user priority"
        ),
    }
    blocker_path = (
        signals_dir / f"intent-expand-{section_number}-signal.json"
    )
    blocker_path.write_text(json.dumps(blocker, indent=2), encoding="utf-8")

    # Pause for parent
    response = pause_for_parent(
        planspace, parent,
        f"pause:need_decision:{section_number}:"
        f"Philosophy tension requires user direction",
    )
    return response


def _run_problem_expander(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> dict | None:
    """Dispatch problem-expander and return its delta."""
    artifacts = planspace / "artifacts"
    intent_sec = (
        artifacts / "intent" / "sections" / f"section-{section_number}"
    )
    signals_dir = artifacts / "signals"

    surfaces_path = signals_dir / f"intent-surfaces-{section_number}.json"
    problem_path = intent_sec / "problem.md"
    rubric_path = intent_sec / "problem-alignment.md"
    delta_path = signals_dir / f"intent-delta-{section_number}.json"

    prompt_path = artifacts / f"problem-expand-{section_number}-prompt.md"
    output_path = artifacts / f"problem-expand-{section_number}-output.md"

    prompt_path.write_text(f"""# Task: Expand Problem Definition for Section {section_number}

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
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:problem-expand-{section_number}")

    result = dispatch_agent(
        policy.get("intent_problem_expander", "claude-opus"),
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


def _run_philosophy_expander(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> dict | None:
    """Dispatch philosophy-expander and return its delta."""
    artifacts = planspace / "artifacts"
    signals_dir = artifacts / "signals"
    intent_global = artifacts / "intent" / "global"

    surfaces_path = signals_dir / f"intent-surfaces-{section_number}.json"
    philosophy_path = intent_global / "philosophy.md"
    decisions_path = intent_global / "philosophy-decisions.md"
    delta_path = signals_dir / f"intent-delta-{section_number}.json"

    prompt_path = artifacts / f"philosophy-expand-{section_number}-prompt.md"
    output_path = artifacts / f"philosophy-expand-{section_number}-output.md"

    prompt_path.write_text(f"""# Task: Expand Philosophy for Section {section_number}

## Files to Read
1. Intent surfaces (philosophy portion): `{surfaces_path}`
2. Current operational philosophy: `{philosophy_path}`

## Instructions
Validate each philosophy surface and classify it:

1. **Absorbable clarification** — existing principle already implies this
   → Update philosophy.md with clarification. No user gate.
2. **Compatible addition** — new principle that doesn't conflict
   → Add provisionally to philosophy.md. Notify parent (non-blocking).
3. **Tension** — two principles conflict in this context
   → Write to decisions file. User gate required.
4. **New axis** — philosophy is silent on this dimension
   → Write to decisions file. User gate required.
5. **Contradiction** — principles cannot coexist
   → Write to decisions file. User gate required.

## Output
1. Update `{philosophy_path}` for absorbable and compatible additions
2. Write `{decisions_path}` ONLY if user decisions are needed
3. Write delta signal to `{delta_path}`:
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
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:philosophy-expand-{section_number}")

    result = dispatch_agent(
        policy.get("intent_philosophy_expander", "claude-opus"),
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

    return read_agent_signal(delta_path)
