"""Scope-delta aggregation and adjudication helpers."""

from __future__ import annotations

from pathlib import Path

from signals.repository.artifact_io import read_json, rename_malformed, write_json
from dispatch.service.model_policy import resolve
from orchestrator.repository.decisions import Decision, load_decisions, record_decision
from orchestrator.path_registry import PathRegistry
from implementation.service.scope_delta_parser import (
    normalize_section_id,
    parse_scope_delta_adjudication,
)
from dispatch.service.prompt_guard import write_validated_prompt
from signals.service.communication import _log_artifact, log, mailbox_send
from dispatch.engine.section_dispatch import dispatch_agent
from taskrouter import agent_for


class ScopeDeltaAggregationExit(Exception):
    """Raised when scope-delta adjudication must fail closed."""


def _load_pending_deltas(scope_deltas_dir: Path) -> tuple[list[Path], list[dict]]:
    delta_files = sorted(
        path
        for path in scope_deltas_dir.iterdir()
        if path.suffix == ".json" and not path.name.endswith(".malformed.json")
    )
    pending_deltas: list[dict] = []
    for delta_file in delta_files:
        delta = read_json(delta_file)
        if delta is not None:
            if delta.get("adjudicated"):
                continue
            pending_deltas.append(delta)
        else:
            log(
                f"  coordinator: WARNING — malformed scope-delta "
                f"{delta_file.name}, preserving as .malformed.json",
            )
    return delta_files, pending_deltas


def _write_adjudication_prompt(
    coord_dir: Path,
    pending_deltas: list[dict],
) -> tuple[Path, Path]:
    adjudication_prompt = coord_dir / "scope-delta-prompt.md"
    adjudication_output = coord_dir / "scope-delta-output.md"
    pending_deltas_path = coord_dir / "scope-deltas-pending.json"
    prompt_deltas = []
    for delta in pending_deltas:
        prompt_delta = dict(delta)
        prompt_delta["requires_root_reframing"] = bool(
            delta.get("requires_root_reframing", False),
        )
        prompt_deltas.append(prompt_delta)
    write_json(pending_deltas_path, prompt_deltas)

    prompt_text = f"""# Task: Adjudicate Scope Deltas

## Pending Scope Deltas

Read the pending scope deltas from: `{pending_deltas_path}`

Each delta has a unique `delta_id`. Use it as the primary key in your
decisions so the system can apply each decision back to the exact
originating artifact.

## Instructions

Each scope delta represents a section discovering work outside its
designated scope. For each delta, decide:

1. **accept**: Create new section(s) to handle the out-of-scope work
2. **reject**: The work is not needed or can be deferred
3. **absorb**: Expand an existing section's scope to include it

Each delta also includes `requires_root_reframing`:
- `true`: this concern changes the parent framing and should not be
  treated as a routine local section split
- `false`: this can be handled as an ordinary local scope adjustment

Reply with a JSON block:

```json
{{"decisions": [
  {{"delta_id": "delta-03-proposal-oos", "action": "accept", "reason": "New section needed for auth module", "new_sections": [{{"title": "Authentication Middleware", "scope": "Authentication middleware setup and integration"}}]}},
  {{"delta_id": "delta-05-scan-deep", "action": "reject", "reason": "Optimization can be deferred to next round"}},
  {{"delta_id": "delta-07-candidate-a1b2c3d4", "action": "absorb", "reason": "Small addition fits existing scope", "absorb_into_section": "02", "scope_addition": "Include config validation"}}
]}}
```

**Required fields by action:**
- ALL: `delta_id`, `action`, `reason`
- accept: `new_sections` (array of `{{title, scope}}`)
- absorb: `absorb_into_section`, `scope_addition`
"""
    if not write_validated_prompt(prompt_text, adjudication_prompt):
        raise ScopeDeltaAggregationExit

    return adjudication_prompt, adjudication_output


def _dispatch_adjudication(
    planspace: Path,
    parent: str,
    policy: dict[str, str],
    adjudication_prompt: Path,
    adjudication_output: Path,
) -> dict | None:
    _log_artifact(planspace, "prompt:scope-delta-adjudication")

    adjudication_result = dispatch_agent(
        resolve(policy, "coordination_plan"),
        adjudication_prompt,
        adjudication_output,
        planspace,
        parent,
        agent_file=agent_for("coordination.plan"),
    )
    if adjudication_result == "ALIGNMENT_CHANGED_PENDING":
        raise ScopeDeltaAggregationExit

    adj_data = parse_scope_delta_adjudication(adjudication_result)
    if adj_data is not None:
        return adj_data

    log("  coordinator: scope-delta adjudication parse "
        "failed — retrying with escalation model")
    retry_prompt = adjudication_prompt.with_name("scope-delta-prompt-retry.md")
    retry_prompt.write_text(
        adjudication_prompt.read_text(encoding="utf-8")
        + "\n\nOutput ONLY the JSON object, no prose.\n",
        encoding="utf-8",
    )
    retry_output = adjudication_output.with_name("scope-delta-output-retry.md")
    retry_result = dispatch_agent(
        resolve(policy, "escalation_model"),
        retry_prompt,
        retry_output,
        planspace,
        parent,
        agent_file=agent_for("coordination.plan"),
    )
    if retry_result == "ALIGNMENT_CHANGED_PENDING":
        raise ScopeDeltaAggregationExit

    return parse_scope_delta_adjudication(retry_result)


