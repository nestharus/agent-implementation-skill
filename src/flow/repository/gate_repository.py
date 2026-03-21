"""Gate and chain lifecycle operations for the flow database.

Provides CRUD operations for gate members and chain management,
plus the gate-firing logic that triggers synthesis tasks.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import task_db
from flow.types.context import TaskStatus

logger = logging.getLogger(__name__)

_GATE_FAILURE_POLICY_BLOCK = "block"
from flow.repository.flow_context_store import (
    FlowContextStore,
    FlowReadStatus,
    continuation_relpath,
    flow_context_relpath,
    gate_aggregate_relpath,
    result_manifest_relpath,
)
from flow.types.context import FlowTask, new_chain_id, new_instance_id
from flow.types.routing import Task, request_task, update_task_flow_paths
from flow.types.schema import GateSpec

if TYPE_CHECKING:
    from containers import ArtifactIOService


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
    """Fail downstream chain tasks after an ancestor failure."""
    with task_db(db_path) as conn:
        conn.execute(
            """UPDATE tasks
               SET status='failed',
                   status_reason='dependency_failed',
                   error=?,
                   completed_at=datetime('now')
               WHERE chain_id=? AND id > ? AND status IN ('pending', 'blocked')""",
            (f"dependency_failed:{after_task_id}", chain_id, after_task_id),
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


def insert_gate_record(
    db_path: Path,
    gate_id: str,
    flow_id: str,
    declared_by_task_id: int | None,
    gate: GateSpec,
    branch_info: list[tuple[str, int, str]],
) -> None:
    """Insert a gate and its members into the task database."""
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
                gate_id, flow_id, declared_by_task_id,
                gate.mode, gate.failure_policy, len(branch_info),
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


class GateRepository:
    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io
        self._flow_store = FlowContextStore(artifact_io)

    def read_origin_refs(self, planspace: Path, task_id: int) -> list[str]:
        """Read origin_refs from a task's flow context file."""
        ctx_file = planspace / flow_context_relpath(task_id)
        status, data = self._flow_store.read_flow_json(ctx_file)
        if status == FlowReadStatus.OK and isinstance(data, dict):
            return data.get("origin_refs", [])
        return []

    def _fire_synthesis_task(
        self,
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

        syn_tid = request_task(
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

        update_task_flow_paths(
            db_path, syn_tid, syn_ctx_path, syn_cont_path, syn_res_path,
        )

        conn.execute(
            """UPDATE gates
               SET status='fired', fired_task_id=?,
                   fired_at=datetime('now')
               WHERE gate_id=?""",
            (syn_tid, gate_id),
        )
        conn.commit()

        self._flow_store.write_flow_context(
            planspace=planspace,
                task=FlowTask(
                    task_id=syn_tid,
                    instance_id=syn_instance_id,
                    flow_id=flow_id,
                    chain_id=syn_chain_id,
                    task_type=gate["synthesis_task_type"],
                    declared_by_task_id=None,
                    trigger_gate_id=gate_id,
                ),
            origin_refs=origin_refs,
            previous_task_id=None,
        )

    @staticmethod
    def _reconcile_member_task_statuses(
        conn: sqlite3.Connection,
        members: list[dict],
    ) -> int:
        """Ensure leaf tasks match their gate-member status.

        When a gate fires, the gate_members table is authoritative.  If a
        member's leaf task is still ``running`` in the tasks table (e.g. the
        dispatcher crashed between capturing output and updating task
        status), force it to match the member status so the task does not
        remain stuck in ``running`` forever.

        Returns the number of tasks that were patched.
        """
        _TERMINAL = {TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.CANCELLED}
        patched = 0
        for member in members:
            leaf_id = member.get("leaf_task_id")
            member_status = member.get("status")
            if leaf_id is None or member_status not in _TERMINAL:
                continue
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (leaf_id,)
            ).fetchone()
            if row is None:
                continue
            task_status = row[0] if isinstance(row, tuple) else row["status"]
            if task_status in _TERMINAL:
                continue
            # Task is non-terminal (e.g. still 'running') — patch it.
            status_reason = None
            if member_status == TaskStatus.FAILED and task_status == "blocked":
                status_reason = "dependency_failed"
            conn.execute(
                "UPDATE tasks SET status=?, status_reason=?, completed_at=datetime('now') "
                "WHERE id=?",
                (str(member_status), status_reason, leaf_id),
            )
            patched += 1
            logger.warning(
                "gate member leaf task %d was '%s', forced to '%s'",
                leaf_id, task_status, member_status,
            )
        if patched:
            conn.commit()
        return patched

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

            # Safety: reconcile any leaf tasks still stuck in 'running'.
            self._reconcile_member_task_statuses(conn, members)

            any_failed = any(
                member["status"] == TaskStatus.FAILED for member in members
            )
            if gate["failure_policy"] == _GATE_FAILURE_POLICY_BLOCK and any_failed:
                conn.execute(
                    "UPDATE gates SET status='blocked' WHERE gate_id=? AND status='open'",
                    (gate_id,),
                )
                conn.commit()
                return

            # Atomic guard: claim the gate for firing.  If another thread
            # already moved it out of 'open', rowcount is 0 and we bail.
            claim_cur = conn.execute(
                "UPDATE gates SET status='firing' WHERE gate_id=? AND status='open'",
                (gate_id,),
            )
            conn.commit()
            if claim_cur.rowcount == 0:
                return  # another thread already fired or blocked this gate

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
                self._fire_synthesis_task(
                    conn, db_path, planspace, gate, gate_id,
                    flow_id, agg_relpath, origin_refs,
                )
