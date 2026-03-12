"""Flow submission helpers shared by ingestion and reconciliation."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from flow.repository.catalog import resolve_chain_ref
from flow.repository.flow_context_store import (
    continuation_relpath,
    flow_context_relpath,
    result_manifest_relpath,
    write_flow_context,
)
from flow.service.task_db_client import task_db
from flow.types.routing import submit_task
from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from containers import Services


def new_instance_id() -> str:
    return f"inst_{uuid.uuid4()}"


def new_flow_id() -> str:
    return f"flow_{uuid.uuid4()}"


def new_chain_id() -> str:
    return f"chain_{uuid.uuid4()}"


def new_gate_id() -> str:
    return f"gate_{uuid.uuid4()}"


def submit_chain(
    db_path: Path,
    submitted_by: str,
    steps: list[TaskSpec],
    *,
    flow_id: str | None = None,
    chain_id: str | None = None,
    declared_by_task_id: int | None = None,
    origin_refs: list[str] | None = None,
    planspace: Path | None = None,
    freshness_token: str | None = None,
) -> list[int]:
    """Submit a linear chain of tasks."""
    if not steps:
        return []

    flow_id = flow_id or new_flow_id()
    chain_id = chain_id or new_chain_id()
    refs = origin_refs or []

    task_ids: list[int] = []
    previous_task_id: int | None = None

    for step in steps:
        instance_id = new_instance_id()
        depends_on = previous_task_id
        tid = submit_task(
            db_path,
            submitted_by,
            step.task_type,
            problem_id=step.problem_id or None,
            concern_scope=step.concern_scope or None,
            payload_path=step.payload_path or None,
            priority=step.priority,
            depends_on=depends_on,
            instance_id=instance_id,
            flow_id=flow_id,
            chain_id=chain_id,
            declared_by_task_id=declared_by_task_id,
            flow_context_path=None,
            continuation_path=None,
            result_manifest_path=None,
            freshness_token=freshness_token,
        )

        ctx_path = flow_context_relpath(tid)
        cont_path = continuation_relpath(tid)
        res_path = result_manifest_relpath(tid)

        with task_db(db_path) as conn:
            conn.execute(
                """UPDATE tasks
                   SET flow_context_path=?, continuation_path=?,
                       result_manifest_path=?
                   WHERE id=?""",
                (ctx_path, cont_path, res_path, tid),
            )
            conn.commit()

        if planspace is not None:
            write_flow_context(
                planspace=planspace,
                task_id=tid,
                instance_id=instance_id,
                flow_id=flow_id,
                chain_id=chain_id,
                task_type=step.task_type,
                declared_by_task_id=declared_by_task_id,
                depends_on=depends_on,
                trigger_gate_id=None,
                origin_refs=refs,
                previous_task_id=previous_task_id,
            )

        task_ids.append(tid)
        previous_task_id = tid

    return task_ids


def submit_fanout(
    db_path: Path,
    submitted_by: str,
    branches: list[BranchSpec],
    *,
    flow_id: str,
    declared_by_task_id: int | None = None,
    origin_refs: list[str] | None = None,
    gate: GateSpec | None = None,
    planspace: Path | None = None,
    freshness_token: str | None = None,
) -> str | None:
    """Submit parallel branches, optionally under a gate."""
    if not branches:
        return None

    refs = origin_refs or []
    gate_id: str | None = None
    if gate is not None:
        gate_id = new_gate_id()

    branch_info: list[tuple[str, int, str]] = []

    for branch in branches:
        child_chain_id = new_chain_id()

        if branch.chain_ref:
            steps = resolve_chain_ref(branch.chain_ref, branch.args, refs)
        else:
            steps = branch.steps

        branch_freshness: str | None = freshness_token
        if branch_freshness is None and planspace is not None:
            for step in steps:
                if step.concern_scope:
                    match = re.match(r"^section-(\d+)$", step.concern_scope)
                    if match:
                        branch_freshness = Services.freshness().compute(
                            planspace,
                            match.group(1),
                        )
                        break

        task_ids = submit_chain(
            db_path,
            submitted_by,
            steps,
            flow_id=flow_id,
            chain_id=child_chain_id,
            declared_by_task_id=declared_by_task_id,
            origin_refs=refs,
            planspace=planspace,
            freshness_token=branch_freshness,
        )

        if task_ids:
            branch_info.append((child_chain_id, task_ids[-1], branch.label))

    if gate_id is not None and branch_info:
        synthesis = gate.synthesis if gate else None
        with task_db(db_path) as conn:
            conn.execute(
                """INSERT INTO gates(
                       gate_id, flow_id, created_by_task_id, mode,
                       failure_policy, expected_count,
                       synthesis_task_type, synthesis_problem_id,
                       synthesis_concern_scope, synthesis_payload_path,
                       synthesis_priority)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    gate_id,
                    flow_id,
                    declared_by_task_id,
                    gate.mode,
                    gate.failure_policy,
                    len(branch_info),
                    synthesis.task_type if synthesis else None,
                    synthesis.problem_id if synthesis else None,
                    synthesis.concern_scope if synthesis else None,
                    synthesis.payload_path if synthesis else None,
                    synthesis.priority if synthesis else None,
                ),
            )

            for child_chain_id, leaf_tid, label in branch_info:
                conn.execute(
                    """INSERT INTO gate_members(
                           gate_id, chain_id, slot_label, leaf_task_id)
                       VALUES(?, ?, ?, ?)""",
                    (gate_id, child_chain_id, label or None, leaf_tid),
                )

            conn.commit()

    return gate_id
