"""Gate and chain lifecycle operations for the flow database.

Provides CRUD operations for gate members and chain management,
plus the gate-firing logic that triggers synthesis tasks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import task_db
from flow.types.context import TaskStatus

_GATE_FAILURE_POLICY_BLOCK = "block"
from flow.repository.flow_context_store import (
    FlowReadStatus,
    continuation_relpath,
    flow_context_relpath,
    gate_aggregate_relpath,
    read_flow_json,
    result_manifest_relpath,
    write_flow_context,
)
from flow.engine.flow_submitter import new_chain_id, new_instance_id
from flow.types.context import FlowTask
from flow.types.routing import Task, submit_task

if TYPE_CHECKING:
    from containers import ArtifactIOService


def read_origin_refs(planspace: Path, task_id: int) -> list[str]:
    """Read origin_refs from a task's flow context file."""
    ctx_file = planspace / flow_context_relpath(task_id)
    status, data = read_flow_json(ctx_file)
    if status == FlowReadStatus.OK and isinstance(data, dict):
        return data.get("origin_refs", [])
    return []


def find_gate_for_chain(db_path: Path, chain_id: str) -> str | None:
    """Find the gate_id for a given chain_id, if any."""
    with task_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT gate_id FROM gate_members WHERE chain_id = ?", (chain_id,))
        row = cur.fetchone()
    return row[0] if row else None


