import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..alignment import _extract_problems, _run_alignment_check_with_retries
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
)
from ..pipeline_control import poll_control_messages
from ..types import Section, SectionResult

from .execution import _dispatch_fix_group, write_coordinator_fix_prompt
from .planning import _parse_coordination_plan, write_coordination_plan_prompt
from .problems import (
    _collect_outstanding_problems,
    _detect_recurrence_patterns,
    build_file_to_sections,
)

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
    coord_dir = planspace / "artifacts" / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)

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
            "gpt-5.3-codex-xhigh", encoding="utf-8")
        log(f"  coordinator: recurrence escalation — setting model to "
            f"gpt-5.3-codex-xhigh for "
            f"{recurrence['recurring_problem_count']} recurring problems "
            f"across sections {recurrence['recurring_sections']}")

    # Save coordination state for debugging / inspection
    state_path = coord_dir / "problems.json"
    state_path.write_text(json.dumps(problems, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:problems")

    # -----------------------------------------------------------------
    # Step 1b: Aggregate scope deltas for coordinator adjudication
    # -----------------------------------------------------------------
    scope_deltas_dir = planspace / "artifacts" / "scope-deltas"
    if scope_deltas_dir.exists():
        delta_files = sorted(scope_deltas_dir.glob("section-*-scope-delta.json"))
        if delta_files:
            pending_deltas = []
            for df in delta_files:
                try:
                    delta = json.loads(df.read_text(encoding="utf-8"))
                    # Skip already-adjudicated deltas
                    if delta.get("adjudicated"):
                        continue
                    pending_deltas.append(delta)
                except (json.JSONDecodeError, OSError):
                    continue

            if pending_deltas:
                log(f"  coordinator: {len(pending_deltas)} pending scope "
                    f"deltas — dispatching adjudicator")

                adjudication_prompt = coord_dir / "scope-delta-prompt.md"
                adjudication_output = coord_dir / "scope-delta-output.md"

                # Write deltas to artifact file (avoid inline embedding)
                pending_deltas_path = coord_dir / "scope-deltas-pending.json"
                pending_deltas_path.write_text(
                    json.dumps(pending_deltas, indent=2), encoding="utf-8")

                adjudication_prompt.write_text(f"""# Task: Adjudicate Scope Deltas

## Pending Scope Deltas

Read the pending scope deltas from: `{pending_deltas_path}`

## Instructions

Each scope delta represents a section discovering work outside its
designated scope. For each delta, decide:

1. **accept**: Create new section(s) to handle the out-of-scope work
2. **reject**: The work is not needed or can be deferred
3. **absorb**: Expand an existing section's scope to include it

Reply with a JSON block:

```json
{{"decisions": [
  {{"section": "03", "action": "accept", "reason": "New section needed for auth module", "new_sections": [{{"title": "Authentication Middleware", "scope": "Authentication middleware setup and integration"}}]}},
  {{"section": "05", "action": "reject", "reason": "Optimization can be deferred to next round"}},
  {{"section": "07", "action": "absorb", "reason": "Small addition fits existing scope", "absorb_into_section": "02", "scope_addition": "Include config validation"}}
]}}
```

**Required fields by action:**
- ALL: `section`, `action`, `reason`
- accept: `new_sections` (array of `{{title, scope}}`)
- absorb: `absorb_into_section`, `scope_addition`
""", encoding="utf-8")
                _log_artifact(planspace, "prompt:scope-delta-adjudication")

                adjudication_result = dispatch_agent(
                    "claude-opus", adjudication_prompt,
                    adjudication_output,
                    planspace, parent,
                    agent_file="coordination-planner.md",
                )
                if adjudication_result == "ALIGNMENT_CHANGED_PENDING":
                    return False

                # Parse adjudication decisions and mark deltas as handled
                try:
                    adj_json = None
                    for line in adjudication_result.split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("{") and "decisions" in stripped:
                            adj_json = stripped
                            break
                    if adj_json is None:
                        start = adjudication_result.find("{")
                        end = adjudication_result.rfind("}")
                        if start >= 0 and end > start:
                            candidate = adjudication_result[start:end + 1]
                            if "decisions" in candidate:
                                adj_json = candidate
                    if adj_json:
                        adj_data = json.loads(adj_json)
                        all_decisions = adj_data.get("decisions", [])
                        for decision in all_decisions:
                            sec = decision.get("section", "")
                            action = decision.get("action", "")
                            # Mark delta as adjudicated — preserve the
                            # ENTIRE decision object (including
                            # new_sections, absorb_into_section,
                            # scope_addition, and any extra fields the
                            # agent provides).
                            delta_path = (scope_deltas_dir
                                          / f"section-{sec}-scope-delta.json")
                            if delta_path.exists():
                                delta = json.loads(
                                    delta_path.read_text(encoding="utf-8"))
                                delta["adjudicated"] = True
                                delta["adjudication"] = decision
                                delta_path.write_text(
                                    json.dumps(delta, indent=2),
                                    encoding="utf-8",
                                )
                            log(f"  coordinator: scope delta for section "
                                f"{sec} → {action}")

                        # Write a rollup artifact of all adjudicated
                        # scope-delta decisions for parent visibility.
                        decisions_rollup_path = (
                            coord_dir
                            / "scope-delta-decisions.json"
                        )
                        decisions_rollup_path.write_text(
                            json.dumps(
                                {"decisions": all_decisions}, indent=2,
                            ),
                            encoding="utf-8",
                        )
                        _log_artifact(
                            planspace,
                            "coordination:scope-delta-decisions",
                        )

                        # Notify parent of each adjudicated delta
                        for decision in all_decisions:
                            sec = decision.get("section", "")
                            action = decision.get("action", "")
                            reason = decision.get(
                                "reason", "")[:150]
                            mailbox_send(
                                planspace, parent,
                                f"summary:scope-delta:{sec}:"
                                f"{action}:{reason}",
                            )
                except (json.JSONDecodeError, KeyError, TypeError):
                    log("  coordinator: could not parse scope delta "
                        "adjudication — deltas remain pending")

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
        "claude-opus", plan_prompt, plan_output,
        planspace, parent, agent_file="coordination-planner.md",
    )
    if plan_result == "ALIGNMENT_CHANGED_PENDING":
        return False

    # Parse the JSON coordination plan from agent output
    coord_plan = _parse_coordination_plan(plan_result, problems)
    if coord_plan is None:
        # Fallback: treat each problem as its own group, sequential
        log("  coordinator: WARNING — could not parse coordination plan, "
            "falling back to one-problem-per-group")
        coord_plan = {
            "groups": [
                {"problems": [i], "reason": "fallback", "strategy": "parallel"}
                for i in range(len(problems))
            ],
        }

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
    plan_path.write_text(json.dumps(coord_plan, indent=2), encoding="utf-8")
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
    groups_path.write_text(json.dumps(groups_data, indent=2), encoding="utf-8")
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
                    planspace / "artifacts" / "contracts"
                    / f"contract-delta-group-{gidx}.md"
                )
                notes_dir = planspace / "artifacts" / "notes"
                notes_dir.mkdir(parents=True, exist_ok=True)
                sec_dir = planspace / "artifacts" / "sections"

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
                proposals_dir = planspace / "artifacts" / "proposals"
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
                dispatch_agent(
                    "gpt-5.3-codex-xhigh", bridge_prompt,
                    bridge_output, planspace, parent,
                    codespace=codespace,
                    agent_file="bridge-agent.md",
                )

                # P9-E: Fail-closed on missing contract delta.
                # If bridge didn't write the delta, retry once.
                # If still missing, emit NEEDS_PARENT blocker.
                contracts_dir = planspace / "artifacts" / "contracts"
                contracts_dir.mkdir(parents=True, exist_ok=True)
                if not contract_delta_path.exists():
                    log(f"  coordinator: bridge didn't write contract "
                        f"delta — retrying (group {gidx})")
                    dispatch_agent(
                        "gpt-5.3-codex-xhigh", bridge_prompt,
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
                        planspace / "artifacts" / "signals"
                        / f"blocker-bridge-{gidx}.json"
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
                        if "**Note ID**" not in note_text:
                            fp = hashlib.sha256(
                                delta_bytes + s.encode("utf-8")
                            ).hexdigest()[:12]
                            note_id = f"bridge-{gidx}-to-{s}-{fp}"
                            note_path.write_text(
                                f"**Note ID** {note_id}\n\n{note_text}",
                                encoding="utf-8",
                            )

                # Register the contract delta as an input artifact for
                # downstream sections in this group
                for s_num in group_sections:
                    input_ref_dir = (
                        planspace / "artifacts" / "inputs"
                        / f"section-{s_num}"
                    )
                    input_ref_dir.mkdir(parents=True, exist_ok=True)
                    ref_path = (
                        input_ref_dir
                        / f"contract-delta-group-{gidx}.ref"
                    )
                    ref_path.write_text(
                        str(contract_delta_path), encoding="utf-8")

                log(f"  coordinator: bridge complete for group {gidx}, "
                    f"contract delta at {contract_delta_path}")

        if len(batch) == 1:
            gidx = batch[0]
            _, modified = _dispatch_fix_group(
                confirmed_groups[gidx], gidx,
                planspace, codespace, parent,
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

        # Compute inputs hash for this section
        sec_artifacts = planspace / "artifacts"
        hash_sources = [
            sec_artifacts / "sections"
            / f"section-{sec_num}-alignment-excerpt.md",
            sec_artifacts / "proposals"
            / f"section-{sec_num}-integration-proposal.md",
        ]
        hasher = hashlib.sha256()
        for hp in hash_sources:
            if hp.exists():
                hasher.update(hp.read_bytes())
        # Include incoming notes hash
        notes_dir = planspace / "artifacts" / "notes"
        if notes_dir.exists():
            for note_path in sorted(notes_dir.glob(f"from-*-to-{sec_num}.md")):
                hasher.update(note_path.read_bytes())
        # Include modified files hash (coordinator may have changed files)
        for mod_f in sorted(all_modified):
            mod_path = codespace / mod_f
            if mod_path.exists():
                hasher.update(mod_path.read_bytes())
        current_hash = hasher.hexdigest()

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
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return False  # Let outer loop restart Phase 1
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

        coord_align_output = (planspace / "artifacts"
                              / f"coord-align-{sec_num}-output.md")
        align_problems = _extract_problems(
            align_result, output_path=coord_align_output,
            planspace=planspace, parent=parent, codespace=codespace,
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
                        f"(gpt-5.3-codex-xhigh). Section is now ALIGNED.\n\n"
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
        log("  coordinator: all sections now ALIGNED")
        return True

    log(f"  coordinator: {len(remaining)} sections still not aligned")
    return False
