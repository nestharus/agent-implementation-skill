from __future__ import annotations

import re
from pathlib import Path

from lib.services.alignment_change_tracker import check_pending as alignment_changed_pending
from lib.core.artifact_io import read_json, write_json
from section_loop.alignment import _extract_problems, collect_modified_files
from section_loop.change_detection import diff_files, snapshot_files
from section_loop.communication import _record_traceability, log, mailbox_send
from section_loop.cross_section import persist_decision
from section_loop.dispatch import check_agent_signals, dispatch_agent, summarize_output
from section_loop.pipeline_control import handle_pending_messages, pause_for_parent
from section_loop.prompts import write_impl_alignment_prompt, write_strategic_impl_prompt
from section_loop.task_ingestion import ingest_and_submit
from section_loop.section_engine.traceability import _write_traceability_index


def run_implementation_loop(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    cycle_budget: dict,
) -> list[str] | None:
    """Run strategic implementation until aligned, then return changed files."""
    artifacts = planspace / "artifacts"
    cycle_budget_path = artifacts / "signals" / f"section-{section.number}-cycle-budget.json"

    all_known_paths = list(section.related_files)
    pre_hashes = snapshot_files(codespace, all_known_paths)

    impl_problems: str | None = None
    impl_attempt = 0

    while True:
        if handle_pending_messages(planspace, [], set()):
            mailbox_send(planspace, parent, f"fail:{section.number}:aborted")
            return None

        if alignment_changed_pending(planspace):
            log(
                f"Section {section.number}: alignment changed — "
                "aborting section to restart Phase 1"
            )
            return None

        impl_attempt += 1

        if impl_attempt > cycle_budget["implementation_max"]:
            log(
                f"Section {section.number}: implementation cycle budget "
                f"exhausted ({cycle_budget['implementation_max']} attempts)"
            )
            budget_signal = {
                "section": section.number,
                "loop": "implementation",
                "attempts": impl_attempt - 1,
                "budget": cycle_budget["implementation_max"],
                "escalate": True,
            }
            budget_signal_path = (
                artifacts / "signals" / f"section-{section.number}-impl-budget-exhausted.json"
            )
            write_json(budget_signal_path, budget_signal)
            mailbox_send(
                planspace,
                parent,
                f"budget-exhausted:{section.number}:implementation:{impl_attempt - 1}",
            )
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:budget_exhausted:{section.number}:implementation loop exceeded "
                f"{cycle_budget['implementation_max']} attempts",
            )
            if not response.startswith("resume"):
                return None
            reloaded = read_json(cycle_budget_path)
            if reloaded is not None:
                cycle_budget.update(reloaded)

        tag = "fix " if impl_problems else ""
        log(
            f"Section {section.number}: {tag}strategic implementation "
            f"(attempt {impl_attempt})"
        )

        impl_prompt = write_strategic_impl_prompt(
            section,
            planspace,
            codespace,
            impl_problems,
            model_policy=policy,
        )
        if impl_prompt is None:
            log(
                f"Section {section.number}: strategic impl prompt "
                f"blocked by template safety — skipping dispatch"
            )
            return None
        impl_output = artifacts / f"impl-{section.number}-output.md"
        impl_agent = f"impl-{section.number}"
        impl_result = dispatch_agent(
            policy.get("implementation", "gpt-5.4-high"),
            impl_prompt,
            impl_output,
            planspace,
            parent,
            impl_agent,
            codespace=codespace,
            section_number=section.number,
            agent_file="implementation-strategist.md",
        )
        if impl_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(
            planspace,
            parent,
            f"summary:impl:{section.number}:{summarize_output(impl_result)}",
        )

        if impl_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: implementation agent timed out")
            mailbox_send(
                planspace,
                parent,
                f"fail:{section.number}:implementation agent timed out",
            )
            return None

        ingest_and_submit(
            planspace,
            db_path=planspace / "run.db",
            submitted_by=f"implementation-{section.number}",
            signal_path=artifacts / "signals" / f"task-requests-impl-{section.number}.json",
            origin_refs=[str(artifacts / f"impl-{section.number}-output.md")],
        )

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            impl_result,
            signal_path=signal_dir / f"impl-{section.number}-signal.json",
            output_path=artifacts / f"impl-{section.number}-output.md",
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )
        if signal:
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        log(f"Section {section.number}: implementation alignment check")
        impl_align_prompt = write_impl_alignment_prompt(
            section,
            planspace,
            codespace,
        )
        impl_align_output = artifacts / f"impl-align-{section.number}-output.md"
        impl_align_result = dispatch_agent(
            policy["alignment"],
            impl_align_prompt,
            impl_align_output,
            planspace,
            parent,
            codespace=codespace,
            section_number=section.number,
            agent_file="alignment-judge.md",
        )
        if impl_align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        if impl_align_result.startswith("TIMEOUT:"):
            log(
                f"Section {section.number}: implementation alignment check "
                f"timed out — retrying"
            )
            impl_problems = "Previous alignment check timed out."
            continue

        problems = _extract_problems(
            impl_align_result,
            output_path=impl_align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            adjudicator_model=policy.get("adjudicator", "glm"),
        )

        signal, detail = check_agent_signals(
            impl_align_result,
            signal_path=signal_dir / f"impl-align-{section.number}-signal.json",
            output_path=impl_align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )
        if signal == "underspec":
            response = pause_for_parent(
                planspace,
                parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if problems is None:
            log(f"Section {section.number}: implementation ALIGNED")
            mailbox_send(
                planspace,
                parent,
                f"summary:impl-align:{section.number}:ALIGNED",
            )
            break

        impl_problems = problems
        short = problems[:200]
        log(
            f"Section {section.number}: implementation problems "
            f"(attempt {impl_attempt}): {short}"
        )
        mailbox_send(
            planspace,
            parent,
            f"summary:impl-align:{section.number}:PROBLEMS-attempt-{impl_attempt}:{short}",
        )

    reported = collect_modified_files(planspace, section, codespace)
    snapshotted_set = set(section.related_files)
    snapshotted_candidates = sorted(
        snapshotted_set | (set(reported) & set(pre_hashes))
    )
    verified_changed = diff_files(codespace, pre_hashes, snapshotted_candidates)
    unsnapshotted_reported = [
        relative_path
        for relative_path in reported
        if relative_path not in pre_hashes and (codespace / relative_path).exists()
    ]
    if unsnapshotted_reported:
        log(
            f"Section {section.number}: {len(unsnapshotted_reported)} "
            f"reported files were outside the pre-snapshot set (trusted)"
        )
    actually_changed = sorted(set(verified_changed) | set(unsnapshotted_reported))
    if len(reported) != len(actually_changed):
        log(
            f"Section {section.number}: {len(reported)} reported, "
            f"{len(actually_changed)} actually changed (detected via diff)"
        )

    for changed_file in actually_changed:
        _record_traceability(
            planspace,
            section.number,
            changed_file,
            f"section-{section.number}-integration-proposal.md",
            "implementation change",
        )

    _write_traceability_index(planspace, section, codespace, actually_changed)

    trace_map_dir = artifacts / "trace-map"
    trace_map_dir.mkdir(parents=True, exist_ok=True)
    trace_map_path = trace_map_dir / f"section-{section.number}.json"
    trace_map = {
        "section": section.number,
        "problems": [],
        "strategies": [],
        "todo_ids": [],
        "files": list(actually_changed),
    }
    problem_frame_path = artifacts / "sections" / f"section-{section.number}-problem-frame.md"
    if problem_frame_path.exists():
        problem_frame_text = problem_frame_path.read_text(encoding="utf-8")
        for line in problem_frame_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                trace_map["problems"].append(stripped[2:])
    for relative_path in section.related_files:
        full_path = codespace / relative_path
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
            for match in re.finditer(r"TODO\[([^\]]+)\]", content):
                trace_map["todo_ids"].append(
                    {"id": match.group(1), "file": relative_path}
                )
        except (OSError, UnicodeDecodeError):
            continue
    write_json(trace_map_path, trace_map)
    log(f"Section {section.number}: trace-map written to {trace_map_path}")

    return actually_changed