def get_gate_member_leaf(
    db_path: Path,
    gate_id: str,
    chain_id: str,
) -> int | None:
    """Get the leaf_task_id for a gate member."""
    with task_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT leaf_task_id FROM gate_members WHERE gate_id=? AND chain_id=?",
            (gate_id, chain_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def update_gate_member_leaf(
    db_path: Path,
    gate_id: str,
    chain_id: str,
    new_leaf_task_id: int,
) -> None:
    """Update a gate member's leaf_task_id when its chain extends."""
    with task_db(db_path) as conn:
        conn.execute(
            """UPDATE gate_members
               SET leaf_task_id=?
               WHERE gate_id=? AND chain_id=?""",
            (new_leaf_task_id, gate_id, chain_id),
        )
        conn.commit()


def cancel_chain_descendants(
    db_path: Path,
    chain_id: str,
    after_task_id: int,
) -> None:
    """Mark all pending tasks in a chain after a failed ancestor as cancelled."""
    with task_db(db_path) as conn:
        conn.execute(
            """UPDATE tasks
               SET status='cancelled', error='chain ancestor failed',
                   completed_at=datetime('now')
               WHERE chain_id=? AND id > ? AND status='pending'""",
            (chain_id, after_task_id),
        )
        conn.commit()


def update_gate_member(
    db_path: Path,
    gate_id: str,
    chain_id: str,
    status: str,
    result_manifest_path: str | None = None,
) -> None:
    """Update a gate member's status and result path."""
    with task_db(db_path) as conn:
        conn.execute(
            """UPDATE gate_members
               SET status=?, result_manifest_path=?,
                   completed_at=datetime('now')
               WHERE gate_id=? AND chain_id=?""",
            (status, result_manifest_path, gate_id, chain_id),
        )
        conn.commit()


def _fire_synthesis_task(
    conn: sqlite3.Connection,
    db_path: Path,
    planspace: Path,
    gate: dict,
    gate_id: str,
    flow_id: str,
    agg_relpath: str,
    origin_refs: list[str],
) -> None:
    """Create and submit the synthesis task when a gate fires."""
    syn_chain_id = new_chain_id()
    syn_instance_id = new_instance_id()

    syn_tid = submit_task(
        db_path,
        Task(
            task_type=gate["synthesis_task_type"],
            submitted_by="reconciler",
            problem_id=gate["synthesis_problem_id"],
            concern_scope=gate["synthesis_concern_scope"],
            payload_path=gate["synthesis_payload_path"],
            priority=gate["synthesis_priority"] or "normal",
            instance_id=syn_instance_id,
            flow_id=flow_id,
            chain_id=syn_chain_id,
            trigger_gate_id=gate_id,
            flow_context_path=agg_relpath,
            result_manifest_path=result_manifest_relpath(0),
        ),
    )

    syn_ctx_path = flow_context_relpath(syn_tid)
    syn_cont_path = continuation_relpath(syn_tid)
    syn_res_path = result_manifest_relpath(syn_tid)

    conn.execute(
        """UPDATE tasks
           SET flow_context_path=?, continuation_path=?,
               result_manifest_path=?
           WHERE id=?""",
        (syn_ctx_path, syn_cont_path, syn_res_path, syn_tid),
    )
    conn.execute(
        """UPDATE gates
           SET status='fired', fired_task_id=?,
               fired_at=datetime('now')
           WHERE gate_id=?""",
        (syn_tid, gate_id),
    )
    conn.commit()

    write_flow_context(
        planspace=planspace,
        task=FlowTask(
            task_id=syn_tid,
            instance_id=syn_instance_id,
            flow_id=flow_id,
            chain_id=syn_chain_id,
            task_type=gate["synthesis_task_type"],
            declared_by_task_id=None,
            depends_on=None,
            trigger_gate_id=gate_id,
        ),
        origin_refs=origin_refs,
        previous_task_id=None,
    )


class GateRepository:
    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def check_and_fire_gate(
        self,
        db_path: Path,
        planspace: Path,
        gate_id: str,
        flow_id: str,
        origin_refs: list[str],
        build_gate_aggregate_manifest,
    ) -> None:
        """Check if all gate members are terminal and fire the gate if so.

        ``build_gate_aggregate_manifest`` is passed as a callable to avoid
        circular imports between repository and engine layers.
        """
        with task_db(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM gates WHERE gate_id = ?", (gate_id,))
            gate_row = cur.fetchone()
            if gate_row is None:
                return
            gate = dict(gate_row)

            cur.execute(
                "SELECT * FROM gate_members WHERE gate_id = ? ORDER BY chain_id",
                (gate_id,),
            )
            members = [dict(row) for row in cur.fetchall()]

            terminal_statuses = {TaskStatus.COMPLETE, TaskStatus.FAILED}
            if not all(
                member["status"] in terminal_statuses for member in members
            ):
                return

            any_failed = any(
                member["status"] == TaskStatus.FAILED for member in members
            )
            if gate["failure_policy"] == _GATE_FAILURE_POLICY_BLOCK and any_failed:
                conn.execute(
                    "UPDATE gates SET status='blocked' WHERE gate_id=?",
                    (gate_id,),
                )
                conn.commit()
                return

            member_entries = [
                {
                    "chain_id": member["chain_id"],
                    "slot_label": member["slot_label"],
                    "status": member["status"],
                    "result_manifest_path": member["result_manifest_path"],
                }
                for member in members
            ]
            aggregate = build_gate_aggregate_manifest(
                gate_id=gate_id,
                flow_id=flow_id,
                mode=gate["mode"],
                failure_policy=gate["failure_policy"],
                origin_refs=origin_refs,
                members=member_entries,
            )

            agg_relpath = gate_aggregate_relpath(gate_id)
            self._artifact_io.write_json(
                PathRegistry(planspace).flow_gate_aggregate(gate_id), aggregate
            )

            conn.execute(
                """UPDATE gates
                   SET status='ready', aggregate_manifest_path=?
                   WHERE gate_id=?""",
                (agg_relpath, gate_id),
            )
            conn.commit()

            if gate["synthesis_task_type"]:
                _fire_synthesis_task(
                    conn, db_path, planspace, gate, gate_id,
                    flow_id, agg_relpath, origin_refs,
                )


# Backward-compat wrappers

def _get_repository() -> GateRepository:
    from containers import Services
    return GateRepository(
        artifact_io=Services.artifact_io(),
    )


def check_and_fire_gate(
    db_path: Path,
    planspace: Path,
    gate_id: str,
    flow_id: str,
    origin_refs: list[str],
    build_gate_aggregate_manifest,
) -> None:
    return _get_repository().check_and_fire_gate(
        db_path, planspace, gate_id, flow_id, origin_refs,
        build_gate_aggregate_manifest,
    )