def _build_delta_id_map(delta_files: list[Path]) -> dict[str, Path]:
    delta_id_to_path: dict[str, Path] = {}
    for delta_file in delta_files:
        delta = read_json(delta_file)
        if isinstance(delta, dict):
            delta_id = delta.get("delta_id")
            if delta_id:
                delta_id_to_path[str(delta_id)] = delta_file
    return delta_id_to_path


def _apply_adjudication(
    decision: dict,
    *,
    scope_deltas_dir: Path,
    delta_id_to_path: dict[str, Path],
) -> None:
    delta_id = str(decision.get("delta_id", ""))
    action = decision.get("action", "")

    if delta_id and delta_id in delta_id_to_path:
        delta_path = delta_id_to_path[delta_id]
    else:
        section = normalize_section_id(str(decision.get("section", "")), scope_deltas_dir)
        delta_path = scope_deltas_dir / f"section-{section}-scope-delta.json"

    if delta_path.exists():
        delta = read_json(delta_path)
        if delta is None:
            log(
                f"  coordinator: WARNING — malformed scope-delta "
                f"{delta_path.name} during adjudication application, "
                "preserving as .malformed.json",
            )
            malformed = delta_path.with_suffix(".malformed.json")
            rename_malformed(delta_path)
            write_json(
                delta_path,
                {
                    "delta_id": delta_id,
                    "section": decision.get("section", ""),
                    "origin": "unknown",
                    "adjudicated": True,
                    "adjudication": decision,
                    "error": (
                        "original scope-delta malformed during "
                        "adjudication application"
                    ),
                    "preserved_path": str(malformed),
                },
            )
            log(f"  coordinator: scope delta {delta_id or delta_path.name} → {action}")
            return

        delta["adjudicated"] = True
        delta["adjudication"] = decision
        write_json(delta_path, delta)

    log(f"  coordinator: scope delta {delta_id or delta_path.name} → {action}")


def _record_decisions(
    planspace: Path,
    parent: str,
    decisions: list[dict],
    *,
    scope_deltas_dir: Path,
) -> None:
    paths = PathRegistry(planspace)
    decisions_rollup_path = paths.coordination_dir() / "scope-delta-decisions.json"
    write_json(decisions_rollup_path, {"decisions": decisions})
    _log_artifact(planspace, "coordination:scope-delta-decisions")

    decisions_dir = paths.decisions_dir()
    for decision in decisions:
        delta_id = str(decision.get("delta_id", ""))
        section = normalize_section_id(str(decision.get("section", "")), scope_deltas_dir)
        action = decision.get("action", "")
        reason = decision.get("reason", "")
        label = delta_id or section
        mailbox_send(
            planspace,
            parent,
            f"summary:scope-delta:{label}:{action}:{reason[:150]}",
        )

        existing = load_decisions(decisions_dir, section=section)
        next_num = len(existing) + 1
        record_decision(
            decisions_dir,
            Decision(
                id=f"d-{delta_id or section}-{next_num:03d}",
                scope="section",
                section=section,
                problem_id=None,
                parent_problem_id=None,
                concern_scope="scope-delta",
                proposal_summary=f"{action}: {reason}",
                alignment_to_parent=None,
                status="decided",
            ),
        )


def aggregate_scope_deltas(
    planspace: Path,
    parent: str,
    policy: dict[str, str],
) -> list[dict]:
    """Adjudicate any pending scope deltas and return the decisions."""
    paths = PathRegistry(planspace)
    scope_deltas_dir = paths.scope_deltas_dir()
    if not scope_deltas_dir.exists():
        return []

    delta_files, pending_deltas = _load_pending_deltas(scope_deltas_dir)
    if not pending_deltas:
        return []

    log(
        f"  coordinator: {len(pending_deltas)} pending scope "
        f"deltas — dispatching adjudicator",
    )
    adjudication_prompt, adjudication_output = _write_adjudication_prompt(
        paths.coordination_dir(),
        pending_deltas,
    )
    adj_data = _dispatch_adjudication(
        planspace,
        parent,
        policy,
        adjudication_prompt,
        adjudication_output,
    )
    if adj_data is None:
        log("  coordinator: scope-delta adjudication parse "
            "failed after retry — fail closed")
        write_json(
            paths.coordination_dir() / "scope-delta-adjudication-failure.json",
            {
                "error": "unparseable_adjudication_json",
                "prompt_path": str(adjudication_prompt),
                "output_path": str(adjudication_output),
                "attempts": 2,
            },
        )
        mailbox_send(
            planspace,
            parent,
            "fail:coordination:unparseable_scope_delta_adjudication",
        )
        raise ScopeDeltaAggregationExit

    delta_id_to_path = _build_delta_id_map(delta_files)
    decisions = list(adj_data.get("decisions", []))
    for decision in decisions:
        _apply_adjudication(
            decision,
            scope_deltas_dir=scope_deltas_dir,
            delta_id_to_path=delta_id_to_path,
        )

    _record_decisions(
        planspace,
        parent,
        decisions,
        scope_deltas_dir=scope_deltas_dir,
    )
    return decisions
