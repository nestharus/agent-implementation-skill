"""Flow completion reconciliation helpers."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import task_db
from flow.engine.submitter import (
    submit_chain,
    submit_fanout,
)
from flow.types.schema import ChainAction, FanoutAction, parse_flow_signal
from research.engine.orchestrator import (
    load_research_status,
    validate_research_plan,
    write_research_status,
)
from research.engine.executor import (
    execute_research_plan,
    submit_research_verify,
)
from intake.service.assessment import (
    read_post_impl_assessment,
    record_assessment_governance,
)
from flow.repository.gate_operations import (
    cancel_chain_descendants,
    check_and_fire_gate as _check_and_fire_gate_impl,
    find_gate_for_chain,
    get_gate_member_leaf,
    read_origin_refs,
    update_gate_member,
    update_gate_member_leaf,
)

logger = logging.getLogger(__name__)


def build_result_manifest(
    task_id: int,
    instance_id: str,
    flow_id: str,
    chain_id: str,
    task_type: str,
    status: str,
    output_path: str | None,
    error: str | None,
) -> dict:
    """Build result manifest dict for a completed or failed task."""
    return {
        "task_id": task_id,
        "instance_id": instance_id,
        "flow_id": flow_id,
        "chain_id": chain_id,
        "task_type": task_type,
        "status": status,
        "output_path": output_path,
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_gate_aggregate_manifest(
    gate_id: str,
    flow_id: str,
    mode: str,
    failure_policy: str,
    origin_refs: list[str],
    members: list[dict],
) -> dict:
    """Build gate aggregate manifest dict."""
    return {
        "gate_id": gate_id,
        "flow_id": flow_id,
        "mode": mode,
        "failure_policy": failure_policy,
        "origin_refs": origin_refs,
        "members": members,
    }


def reconcile_task_completion(
    db_path: Path,
    planspace: Path,
    task_id: int,
    status: str,
    output_path: str | None,
    error: str | None = None,
    codespace: Path | None = None,
) -> None:
    """Called after a task completes or fails."""
    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()

    if row is None:
        logger.warning(
            "reconcile_task_completion called with unknown task_id=%d, skipping",
            task_id,
        )
        return

    task = dict(row)
    instance_id = task["instance_id"] or ""
    flow_id = task["flow_id"] or ""
    chain_id = task["chain_id"] or ""
    task_type = task["task_type"] or ""
    continuation_path = task["continuation_path"]
    result_manifest_path = task["result_manifest_path"]

    manifest = build_result_manifest(
        task_id=task_id,
        instance_id=instance_id,
        flow_id=flow_id,
        chain_id=chain_id,
        task_type=task_type,
        status=status,
        output_path=output_path,
        error=error,
    )

    if result_manifest_path:
        Services.artifact_io().write_json(planspace / result_manifest_path, manifest)

    origin_refs = read_origin_refs(planspace, task_id)
    _handle_research_completion(
        db_path,
        planspace,
        task,
        status,
        output_path,
        error,
        origin_refs,
        codespace,
    )
    _handle_post_impl_assessment_completion(task, status, planspace, codespace)

    if status == "failed":
        if chain_id:
            cancel_chain_descendants(db_path, chain_id, task_id)

        if chain_id:
            gate_id = find_gate_for_chain(db_path, chain_id)
            if gate_id:
                update_gate_member(
                    db_path,
                    gate_id,
                    chain_id,
                    "failed",
                    result_manifest_path,
                )
                check_and_fire_gate(
                    db_path,
                    planspace,
                    gate_id,
                    flow_id,
                    origin_refs,
                )
        return

    if status == "complete":
        continuation = None
        if continuation_path:
            cont_file = planspace / continuation_path
            if cont_file.exists():
                try:
                    continuation = parse_flow_signal(cont_file)
                except (ValueError, json.JSONDecodeError) as exc:
                    print(
                        f"[FLOW][WARN] Malformed continuation at {cont_file} "
                        f"({exc}) — renaming to .malformed.json",
                    )
                    Services.artifact_io().rename_malformed(cont_file)
                    if chain_id:
                        cancel_chain_descendants(db_path, chain_id, task_id)
                        gate_id = find_gate_for_chain(db_path, chain_id)
                        if gate_id:
                            update_gate_member(
                                db_path,
                                gate_id,
                                chain_id,
                                "failed",
                                result_manifest_path,
                            )
                            check_and_fire_gate(
                                db_path,
                                planspace,
                                gate_id,
                                flow_id,
                                origin_refs,
                            )
                    return

        if continuation is not None and continuation.actions:
            for action in continuation.actions:
                if isinstance(action, ChainAction) and action.steps:
                    new_ids = submit_chain(
                        db_path,
                        "reconciler",
                        action.steps,
                        flow_id=flow_id,
                        chain_id=chain_id,
                        declared_by_task_id=task_id,
                        origin_refs=origin_refs,
                        planspace=planspace,
                    )
                    if new_ids:
                        with task_db(db_path) as conn:
                            conn.execute(
                                "UPDATE tasks SET depends_on=? WHERE id=?",
                                (str(task_id), new_ids[0]),
                            )
                            conn.commit()

                        gate_id = find_gate_for_chain(db_path, chain_id)
                        if gate_id:
                            update_gate_member_leaf(
                                db_path,
                                gate_id,
                                chain_id,
                                new_ids[-1],
                            )

                elif isinstance(action, FanoutAction) and action.branches:
                    submit_fanout(
                        db_path,
                        "reconciler",
                        action.branches,
                        flow_id=flow_id,
                        declared_by_task_id=task_id,
                        origin_refs=origin_refs,
                        gate=action.gate,
                        planspace=planspace,
                    )
        else:
            if chain_id:
                gate_id = find_gate_for_chain(db_path, chain_id)
                if gate_id:
                    member_leaf = get_gate_member_leaf(db_path, gate_id, chain_id)
                    if member_leaf == task_id:
                        update_gate_member(
                            db_path,
                            gate_id,
                            chain_id,
                            "complete",
                            result_manifest_path,
                        )
                        check_and_fire_gate(
                            db_path,
                            planspace,
                            gate_id,
                            flow_id,
                            origin_refs,
                        )


def _research_section_number(task: dict) -> str | None:
    """Extract a section number from a section-scoped research task."""
    return _section_number(task)


def _section_number(task: dict) -> str | None:
    """Extract a section number from a section-scoped task."""
    concern_scope = str(task.get("concern_scope") or "")
    match = re.match(r"^section-(\d+)$", concern_scope)
    if match:
        return match.group(1)
    return None


def _handle_research_completion(
    db_path: Path,
    planspace: Path,
    task: dict,
    status: str,
    output_path: str | None,
    error: str | None,
    origin_refs: list[str],
    codespace: Path | None,
) -> None:
    """Apply script-owned research follow-on logic on task completion."""
    task_type = str(task.get("task_type") or "")
    if task_type not in {
        "research.plan",
        "research.synthesis",
        "research.verify",
    }:
        return

    section_number = _research_section_number(task)
    if section_number is None:
        return

    status_data = load_research_status(section_number, planspace) or {}
    trigger_hash = str(status_data.get("trigger_hash", ""))
    cycle_id = str(status_data.get("cycle_id", ""))

    if status == "failed":
        write_research_status(
            section_number,
            planspace,
            "failed",
            detail=error or f"{task_type} failed",
            trigger_hash=trigger_hash,
            cycle_id=cycle_id,
        )
        return

    if status != "complete":
        return

    if task_type == "research.plan":
        plan_output = Path(output_path) if output_path else PathRegistry(planspace).research_plan(section_number)
        execute_research_plan(
            section_number,
            planspace,
            codespace,
            plan_output,
        )
        return

    if task_type == "research.synthesis":
        plan = validate_research_plan(PathRegistry(planspace).research_plan(section_number))
        verify_claims = bool(
            isinstance(plan, dict)
            and isinstance(plan.get("flow"), dict)
            and plan["flow"].get("verify_claims")
        )
        if verify_claims:
            submit_research_verify(
                section_number,
                planspace,
                db_path=db_path,
                declared_by_task_id=int(task["id"]),
                origin_refs=origin_refs + ([output_path] if output_path else []),
            )
        else:
            write_research_status(
                section_number,
                planspace,
                "synthesized",
                detail="research synthesis complete",
                trigger_hash=trigger_hash,
                cycle_id=cycle_id,
            )
        return

    if task_type == "research.verify":
        write_research_status(
            section_number,
            planspace,
            "verified",
            detail="research verification complete",
            trigger_hash=trigger_hash,
            cycle_id=cycle_id,
        )


def _handle_post_impl_assessment_completion(
    task: dict,
    status: str,
    planspace: Path,
    codespace: Path | None,
) -> None:
    """Apply post-implementation assessment results on task completion."""
    del codespace

    task_type = str(task.get("task_type") or "")
    if task_type != "implementation.post_assessment" or status != "complete":
        return

    section_number = _section_number(task)
    if section_number is None:
        return

    assessment = read_post_impl_assessment(section_number, planspace)
    if assessment is None:
        return

    record_assessment_governance(section_number, planspace, assessment)

    verdict = assessment.get("verdict", "accept")
    if verdict == "accept_with_debt":
        _emit_risk_register_signal(section_number, planspace, assessment)
    elif verdict == "refactor_required":
        _emit_refactor_blocker(section_number, planspace, assessment)


def _emit_risk_register_signal(
    section_number: str,
    planspace: Path,
    assessment: dict,
) -> None:
    """Emit a debt signal for downstream risk register handling."""
    paths = PathRegistry(planspace)
    payload = {
        "section": section_number,
        "source": "post_impl_assessment",
        "profile_id": assessment.get("profile_id", ""),
        "problem_ids": assessment.get("problem_ids_addressed", []),
        "pattern_ids": assessment.get("pattern_ids_followed", []),
        "debt_items": assessment.get("debt_items", []),
        "verdict": assessment.get("verdict", "accept_with_debt"),
    }
    Services.artifact_io().write_json(paths.risk_register_signal(section_number), payload)


def _emit_refactor_blocker(
    section_number: str,
    planspace: Path,
    assessment: dict,
) -> None:
    """Emit a blocker signal when post-implementation assessment fails."""
    paths = PathRegistry(planspace)
    reasons = assessment.get("refactor_reasons", [])
    if not isinstance(reasons, list):
        reasons = []
    detail = (
        "; ".join(str(reason).strip() for reason in reasons if str(reason).strip())
        or "post-implementation assessment requires a refactor pass"
    )
    payload = {
        "state": "needs_parent",
        "blocker_type": "post_impl_refactor_required",
        "source": "post_impl_assessment",
        "section": section_number,
        "scope": f"section-{section_number}",
        "detail": detail,
        "why_blocked": detail,
        "needs": "Re-enter proposal/implementation loop with the flagged refactor reasons",
        "refactor_reasons": reasons,
        "profile_id": assessment.get("profile_id", ""),
        "problem_ids": assessment.get("problem_ids_addressed", []),
        "pattern_ids": assessment.get("pattern_ids_followed", []),
    }
    Services.artifact_io().write_json(paths.post_impl_blocker_signal(section_number), payload)


def check_and_fire_gate(
    db_path: Path,
    planspace: Path,
    gate_id: str,
    flow_id: str,
    origin_refs: list[str],
) -> None:
    """Check if all gate members are terminal and fire the gate if so."""
    _check_and_fire_gate_impl(
        db_path, planspace, gate_id, flow_id, origin_refs,
        build_gate_aggregate_manifest,
    )
