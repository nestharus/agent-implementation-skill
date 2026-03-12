"""Coordination-plan execution helpers."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from signals.repository.artifact_io import read_json, write_json
from containers import Services
from orchestrator.path_registry import PathRegistry
from coordination.prompt.writers import write_bridge_prompt, write_fix_prompt
from flow.service.section_ingestion import ingest_and_submit
from orchestrator.types import Section


class CoordinationExecutionExit(Exception):
    """Raised when coordination execution must stop early."""


def _build_execution_batches(
    coord_plan: dict[str, Any],
    confirmed_groups: list[list[dict[str, Any]]],
) -> list[list[int]]:
    group_file_sets = [
        set(file_path for problem in group for file_path in problem.get("files", []))
        for group in confirmed_groups
    ]

    if "batches" in coord_plan:
        agent_batches = coord_plan["batches"]
        batches: list[list[int]] = []
        for agent_batch in agent_batches:
            for group_index in agent_batch:
                files = group_file_sets[group_index]
                if not files:
                    batches.append([group_index])
                    continue
                placed = False
                for batch in batches:
                    if any(batch_index not in agent_batch for batch_index in batch):
                        continue
                    batch_files = set()
                    for batch_index in batch:
                        batch_files |= group_file_sets[batch_index]
                    if not batch_files or not (files & batch_files):
                        batch.append(group_index)
                        placed = True
                        break
                if not placed:
                    batches.append([group_index])
        Services.logger().log(
            f"  coordinator: using agent-specified batch ordering "
            f"({len(agent_batches)} agent batches → "
            f"{len(batches)} execution batches with file-safety)",
        )
        return batches

    batches = []
    for group_index, files in enumerate(group_file_sets):
        if not files:
            batches.append([group_index])
            continue
        placed = False
        for batch in batches:
            batch_files = set()
            for batch_index in batch:
                batch_files |= group_file_sets[batch_index]
            if batch_files and not (files & batch_files):
                batch.append(group_index)
                placed = True
                break
        if not placed:
            batches.append([group_index])
    return batches


def _write_overlap_stats(
    coord_dir: Path,
    group_index: int,
    group: list[dict[str, Any]],
) -> None:
    group_section_nums = sorted({problem["section"] for problem in group})
    if len(group_section_nums) < 2:
        return

    section_file_sets: dict[str, set[str]] = {}
    for problem in group:
        section_file_sets.setdefault(problem["section"], set()).update(
            problem.get("files", []),
        )

    section_numbers = list(section_file_sets.keys())
    overlap_count = 0
    for idx in range(len(section_numbers)):
        for jdx in range(idx + 1, len(section_numbers)):
            overlap_count += len(
                section_file_sets[section_numbers[idx]]
                & section_file_sets[section_numbers[jdx]],
            )

    if overlap_count <= 0:
        return

    Services.logger().log(
        f"  coordinator: group {group_index} has "
        f"{overlap_count} overlapping files across "
        f"sections — bridge decision deferred to "
        f"coordination planner",
    )
    overlap_signal = {
        "group": group_index,
        "sections": group_section_nums,
        "overlap_count": overlap_count,
        "overlapping_files": sorted(
            file_path
            for file_set in section_file_sets.values()
            for file_path in file_set
            if sum(1 for candidate in section_file_sets.values() if file_path in candidate) > 1
        ),
    }
    (coord_dir / f"overlap-stats-group-{group_index}.json").write_text(
        json.dumps(overlap_signal, indent=2),
        encoding="utf-8",
    )


def _inject_bridge_note_ids(
    notes_dir: Path,
    group_index: int,
    group_sections: list[str],
    contract_delta_path: Path,
) -> None:
    delta_bytes = contract_delta_path.read_bytes()
    for section_num in group_sections:
        note_path = notes_dir / f"from-bridge-{group_index}-to-{section_num}.md"
        if not note_path.exists():
            continue
        note_text = note_path.read_text(encoding="utf-8")
        if "**Note ID**:" in note_text:
            continue
        fingerprint = Services.hasher().content_hash(delta_bytes + section_num.encode("utf-8"))[:12]
        note_path.write_text(
            f"**Note ID**: `bridge-{group_index}-to-{section_num}-{fingerprint}`\n\n"
            f"{note_text}",
            encoding="utf-8",
        )


def _run_bridge_for_group(
    *,
    group_index: int,
    group: list[dict[str, Any]],
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict[str, str],
    bridge_reason: str,
) -> None:
    paths = PathRegistry(planspace)
    coord_dir = paths.coordination_dir()
    group_sections = sorted({problem["section"] for problem in group})
    contract_delta_path = paths.contracts_dir() / f"contract-delta-group-{group_index}.md"
    notes_dir = paths.notes_dir()
    bridge_output = coord_dir / f"bridge-{group_index}-output.md"

    bridge_prompt = write_bridge_prompt(
        group, group_index, group_sections,
        planspace, codespace, bridge_reason,
    )
    if bridge_prompt is None:
        return

    Services.logger().log(
        f"  coordinator: dispatching bridge agent for group "
        f"{group_index} ({group_sections}) — reason: {bridge_reason}",
    )

    bridge_model = Services.policies().resolve(policy, "coordination_bridge")
    Services.dispatcher().dispatch(
        bridge_model,
        bridge_prompt,
        bridge_output,
        planspace,
        parent,
        codespace=codespace,
        agent_file=Services.task_router().agent_for("coordination.bridge"),
    )

    paths.contracts_dir().mkdir(parents=True, exist_ok=True)
    if not contract_delta_path.exists():
        Services.logger().log(
            f"  coordinator: bridge didn't write contract "
            f"delta — retrying (group {group_index})",
        )
        Services.dispatcher().dispatch(
            bridge_model,
            bridge_prompt,
            bridge_output,
            planspace,
            parent,
            codespace=codespace,
            agent_file=Services.task_router().agent_for("coordination.bridge"),
        )
    if not contract_delta_path.exists():
        Services.logger().log(
            f"  coordinator: bridge failed to write contract "
            f"delta after retry — pausing for parent "
            f"(group {group_index})",
        )
        blocker_signal = {
            "state": "needs_parent",
            "why_blocked": (
                f"Bridge agent for group {group_index} failed to "
                f"produce contract delta after retry. "
                f"Sections: {group_sections}. "
                f"Reason: {bridge_reason}"
            ),
        }
        blocker_path = paths.signals_dir() / f"blocker-bridge-{group_index}.json"
        blocker_path.parent.mkdir(parents=True, exist_ok=True)
        blocker_path.write_text(json.dumps(blocker_signal, indent=2), encoding="utf-8")
        Services.communicator().mailbox_send(
            planspace,
            f"pause:needs_parent:bridge-{group_index}:contract delta missing after retry",
            "coordinator",
        )
        return

    _inject_bridge_note_ids(notes_dir, group_index, group_sections, contract_delta_path)
    for section_num in group_sections:
        input_ref_dir = paths.input_refs_dir(section_num)
        input_ref_dir.mkdir(parents=True, exist_ok=True)
        (input_ref_dir / f"contract-delta-group-{group_index}.ref").write_text(
            str(contract_delta_path),
            encoding="utf-8",
        )
    Services.logger().log(
        f"  coordinator: bridge complete for group {group_index}, "
        f"contract delta at {contract_delta_path}",
    )


def _dispatch_fix_group(
    group: list[dict[str, Any]], group_id: int,
    planspace: Path, codespace: Path, parent: str,
    default_fix_model: str = "",
) -> tuple[int, list[str] | None]:
    """Dispatch an agent to fix a single problem group.

    Returns (group_id, list_of_modified_files) on success.
    Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.
    """
    paths = PathRegistry(planspace)
    coord_dir = paths.coordination_dir()
    policy = Services.policies().load(planspace)
    fix_prompt = write_fix_prompt(group, planspace, codespace, group_id)
    if fix_prompt is None:
        Services.logger().log(f"  coordinator: fix group {group_id} prompt blocked "
            f"by template safety — skipping dispatch")
        return group_id, None
    fix_output = coord_dir / f"fix-{group_id}-output.md"
    modified_report = coord_dir / f"fix-{group_id}-modified.txt"

    if not default_fix_model:
        default_fix_model = Services.policies().resolve(policy, "coordination_fix")
    fix_model = default_fix_model
    coord_escalated_from = None
    escalation_file = coord_dir / "model-escalation.txt"
    if escalation_file.exists():
        coord_escalated_from = fix_model
        fix_model = escalation_file.read_text(encoding="utf-8").strip()
        Services.logger().log(f"  coordinator: using escalated model {fix_model}")

    Services.dispatch_helpers().write_model_choice_signal(
        planspace, f"coord-{group_id}", "coordination-fix",
        fix_model,
        "escalated due to coordination churn" if coord_escalated_from
        else "default model",
        coord_escalated_from,
    )

    Services.logger().log(f"  coordinator: dispatching fix for group {group_id} "
        f"({len(group)} problems)")
    result = Services.dispatcher().dispatch(
        fix_model, fix_prompt, fix_output,
        planspace, parent, codespace=codespace,
        agent_file=Services.task_router().agent_for("coordination.fix"),
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return group_id, None

    ingest_and_submit(
        planspace,
        db_path=paths.run_db(),
        submitted_by=f"coordination-fix-{group_id}",
        signal_path=coord_dir / f"signals/task-requests-coord-{group_id}.json",
        origin_refs=[str(fix_prompt)],
    )

    return group_id, _collect_modified_files(modified_report, codespace)


def _collect_modified_files(modified_report: Path, codespace: Path) -> list[str]:
    """Parse the modified-files report, validating paths stay within codespace."""
    if not modified_report.exists():
        return []
    codespace_resolved = codespace.resolve()
    modified: list[str] = []
    for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        pp = Path(line)
        if pp.is_absolute():
            try:
                rel = pp.resolve().relative_to(codespace_resolved)
            except ValueError:
                Services.logger().log(f"  coordinator: WARNING — fix path outside "
                    f"codespace, skipping: {line}")
                continue
        else:
            full = (codespace / pp).resolve()
            try:
                rel = full.relative_to(codespace_resolved)
            except ValueError:
                Services.logger().log(f"  coordinator: WARNING — fix path escapes "
                    f"codespace, skipping: {line}")
                continue
        modified.append(str(rel))
    return modified


def _persist_modified_files(planspace: Path, modified_files: list[str]) -> None:
    write_json(
        PathRegistry(planspace).coordination_dir() / "execution-modified-files.json",
        {"files": modified_files},
    )


def read_execution_modified_files(planspace: Path) -> list[str]:
    """Read the persisted list of files modified during coordination."""
    data = read_json(
        PathRegistry(planspace).coordination_dir() / "execution-modified-files.json",
    )
    if not isinstance(data, dict):
        return []
    files = data.get("files", [])
    return [str(file_path) for file_path in files] if isinstance(files, list) else []


def execute_coordination_plan(
    plan: dict[str, Any],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict[str, str],
) -> list[str]:
    """Execute the coordination plan and return affected section numbers."""
    coord_plan = plan["coord_plan"]
    confirmed_groups = plan["confirmed_groups"]
    batches = _build_execution_batches(coord_plan, confirmed_groups)
    Services.logger().log(f"  coordinator: {len(batches)} execution batches")

    affected_sections: set[str] = {
        problem["section"]
        for group in confirmed_groups
        for problem in group
    }
    all_modified: list[str] = []
    coord_dir = PathRegistry(planspace).coordination_dir()
    coord_dir.mkdir(parents=True, exist_ok=True)

    for batch_num, batch in enumerate(batches):
        ctrl = Services.pipeline_control().poll_control_messages(planspace, parent)
        if ctrl == "alignment_changed":
            raise CoordinationExecutionExit

        for group_index in batch:
            group = confirmed_groups[group_index]
            plan_group = (
                coord_plan["groups"][group_index]
                if group_index < len(coord_plan["groups"])
                else {}
            )
            bridge_directive = plan_group.get("bridge", {})
            if not isinstance(bridge_directive, dict):
                bridge_directive = {}
            if bridge_directive.get("needed", False):
                _run_bridge_for_group(
                    group_index=group_index,
                    group=group,
                    planspace=planspace,
                    codespace=codespace,
                    parent=parent,
                    policy=policy,
                    bridge_reason=bridge_directive.get("reason", "planner-requested"),
                )
            else:
                _write_overlap_stats(coord_dir, group_index, group)

        fix_model_default = Services.policies().resolve(policy, "coordination_fix")
        if len(batch) == 1:
            group_index = batch[0]
            _, modified = _dispatch_fix_group(
                confirmed_groups[group_index],
                group_index,
                planspace,
                codespace,
                parent,
                default_fix_model=fix_model_default,
            )
            if modified is None:
                raise CoordinationExecutionExit
            all_modified.extend(modified)
            continue

        Services.logger().log(f"  coordinator: batch {batch_num} — {len(batch)} groups in parallel")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(
                    _dispatch_fix_group,
                    confirmed_groups[group_index],
                    group_index,
                    planspace,
                    codespace,
                    parent,
                    fix_model_default,
                ): group_index
                for group_index in batch
            }
            sentinel_hit = False
            for future in as_completed(futures):
                group_index = futures[future]
                try:
                    _, modified = future.result()
                    if modified is None:
                        sentinel_hit = True
                        continue
                    all_modified.extend(modified)
                    Services.logger().log(
                        f"  coordinator: group {group_index} fix "
                        f"complete ({len(modified)} files modified)",
                    )
                except Exception as exc:
                    Services.logger().log(f"  coordinator: group {group_index} fix FAILED: {exc}")
            if sentinel_hit:
                raise CoordinationExecutionExit

    Services.logger().log(f"  coordinator: fixes complete, {len(all_modified)} total files modified")

    file_to_sections: dict[str, set[str]] = {}
    for section_num, section in sections_by_num.items():
        for file_path in section.related_files:
            file_to_sections.setdefault(file_path, set()).add(section_num)
    for modified_file in all_modified:
        affected_sections.update(file_to_sections.get(modified_file, set()))

    _persist_modified_files(planspace, all_modified)
    return sorted(affected_sections)
