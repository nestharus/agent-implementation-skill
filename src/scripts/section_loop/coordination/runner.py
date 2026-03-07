import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from lib.artifact_io import read_json, rename_malformed, write_json
from lib.hash_service import content_hash
from lib.path_registry import PathRegistry
from lib.scope_delta_parser import (
    normalize_section_id,
    parse_scope_delta_adjudication,
)

from ..alignment import (
    _extract_problems,
    _parse_alignment_verdict,
    _run_alignment_check_with_retries,
)
from ..communication import (
    _log_artifact,
    log,
    mailbox_send,
)
from ..cross_section import read_incoming_notes
from ..dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_agent_signal,
    read_model_policy,
)
from ..pipeline_control import coordination_recheck_hash, poll_control_messages
from ..types import Section, SectionResult
from prompt_safety import write_validated_prompt

from .execution import _dispatch_fix_group, write_coordinator_fix_prompt
from .planning import _parse_coordination_plan, write_coordination_plan_prompt
from .problems import (
    _collect_outstanding_problems,
    _detect_recurrence_patterns,
    build_file_to_sections,
)

_parse_scope_delta_adjudication = parse_scope_delta_adjudication
_normalize_section_id = normalize_section_id


# Coordination round limits: hard cap to prevent runaway, but rounds
# continue adaptively while problem count decreases.
MAX_COORDINATION_ROUNDS = 10  # hard safety cap
MIN_COORDINATION_ROUNDS = 2   # always try at least this many


