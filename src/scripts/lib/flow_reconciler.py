"""Flow completion reconciliation helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .artifact_io import rename_malformed, write_json
from .flow_context import (
    flow_context_relpath,
    gate_aggregate_relpath,
    read_flow_json,
    result_manifest_relpath,
    write_flow_context,
)
from .flow_submitter import (
    new_chain_id,
    new_instance_id,
    submit_chain,
    submit_fanout,
)

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from flow_schema import ChainAction, FanoutAction, parse_flow_signal  # noqa: E402
from task_router import submit_task  # noqa: E402


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
) -> None:
    """Called after a task completes or fails."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()

    if row is None:
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
        write_json(planspace / result_manifest_path, manifest)

    origin_refs = read_origin_refs(planspace, task_id)

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
                    rename_malformed(cont_file)
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

        if continuation is not None:
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
                        conn = sqlite3.connect(str(db_path), timeout=5.0)
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("PRAGMA busy_timeout=5000")
                        conn.execute(
                            "UPDATE tasks SET depends_on=? WHERE id=?",
                            (str(task_id), new_ids[0]),
                        )
                        conn.commit()
                        conn.close()

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


def read_origin_refs(planspace: Path, task_id: int) -> list[str]:
    """Read origin_refs from a task's flow context file."""
    ctx_file = planspace / flow_context_relpath(task_id)
    status, data = read_flow_json(ctx_file)
    if status == "ok" and isinstance(data, dict):
        return data.get("origin_refs", [])
    return []


def find_gate_for_chain(db_path: Path, chain_id: str) -> str | None:
    """Find the gate_id for a given chain_id, if any."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()
    cur.execute("SELECT gate_id FROM gate_members WHERE chain_id = ?", (chain_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_gate_member_leaf(
    db_path: Path,
    gate_id: str,
    chain_id: str,
) -> int | None:
    """Get the leaf_task_id for a gate member."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()
    cur.execute(
        "SELECT leaf_task_id FROM gate_members WHERE gate_id=? AND chain_id=?",
        (gate_id, chain_id),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def update_gate_member_leaf(
    db_path: Path,
    gate_id: str,
    chain_id: str,
    new_leaf_task_id: int,
) -> None:
    """Update a gate member's leaf_task_id when its chain extends."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """UPDATE gate_members
           SET leaf_task_id=?
           WHERE gate_id=? AND chain_id=?""",
        (new_leaf_task_id, gate_id, chain_id),
    )
    conn.commit()
    conn.close()


def cancel_chain_descendants(
    db_path: Path,
    chain_id: str,
    after_task_id: int,
) -> None:
    """Mark all pending tasks in a chain after a failed ancestor as cancelled."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """UPDATE tasks
           SET status='cancelled', error='chain ancestor failed',
               completed_at=datetime('now')
           WHERE chain_id=? AND id > ? AND status='pending'""",
        (chain_id, after_task_id),
    )
    conn.commit()
    conn.close()


def update_gate_member(
    db_path: Path,
    gate_id: str,
    chain_id: str,
    status: str,
    result_manifest_path: str | None = None,
) -> None:
    """Update a gate member's status and result path."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """UPDATE gate_members
           SET status=?, result_manifest_path=?,
               completed_at=datetime('now')
           WHERE gate_id=? AND chain_id=?""",
        (status, result_manifest_path, gate_id, chain_id),
    )
    conn.commit()
    conn.close()


def check_and_fire_gate(
    db_path: Path,
    planspace: Path,
    gate_id: str,
    flow_id: str,
    origin_refs: list[str],
) -> None:
    """Check if all gate members are terminal and fire the gate if so."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()
    cur.execute("SELECT * FROM gates WHERE gate_id = ?", (gate_id,))
    gate_row = cur.fetchone()
    if gate_row is None:
        conn.close()
        return
    gate = dict(gate_row)

    cur.execute(
        "SELECT * FROM gate_members WHERE gate_id = ? ORDER BY chain_id",
        (gate_id,),
    )
    members = [dict(row) for row in cur.fetchall()]
    conn.close()

    terminal_statuses = {"complete", "failed"}
    if not all(member["status"] in terminal_statuses for member in members):
        return

    any_failed = any(member["status"] == "failed" for member in members)
    if gate["failure_policy"] == "block" and any_failed:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "UPDATE gates SET status='blocked' WHERE gate_id=?",
            (gate_id,),
        )
        conn.commit()
        conn.close()
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
    write_json(planspace / agg_relpath, aggregate)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """UPDATE gates
           SET status='ready', aggregate_manifest_path=?
           WHERE gate_id=?""",
        (agg_relpath, gate_id),
    )
    conn.commit()

    if gate["synthesis_task_type"]:
        syn_chain_id = new_chain_id()
        syn_instance_id = new_instance_id()

        syn_tid = submit_task(
            db_path,
            "reconciler",
            gate["synthesis_task_type"],
            problem_id=gate["synthesis_problem_id"],
            concern_scope=gate["synthesis_concern_scope"],
            payload_path=gate["synthesis_payload_path"],
            priority=gate["synthesis_priority"] or "normal",
            instance_id=syn_instance_id,
            flow_id=flow_id,
            chain_id=syn_chain_id,
            declared_by_task_id=None,
            trigger_gate_id=gate_id,
            flow_context_path=agg_relpath,
            result_manifest_path=result_manifest_relpath(0),
        )

        syn_ctx_path = flow_context_relpath(syn_tid)
        syn_cont_path = f"artifacts/flows/task-{syn_tid}-continuation.json"
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
            task_id=syn_tid,
            instance_id=syn_instance_id,
            flow_id=flow_id,
            chain_id=syn_chain_id,
            task_type=gate["synthesis_task_type"],
            declared_by_task_id=None,
            depends_on=None,
            trigger_gate_id=gate_id,
            origin_refs=origin_refs,
            previous_task_id=None,
        )

    conn.close()