def run_global_coordination(
    sections: list[Section],
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> bool:
    """Run the global problem coordinator.

    Collects outstanding problems across all sections, groups related
    problems, dispatches coordinated fixes, and re-runs alignment on
    affected sections.

    Returns True if all sections are ALIGNED (or no problems remain).
    """
    paths = PathRegistry(planspace)
    coord_dir = paths.coordination_dir()
    coord_dir.mkdir(parents=True, exist_ok=True)
    policy = read_model_policy(planspace)

    # -----------------------------------------------------------------
    # Step 1: Collect all outstanding problems
    # -----------------------------------------------------------------
    problems = _collect_outstanding_problems(
        section_results, sections_by_num, planspace,
    )

    if not problems:
        log("  coordinator: no outstanding problems — all ALIGNED")
        return True

    log(f"  coordinator: {len(problems)} outstanding problems across "
        f"{len({p['section'] for p in problems})} sections")

    # Detect recurrence patterns for escalated handling
    recurrence = _detect_recurrence_patterns(planspace, problems)

    # If recurrence detected, escalate model for affected groups
    if recurrence:
        escalation_file = coord_dir / "model-escalation.txt"
        escalation_file.write_text(
            policy["escalation_model"], encoding="utf-8")
        log(f"  coordinator: recurrence escalation — setting model to "
            f"{policy['escalation_model']} for "
            f"{recurrence['recurring_problem_count']} recurring problems "
            f"across sections {recurrence['recurring_sections']}")

    # Save coordination state for debugging / inspection
    state_path = coord_dir / "problems.json"
    write_json(state_path, problems)
    _log_artifact(planspace, "coordination:problems")

    # -----------------------------------------------------------------
    # Step 1b: Aggregate scope deltas for coordinator adjudication
    # -----------------------------------------------------------------
    scope_deltas_dir = paths.scope_deltas_dir()
    if scope_deltas_dir.exists():
        # Pick up both per-section deltas and reconciliation-generated
        # consolidated deltas (written by Phase 1b reconciliation).
        delta_files = sorted(
            f for f in scope_deltas_dir.iterdir()
            if f.suffix == ".json" and not f.name.endswith(".malformed.json")
        )
        if delta_files:
            pending_deltas = []
            for df in delta_files:
                delta = read_json(df)
                if delta is not None:
                    # Skip already-adjudicated deltas
                    if delta.get("adjudicated"):
                        continue
                    pending_deltas.append(delta)
                else:
                    log(f"  coordinator: WARNING — malformed scope-delta "
                        f"{df.name}, preserving as "
                        f".malformed.json")
                    continue

            if pending_deltas:
                log(f"  coordinator: {len(pending_deltas)} pending scope "
                    f"deltas — dispatching adjudicator")

                adjudication_prompt = coord_dir / "scope-delta-prompt.md"
                adjudication_output = coord_dir / "scope-delta-output.md"

                # Write deltas to artifact file (avoid inline embedding)
                pending_deltas_path = coord_dir / "scope-deltas-pending.json"
                write_json(pending_deltas_path, pending_deltas)

                adj_prompt_text = f"""# Task: Adjudicate Scope Deltas

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
                if not write_validated_prompt(
                    adj_prompt_text, adjudication_prompt,
                ):
                    return False
                _log_artifact(planspace, "prompt:scope-delta-adjudication")

                adjudication_result = dispatch_agent(
                    policy["coordination_plan"], adjudication_prompt,
                    adjudication_output,
                    planspace, parent,
                    agent_file="coordination-planner.md",
                )
                if adjudication_result == "ALIGNMENT_CHANGED_PENDING":
                    return False

                # Parse adjudication decisions (robust, with retry
                # + fail-closed — mirrors coordination-plan pattern)
                adj_data = _parse_scope_delta_adjudication(
                    adjudication_result)

                if adj_data is None:
                    # Retry once with escalation model
                    log("  coordinator: scope-delta adjudication parse "
                        "failed — retrying with escalation model")
                    retry_adj_prompt = (
                        coord_dir / "scope-delta-prompt-retry.md")
                    retry_adj_prompt.write_text(
                        adjudication_prompt.read_text(encoding="utf-8")
                        + "\n\nOutput ONLY the JSON object, no prose.\n",
                        encoding="utf-8",
                    )
                    retry_adj_output = (
                        coord_dir / "scope-delta-output-retry.md")
                    retry_adj_result = dispatch_agent(
                        policy["escalation_model"], retry_adj_prompt,
                        retry_adj_output, planspace, parent,
                        agent_file="coordination-planner.md",
                    )
                    if retry_adj_result == "ALIGNMENT_CHANGED_PENDING":
                        return False
                    adj_data = _parse_scope_delta_adjudication(
                        retry_adj_result)

                if adj_data is None:
                    # Fail closed: write failure artifact + mailbox,
                    # return False (pause global convergence).
                    log("  coordinator: scope-delta adjudication parse "
                        "failed after retry — fail closed")
                    failure_path = (
                        coord_dir
                        / "scope-delta-adjudication-failure.json"
                    )
                    write_json(failure_path, {
                        "error": "unparseable_adjudication_json",
                        "prompt_path": str(adjudication_prompt),
                        "output_path": str(adjudication_output),
                        "attempts": 2,
                    })
                    mailbox_send(
                        planspace, parent,
                        "fail:coordination:"
                        "unparseable_scope_delta_adjudication",
                    )
                    return False

                # Build delta_id → file path map from pending
                # deltas so we can apply decisions to exact files.
                delta_id_to_path: dict[str, Path] = {}
                for df in delta_files:
                    d = read_json(df)
                    if isinstance(d, dict):
                        did = d.get("delta_id")
                        if did:
                            delta_id_to_path[did] = df

                all_decisions = adj_data.get("decisions", [])
                for decision in all_decisions:
                    did = decision.get("delta_id", "")
                    action = decision.get("action", "")
                    # Resolve target file: prefer delta_id map,
                    # fall back to section-based path for compat.
                    if did and did in delta_id_to_path:
                        delta_path = delta_id_to_path[did]
                    else:
                        sec = str(decision.get("section", ""))
                        sec = _normalize_section_id(
                            sec, scope_deltas_dir)
                        delta_path = (
                            scope_deltas_dir
                            / f"section-{sec}-scope-delta.json")
                    if delta_path.exists():
                        delta = read_json(delta_path)
                        if delta is None:
                            # V1/R58: Preserve corrupted delta for
                            # diagnosis — don't crash the coordinator.
                            log(f"  coordinator: WARNING — malformed "
                                f"scope-delta {delta_path.name} during "
                                "adjudication application, "
                                f"preserving as .malformed.json")
                            malformed = delta_path.with_suffix(
                                ".malformed.json")
                            rename_malformed(delta_path)
                            delta = {
                                "delta_id": did,
                                "section": decision.get("section", ""),
                                "origin": "unknown",
                                "adjudicated": True,
                                "adjudication": decision,
                                "error": (
                                    "original scope-delta malformed "
                                    "during adjudication application"
                                ),
                                "preserved_path": str(malformed),
                            }
                            write_json(delta_path, delta)
                            continue
                        delta["adjudicated"] = True
                        delta["adjudication"] = decision
                        write_json(delta_path, delta)
                    log(f"  coordinator: scope delta "
                        f"{did or delta_path.name} → {action}")

                # Write a rollup artifact of all adjudicated
                # scope-delta decisions for parent visibility.
                decisions_rollup_path = (
                    coord_dir
                    / "scope-delta-decisions.json"
                )
                write_json(decisions_rollup_path, {"decisions": all_decisions})
                _log_artifact(
                    planspace,
                    "coordination:scope-delta-decisions",
                )

                # Notify parent of each adjudicated delta
                for decision in all_decisions:
                    did = decision.get("delta_id", "")
                    sec = str(decision.get("section", ""))
                    sec = _normalize_section_id(
                        sec, scope_deltas_dir)
                    action = decision.get("action", "")
                    reason = decision.get(
                        "reason", "")[:150]
                    label = did or sec
                    mailbox_send(
                        planspace, parent,
                        f"summary:scope-delta:{label}:"
                        f"{action}:{reason}",
                    )

                # Record each scope-delta adjudication as a
                # structured Decision sidecar for machine readability.
                from lib.decision_repository import (
                    Decision,
                    load_decisions,
                    record_decision,
                )
                decisions_dir = paths.decisions_dir()
                for adj_decision in all_decisions:
                    adj_did = adj_decision.get("delta_id", "")
                    adj_sec = str(adj_decision.get("section", ""))
                    adj_sec = _normalize_section_id(
                        adj_sec, scope_deltas_dir)
                    adj_action = adj_decision.get("action", "")
                    adj_reason = adj_decision.get("reason", "")
                    existing = load_decisions(
                        decisions_dir, section=adj_sec)
                    next_num = len(existing) + 1
                    record_decision(
                        decisions_dir,
                        Decision(
                            id=f"d-{adj_did or adj_sec}-{next_num:03d}",
                            scope="section",
                            section=adj_sec,
                            problem_id=None,
                            parent_problem_id=None,
                            concern_scope="scope-delta",
                            proposal_summary=(
                                f"{adj_action}: {adj_reason}"
                            ),
                            alignment_to_parent=None,
                            status="decided",
                        ),
                    )

    # -----------------------------------------------------------------
    # Step 2: Dispatch coordination-planner agent to group problems
    # -----------------------------------------------------------------
    ctrl = poll_control_messages(planspace, parent)
    if ctrl == "alignment_changed":
        return False

    plan_prompt = write_coordination_plan_prompt(problems, planspace)
    plan_output = coord_dir / "coordination-plan-output.md"
    log("  coordinator: dispatching coordination-planner agent")
    plan_result = dispatch_agent(
        policy["coordination_plan"], plan_prompt, plan_output,
        planspace, parent, agent_file="coordination-planner.md",
    )
    if plan_result == "ALIGNMENT_CHANGED_PENDING":
        return False

    # Parse the JSON coordination plan from agent output
    coord_plan = _parse_coordination_plan(plan_result, problems)
    if coord_plan is None:
        # Retry once with escalation model — scripts must not decide
        # problem grouping (that is a strategic agent decision).
        log("  coordinator: plan parse failed — retrying with "
            "escalation model")
        plan_output_retry = coord_dir / "coordination-plan-output-retry.md"
        retry_result = dispatch_agent(
            policy["escalation_model"], plan_prompt, plan_output_retry,
            planspace, parent, agent_file="coordination-planner.md",
        )
        if retry_result == "ALIGNMENT_CHANGED_PENDING":
            return False
        coord_plan = _parse_coordination_plan(retry_result, problems)

    if coord_plan is None:
        # Fail closed: write failure artifact + mailbox, return False.
        # Scripts must not invent grouping — only the agent decides.
        log("  coordinator: plan parse failed after retry — fail closed")
        failure_path = coord_dir / "coordination-plan-failure.md"
        write_json(failure_path, {
            "reason": "unparseable_plan_json",
            "attempts": 2,
        })
        mailbox_send(
            planspace, parent,
            "fail:coordination:unparseable_plan_json",
        )
        return False

    # Build confirmed groups from the plan
    confirmed_groups: list[list[dict[str, Any]]] = []
    group_strategies: list[str] = []
    for g in coord_plan["groups"]:
        group_problems = [problems[i] for i in g["problems"]]
        confirmed_groups.append(group_problems)
        group_strategies.append(g.get("strategy", "sequential"))
        log(f"  coordinator: group {len(confirmed_groups) - 1} — "
            f"{len(group_problems)} problems, "
            f"strategy={group_strategies[-1]}, "
            f"reason={g.get('reason', '(none)')}")

    log(f"  coordinator: {len(confirmed_groups)} problem groups from "
        f"coordination plan")

    # Save plan and groups for debugging
    plan_path = coord_dir / "coordination-plan.json"
    write_json(plan_path, coord_plan)
    _log_artifact(planspace, "coordination:plan")

    groups_path = coord_dir / "groups.json"
    groups_data = []
    for i, g in enumerate(confirmed_groups):
        groups_data.append({
            "group_id": i,
            "problem_count": len(g),
            "strategy": group_strategies[i],
            "sections": sorted({p["section"] for p in g}),
            "files": sorted({f for p in g for f in p.get("files", [])}),
        })
    write_json(groups_path, groups_data)
    _log_artifact(planspace, "coordination:groups")

    # -----------------------------------------------------------------
    # Step 3: Execute the coordination plan
    # -----------------------------------------------------------------
    group_file_sets = [
        set(f for p in g for f in p.get("files", []))
        for g in confirmed_groups
    ]

    # Use agent-specified batches if present (structured ordering from
    # the coordination planner). Within each agent batch, apply file-
    # safety sub-batching to prevent concurrent modification of shared
    # files. If no agent batches, fall back to file-safety-only batching.
    if "batches" in coord_plan:
        # Agent-specified outer ordering; file-safety inner constraint
        agent_batches = coord_plan["batches"]
        batches: list[list[int]] = []
        for agent_batch in agent_batches:
            # Sub-batch by file safety within the agent's batch
            for gidx in agent_batch:
                files = group_file_sets[gidx]
                if not files:
                    batches.append([gidx])
                    continue
                placed = False
                for batch in batches:
                    # Only merge into batches from THIS agent batch
                    if any(bi not in agent_batch for bi in batch):
                        continue
                    batch_files = set()
                    for bidx in batch:
                        batch_files |= group_file_sets[bidx]
                    if not batch_files or not (files & batch_files):
                        batch.append(gidx)
                        placed = True
                        break
                if not placed:
                    batches.append([gidx])
        log(f"  coordinator: using agent-specified batch ordering "
            f"({len(agent_batches)} agent batches → "
            f"{len(batches)} execution batches with file-safety)")
    else:
        # No agent batches — pure file-safety batching
        batches = []
        for gidx, files in enumerate(group_file_sets):
            if not files:
                batches.append([gidx])
                continue
            placed = False
            for batch in batches:
                batch_files = set()
                for bidx in batch:
                    batch_files |= group_file_sets[bidx]
                if not batch_files:
                    continue
                if not (files & batch_files):
                    batch.append(gidx)
                    placed = True
                    break
            if not placed:
                batches.append([gidx])

    log(f"  coordinator: {len(batches)} execution batches")

    all_modified: list[str] = []
    for batch_num, batch in enumerate(batches):
        ctrl = poll_control_messages(planspace, parent)
        if ctrl == "alignment_changed":
            return False

        # Bridge agent: dispatch when coordination planner says so.
        # Overlap stats are computed and logged for the planner's next
        # round, but the script does NOT auto-trigger bridges.
        for gidx in batch:
            group = confirmed_groups[gidx]
            plan_group = coord_plan["groups"][gidx] if gidx < len(coord_plan["groups"]) else {}
            bridge_directive = plan_group.get("bridge", {})
            if not isinstance(bridge_directive, dict):
                bridge_directive = {}
            bridge_needed = bridge_directive.get("needed", False)
            bridge_reason = bridge_directive.get("reason", "planner-requested")

            # --- Bridge candidate detection (agent-decided) ---
            # Compute mechanical overlap stats, then let the coordination
            # planner decide (via bridge directive in the plan).
            # Script does NOT decide relatedness — only computes stats.
            if not bridge_needed:
                group_section_nums = sorted(
                    {p["section"] for p in group})
                if len(group_section_nums) >= 2:
                    section_file_sets: dict[str, set[str]] = {}
                    for p in group:
                        section_file_sets.setdefault(
                            p["section"], set()
                        ).update(p.get("files", []))
                    # Compute overlap stats for logging (not for decision)
                    nums = list(section_file_sets.keys())
                    overlap_count = 0
                    for i in range(len(nums)):
                        for j in range(i + 1, len(nums)):
                            overlap_count += len(
                                section_file_sets[nums[i]]
                                & section_file_sets[nums[j]]
                            )
                    if overlap_count > 0:
                        log(f"  coordinator: group {gidx} has "
                            f"{overlap_count} overlapping files across "
                            f"sections — bridge decision deferred to "
                            f"coordination planner")
                        # Write overlap stats for planner's next round
                        overlap_signal = {
                            "group": gidx,
                            "sections": group_section_nums,
                            "overlap_count": overlap_count,
                            "overlapping_files": sorted(
                                f for s in section_file_sets.values()
                                for f in s
                                if sum(1 for sv in section_file_sets.values()
                                       if f in sv) > 1
                            ),
                        }
                        overlap_path = (
                            coord_dir
                            / f"overlap-stats-group-{gidx}.json"
                        )
                        overlap_path.write_text(
                            json.dumps(overlap_signal, indent=2),
                            encoding="utf-8",
                        )

            if bridge_needed:
                group_sections = sorted({p["section"] for p in group})
                group_files = sorted({
                    f for p in group for f in p.get("files", [])})
                bridge_prompt = (
                    coord_dir / f"bridge-{gidx}-prompt.md")
                bridge_output = (
                    coord_dir / f"bridge-{gidx}-output.md")
                contract_path = (
                    coord_dir / f"contract-patch-{gidx}.md")
                contract_delta_path = (
                    paths.contracts_dir() / f"contract-delta-group-{gidx}.md"
                )
                notes_dir = paths.notes_dir()
                notes_dir.mkdir(parents=True, exist_ok=True)
                sec_dir = paths.sections_dir()

                # P9-D: Build full context for bridge agent —
                # include proposal excerpts, alignment excerpts,
                # and consequence notes (matching bridge-agent.md
                # Phase 1 requirements)
                sec_refs = "\n".join(
                    f"- Section {s}: `{sec_dir / f'section-{s}-proposal-excerpt.md'}`"
                    for s in group_sections
                )
                alignment_refs = "\n".join(
                    f"- Section {s}: `{sec_dir / f'section-{s}-alignment-excerpt.md'}`"
                    for s in group_sections
                )
                proposals_dir = paths.proposals_dir()
                prop_refs = "\n".join(
                    f"- `{proposals_dir / f'section-{s}-integration-proposal.md'}`"
                    for s in group_sections
                )

                # Collect consequence notes targeting affected sections
                consequence_refs = []
                for s in group_sections:
                    pattern = f"from-*-to-{s}.md"
                    for note in sorted(notes_dir.glob(pattern)):
                        consequence_refs.append(f"- `{note}`")
                consequence_block = ""
                if consequence_refs:
                    consequence_block = (
                        f"\n\n## Existing Consequence Notes\n"
                        + "\n".join(consequence_refs)
                    )

                # P9-A: Note output paths use from-bridge-* naming
                # so read_incoming_notes and _section_inputs_hash
                # consume them automatically
                note_output_refs = "\n".join(
                    f"- `{notes_dir / f'from-bridge-{gidx}-to-{s}.md'}`"
                    for s in group_sections
                )

                bridge_prompt.write_text(
                    f"# Bridge: Resolve Cross-Section Friction "
                    f"(Group {gidx})\n\n"
                    f"## Trigger Reason\n{bridge_reason}\n\n"
                    f"## Sections in Conflict\n{sec_refs}\n\n"
                    f"## Alignment Excerpts\n{alignment_refs}\n\n"
                    f"## Integration Proposals\n{prop_refs}\n\n"
                    f"## Shared Files\n"
                    + "\n".join(f"- `{f}`" for f in group_files)
                    + consequence_block
                    + f"\n\n## Output\n"
                    f"Write your contract patch to: `{contract_path}`\n"
                    f"Write a contract delta summary to: "
                    f"`{contract_delta_path}`\n"
                    f"Write per-section consequence notes to:\n"
                    + note_output_refs + "\n",
                    encoding="utf-8",
                )
                log(f"  coordinator: dispatching bridge agent for group "
                    f"{gidx} ({group_sections}) — reason: {bridge_reason}")
                bridge_model = policy.get(
                    "coordination_bridge", "gpt-5.4-xhigh")
                dispatch_agent(
                    bridge_model, bridge_prompt,
                    bridge_output, planspace, parent,
                    codespace=codespace,
                    agent_file="bridge-agent.md",
                )

                # P9-E: Fail-closed on missing contract delta.
                # If bridge didn't write the delta, retry once.
                # If still missing, emit NEEDS_PARENT blocker.
                contracts_dir = paths.contracts_dir()
                contracts_dir.mkdir(parents=True, exist_ok=True)
                if not contract_delta_path.exists():
                    log(f"  coordinator: bridge didn't write contract "
                        f"delta — retrying (group {gidx})")
                    dispatch_agent(
                        bridge_model, bridge_prompt,
                        bridge_output, planspace, parent,
                        codespace=codespace,
                        agent_file="bridge-agent.md",
                    )
                if not contract_delta_path.exists():
                    log(f"  coordinator: bridge failed to write contract "
                        f"delta after retry — pausing for parent "
                        f"(group {gidx})")
                    blocker_signal = {
                        "state": "needs_parent",
                        "why_blocked": (
                            f"Bridge agent for group {gidx} failed to "
                            f"produce contract delta after retry. "
                            f"Sections: {group_sections}. "
                            f"Reason: {bridge_reason}"
                        ),
                    }
                    blocker_path = (
                        paths.signals_dir() / f"blocker-bridge-{gidx}.json"
                    )
                    blocker_path.parent.mkdir(parents=True, exist_ok=True)
                    blocker_path.write_text(
                        json.dumps(blocker_signal, indent=2),
                        encoding="utf-8",
                    )
                    mailbox_send(
                        planspace,
                        f"pause:needs_parent:bridge-{gidx}:"
                        f"contract delta missing after retry",
                        "coordinator",
                    )
                    continue  # Skip this group, proceed with others

                # P9-B: Inject stable Note IDs into bridge notes.
                # ID is derived mechanically from contract delta
                # content + target section (stable across reruns
                # with same input).
                delta_bytes = contract_delta_path.read_bytes()
                for s in group_sections:
                    note_path = (
                        notes_dir / f"from-bridge-{gidx}-to-{s}.md"
                    )
                    if note_path.exists():
                        note_text = note_path.read_text(encoding="utf-8")
                        if "**Note ID**:" not in note_text:
                            fp = content_hash(
                                delta_bytes + s.encode("utf-8"),
                            )[:12]
                            note_id = f"bridge-{gidx}-to-{s}-{fp}"
                            note_path.write_text(
                                f"**Note ID**: `{note_id}`\n\n{note_text}",
                                encoding="utf-8",
                            )

                # Register the contract delta as an input artifact for
                # downstream sections in this group
                for s_num in group_sections:
                    input_ref_dir = paths.input_refs_dir(s_num)
                    input_ref_dir.mkdir(parents=True, exist_ok=True)
                    ref_path = (
                        input_ref_dir
                        / f"contract-delta-group-{gidx}.ref"
                    )
                    ref_path.write_text(
                        str(contract_delta_path), encoding="utf-8")

                log(f"  coordinator: bridge complete for group {gidx}, "
                    f"contract delta at {contract_delta_path}")

        fix_model_default = policy["coordination_fix"]
        if len(batch) == 1:
            gidx = batch[0]
            _, modified = _dispatch_fix_group(
                confirmed_groups[gidx], gidx,
                planspace, codespace, parent,
                default_fix_model=fix_model_default,
            )
            if modified is None:
                return False
            all_modified.extend(modified)
        else:
            log(f"  coordinator: batch {batch_num} — "
                f"{len(batch)} groups in parallel")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(
                        _dispatch_fix_group,
                        confirmed_groups[gidx], gidx,
                        planspace, codespace, parent,
                        fix_model_default,
                    ): gidx
                    for gidx in batch
                }
                sentinel_hit = False
                for future in as_completed(futures):
                    gidx = futures[future]
                    try:
                        _, modified = future.result()
                        if modified is None:
                            sentinel_hit = True
                            continue
                        all_modified.extend(modified)
                        log(f"  coordinator: group {gidx} fix "
                            f"complete ({len(modified)} files "
                            f"modified)")
                    except Exception as exc:
                        log(f"  coordinator: group {gidx} fix "
                            f"FAILED: {exc}")
            if sentinel_hit:
                return False

    log(f"  coordinator: fixes complete, "
        f"{len(all_modified)} total files modified")

    # -----------------------------------------------------------------
    # Step 4: Re-run per-section alignment on affected sections
    # -----------------------------------------------------------------
    # Determine which sections need re-checking:
    # sections that had problems + sections whose files were modified
    affected_sections: set[str] = set()

    # Sections that had problems
    for p in problems:
        affected_sections.add(p["section"])

    # Sections whose files were modified by the coordinator
    file_to_sections = build_file_to_sections(sections)
    for mod_file in all_modified:
        for sec_num in file_to_sections.get(mod_file, []):
            affected_sections.add(sec_num)

    log(f"  coordinator: re-checking alignment for sections "
        f"{sorted(affected_sections)}")

    # Incremental alignment: track per-section input hashes to skip
    # unchanged sections
    inputs_hash_dir = coord_dir / "inputs-hashes"
    inputs_hash_dir.mkdir(parents=True, exist_ok=True)

    for sec_num in sorted(affected_sections):
        section = sections_by_num.get(sec_num)
        if not section:
            continue

        # Canonical section-input hash + coordinator-modified files
        current_hash = coordination_recheck_hash(
            sec_num, planspace, codespace, sections_by_num,
            list(all_modified),
        )

        prev_hash_file = inputs_hash_dir / f"section-{sec_num}.hash"
        if prev_hash_file.exists():
            prev_hash = prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash == current_hash:
                log(f"  coordinator: section {sec_num} inputs unchanged "
                    f"— skipping alignment recheck")
                continue
        prev_hash_file.write_text(current_hash, encoding="utf-8")

        # Poll for control messages before each re-check
        ctrl = poll_control_messages(planspace, parent, sec_num)
        if ctrl == "alignment_changed":
            log("  coordinator: alignment changed — aborting re-checks")
            return False

        # Read any incoming notes for this section (cross-section context)
        notes = read_incoming_notes(section, planspace, codespace)
        if notes:
            log(f"  coordinator: section {sec_num} has incoming notes "
                f"from other sections")

        # Re-run implementation alignment check with TIMEOUT retry
        align_result = _run_alignment_check_with_retries(
            section, planspace, codespace, parent, sec_num,
            output_prefix="coord-align",
            model=policy["alignment"],
            adjudicator_model=policy.get("adjudicator", "glm"),
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return False  # Let outer loop restart Phase 1
        if align_result == "INVALID_FRAME":
            # Structural failure — alignment prompt frame is wrong.
            # Surface upward, don't continue with broken evaluation.
            log(f"  coordinator: section {sec_num} invalid alignment "
                f"frame — requires parent intervention")
            mailbox_send(
                planspace, parent,
                f"fail:invalid_alignment_frame:{sec_num}",
            )
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="invalid alignment frame — requires "
                         "parent intervention",
            )
            continue
        if align_result is None:
            # All retries timed out
            log(f"  coordinator: section {sec_num} alignment check "
                f"timed out after retries")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="alignment check timed out after retries",
            )
            continue

        coord_align_output = (
            paths.artifacts / f"coord-align-{sec_num}-output.md"
        )
        align_problems = _extract_problems(
            align_result, output_path=coord_align_output,
            planspace=planspace, parent=parent, codespace=codespace,
            adjudicator_model=policy.get("adjudicator", "glm"),
        )
        coord_signal_dir = coord_dir / "signals"
        coord_signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=(coord_signal_dir
                         / f"coord-align-{sec_num}-signal.json"),
            output_path=coord_dir / f"coord-align-{sec_num}-output.md",
            planspace=planspace, parent=parent, codespace=codespace,
        )

        if align_problems is None and signal is None:
            log(f"  coordinator: section {sec_num} now ALIGNED")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
            )

            # Record resolution if this section had a recurring problem
            if recurrence and sec_num in [
                str(s) for s in recurrence.get("recurring_sections", [])
            ]:
                # Find what the previous problem was
                prev_problem = next(
                    (p for p in problems if p["section"] == sec_num),
                    None,
                )
                if prev_problem:
                    resolution_dir = coord_dir
                    resolution_dir.mkdir(parents=True, exist_ok=True)
                    resolution_path = (
                        resolution_dir
                        / f"resolution-{sec_num}.md"
                    )
                    resolution_path.write_text(
                        f"# Resolution: Section {sec_num}\n\n"
                        f"## Recurring Problem\n\n"
                        f"{prev_problem.get('description', 'unknown')}\n\n"
                        f"## Resolution\n\n"
                        f"Resolved during coordination round via "
                        f"coordinated fix with escalated model "
                        f"({policy['escalation_model']}). Section is now ALIGNED.\n\n"
                        f"## Files Involved\n\n"
                        + "\n".join(
                            f"- `{f}`"
                            for f in prev_problem.get("files", [])
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    log(f"  coordinator: recorded resolution for "
                        f"recurring section {sec_num}")
        else:
            log(f"  coordinator: section {sec_num} still has problems")
            # Fold signal info into problems string (SectionResult has
            # no signal fields — only problems)
            combined_problems = align_problems or ""
            if signal:
                combined_problems += (
                    f"\n[signal:{signal}] {detail}" if combined_problems
                    else f"[signal:{signal}] {detail}"
                )
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems=combined_problems or None,
            )

    # Check if everything is now aligned
    remaining = [r for r in section_results.values() if not r.aligned]
    if not remaining:
        # Re-check outstanding problems (notes may have been generated
        # during coordination fixes).
        outstanding_after = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        if outstanding_after:
            outstanding_types = [p["type"] for p in outstanding_after]
            log(f"  coordinator: all sections aligned but "
                f"{len(outstanding_after)} outstanding problems "
                f"remain (types: {outstanding_types})")
            return False
        log("  coordinator: all sections now ALIGNED")
        return True

    log(f"  coordinator: {len(remaining)} sections still not aligned")
    return False
