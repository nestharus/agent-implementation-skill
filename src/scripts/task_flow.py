"""Flow submission engine — submits chains and fanouts to the task queue.

Also provides ``compute_section_freshness`` — a lightweight, model-free
hash of a section's alignment artifacts used as a freshness token for
the dispatcher's staleness gate (P4).

Uses the data structures from flow_schema.py and the DB functions from
task_router.py.  Writes flow context JSON files so agents can discover
their position in a chain or fanout.

Also provides completion reconciliation: when a task finishes, this module
handles chain continuations, gate member updates, failure cascading, and
gate firing.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.artifact_io import read_json, rename_malformed, write_json

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from flow_catalog import resolve_chain_ref  # noqa: E402
from flow_schema import (  # noqa: E402
    BranchSpec,
    ChainAction,
    FanoutAction,
    GateSpec,
    TaskSpec,
    parse_flow_signal,
)
from task_router import submit_task  # noqa: E402


# ---------------------------------------------------------------------------
# Flow corruption error
# ---------------------------------------------------------------------------

class FlowCorruptionError(Exception):
    """Raised when a flow artifact is corrupt (malformed JSON)."""
    pass


# ---------------------------------------------------------------------------
# ID allocation
# ---------------------------------------------------------------------------

def _new_instance_id() -> str:
    return f"inst_{uuid.uuid4()}"


def _new_flow_id() -> str:
    return f"flow_{uuid.uuid4()}"


def _new_chain_id() -> str:
    return f"chain_{uuid.uuid4()}"


def _new_gate_id() -> str:
    return f"gate_{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Section freshness (P4)
# ---------------------------------------------------------------------------

def compute_section_freshness(planspace: Path, section_number: str) -> str:
    """Compute a canonical alignment fingerprint for a section.

    R82/P3: Hashes the same load-bearing artifacts recognized by the
    section-loop's ``_section_inputs_hash`` to prevent queued tasks
    from surviving alignment-relevant changes.  Must stay fast
    (file-level hashing only, no model loading).
    """
    hasher = hashlib.sha256()
    artifacts = planspace / "artifacts"
    sec = section_number

    # Excerpts + section spec + integration proposal
    for suffix in (
        f"sections/section-{sec}-alignment-excerpt.md",
        f"sections/section-{sec}-proposal-excerpt.md",
        f"sections/section-{sec}.md",
        f"proposals/section-{sec}-integration-proposal.md",
    ):
        p = artifacts / suffix
        if p.exists():
            hasher.update(p.read_bytes())

    # Notes targeting this section
    notes_dir = artifacts / "notes"
    if notes_dir.exists():
        for note in sorted(notes_dir.glob(f"from-*-to-{sec}.md")):
            hasher.update(note.read_bytes())

    # Tool registry
    tools_path = artifacts / "tool-registry.json"
    if tools_path.exists():
        hasher.update(tools_path.read_bytes())

    # Decisions
    decisions_path = artifacts / "decisions" / f"section-{sec}.md"
    if decisions_path.exists():
        hasher.update(decisions_path.read_bytes())

    # Microstrategy
    microstrategy_path = (
        artifacts / "proposals" / f"section-{sec}-microstrategy.md"
    )
    if microstrategy_path.exists():
        hasher.update(microstrategy_path.read_bytes())

    # TODOs
    todos_path = artifacts / "todos" / f"section-{sec}-todos.md"
    if todos_path.exists():
        hasher.update(todos_path.read_bytes())

    # Codemap + corrections
    codemap_path = artifacts / "codemap.md"
    if codemap_path.exists():
        hasher.update(codemap_path.read_bytes())
    corrections_path = artifacts / "signals" / "codemap-corrections.json"
    if corrections_path.exists():
        hasher.update(corrections_path.read_bytes())

    # Mode files
    for mode_file in (
        artifacts / "project-mode.txt",
        artifacts / "signals" / "project-mode.json",
        artifacts / "sections" / f"section-{sec}-mode.txt",
    ):
        if mode_file.exists():
            hasher.update(mode_file.read_bytes())

    # Problem frame
    problem_frame = (
        artifacts / "sections" / f"section-{sec}-problem-frame.md"
    )
    if problem_frame.exists():
        hasher.update(problem_frame.read_bytes())

    # Intent artifacts
    intent_global = artifacts / "intent" / "global"
    for intent_file in (
        intent_global / "philosophy.md",
        intent_global / "philosophy-source-manifest.json",
        intent_global / "philosophy-source-map.json",
    ):
        if intent_file.exists():
            hasher.update(intent_file.read_bytes())
    intent_sec_dir = artifacts / "intent" / "sections" / f"section-{sec}"
    for intent_file in (
        intent_sec_dir / "problem.md",
        intent_sec_dir / "problem-alignment.md",
        intent_sec_dir / "philosophy-excerpt.md",
    ):
        if intent_file.exists():
            hasher.update(intent_file.read_bytes())

    # Proposal state — captures resolved/unresolved anchors, contracts,
    # research questions, and execution_ready flag
    proposal_state_path = (
        artifacts / "proposals"
        / f"section-{sec}-proposal-state.json"
    )
    if proposal_state_path.exists():
        hasher.update(proposal_state_path.read_bytes())

    # Reconciliation result — cross-section overlap/conflict findings
    reconciliation_path = (
        artifacts / "reconciliation"
        / f"section-{sec}-reconciliation-result.json"
    )
    if reconciliation_path.exists():
        hasher.update(reconciliation_path.read_bytes())

    # Execution readiness — fail-closed gate artifact
    readiness_path = (
        artifacts / "readiness"
        / f"section-{sec}-execution-ready.json"
    )
    if readiness_path.exists():
        hasher.update(readiness_path.read_bytes())

    return hasher.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Flow context file paths (relative to planspace)
# ---------------------------------------------------------------------------

def _flow_context_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-context.json"


def _continuation_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-continuation.json"


def _result_manifest_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-result.json"


def _dispatch_prompt_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-dispatch.md"


# ---------------------------------------------------------------------------
# Fail-closed JSON reader
# ---------------------------------------------------------------------------

def _read_flow_json(path: Path) -> tuple[str, dict | list | None]:
    """Read a flow artifact JSON file with fail-closed semantics.

    Returns (status, data) where status is one of:
    - "ok" — valid JSON, data is the parsed content
    - "missing" — file does not exist, data is None
    - "malformed" — file exists but is corrupt, data is None
      (file is renamed to .malformed.json and a warning is logged)
    """
    if not path.exists():
        return ("missing", None)

    data = read_json(path)
    if data is None:
        print(
            f"[FLOW][WARN] Malformed JSON in {path} "
            f"— renaming to .malformed.json",
        )
        return ("malformed", None)

    return ("ok", data)


# ---------------------------------------------------------------------------
# Flow context reading (for dispatch)
# ---------------------------------------------------------------------------

def build_flow_context(
    planspace: Path,
    task_id: int,
    flow_context_path: str | None = None,
    continuation_path: str | None = None,
    trigger_gate_id: str | None = None,
) -> dict | None:
    """Read and return the flow context for a task, enriched for dispatch.

    Returns None if the task has no flow context path declared.

    Raises ``FlowCorruptionError`` when the flow_context_path IS
    declared but the file is missing or contains malformed JSON.
    The dispatcher should catch this and fail the task.

    When a trigger_gate_id is present, looks up the gate's aggregate
    manifest path from the DB-written flow context or from the gate
    aggregate file on disk and includes it in the returned dict.

    The returned dict is what the dispatched agent should read to
    discover predecessor results and gate aggregates.
    """
    if not flow_context_path:
        return None

    ctx_file = planspace / flow_context_path
    status, context = _read_flow_json(ctx_file)

    if status == "missing":
        raise FlowCorruptionError(
            f"flow context declared but file missing: {ctx_file}"
        )

    if status == "malformed":
        raise FlowCorruptionError(
            f"flow context declared but file corrupt: {ctx_file}"
        )

    # Enrich: if this is a synthesis task triggered by a gate,
    # fill in gate_aggregate_manifest from the known path convention.
    gate_id = trigger_gate_id or (
        context.get("task", {}).get("trigger_gate_id")
    )
    if gate_id and not context.get("gate_aggregate_manifest"):
        agg_relpath = _gate_aggregate_relpath(gate_id)
        agg_file = planspace / agg_relpath
        if agg_file.exists():
            context["gate_aggregate_manifest"] = agg_relpath

    # Ensure continuation_path is present (may come from DB row).
    if continuation_path and not context.get("continuation_path"):
        context["continuation_path"] = continuation_path

    return context


def write_dispatch_prompt(
    planspace: Path,
    task_id: int,
    original_prompt_path: Path,
    flow_context_path: str,
    continuation_path: str | None = None,
) -> Path:
    """Create a wrapper prompt that includes flow context for dispatch.

    The original prompt content is NOT mutated.  Instead, a new file
    is written at ``artifacts/flows/task-{id}-dispatch.md`` that
    prepends a ``<flow-context>`` block and then includes the original
    prompt content verbatim.

    Returns the absolute path to the wrapper prompt file.
    """
    flows_dir = planspace / "artifacts" / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)

    # Read original prompt content.
    original_content = ""
    if original_prompt_path.exists():
        original_content = original_prompt_path.read_text(encoding="utf-8")

    # Build the flow-context header.
    header_lines = [
        "<flow-context>",
        f"Read your flow context from: {flow_context_path}",
    ]
    if continuation_path:
        header_lines.append(
            f"Write any follow-up task declarations to: {continuation_path}"
        )
    header_lines.append("</flow-context>")
    header_lines.append("")  # blank line separator

    wrapper_content = "\n".join(header_lines) + "\n" + original_content

    dispatch_path = flows_dir / f"task-{task_id}-dispatch.md"
    dispatch_path.write_text(wrapper_content, encoding="utf-8")

    return dispatch_path


# ---------------------------------------------------------------------------
# Flow context writing
# ---------------------------------------------------------------------------

def _write_flow_context(
    planspace: Path,
    task_id: int,
    instance_id: str,
    flow_id: str,
    chain_id: str,
    task_type: str,
    declared_by_task_id: int | None,
    depends_on: int | None,
    trigger_gate_id: str | None,
    origin_refs: list[str],
    previous_task_id: int | None,
) -> None:
    """Write a flow context JSON file for a task."""
    flows_dir = planspace / "artifacts" / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)

    previous_result = None
    if previous_task_id is not None:
        previous_result = _result_manifest_relpath(previous_task_id)

    context = {
        "task": {
            "task_id": task_id,
            "instance_id": instance_id,
            "flow_id": flow_id,
            "chain_id": chain_id,
            "task_type": task_type,
            "declared_by_task_id": declared_by_task_id,
            "depends_on": depends_on,
            "trigger_gate_id": trigger_gate_id,
        },
        "origin_refs": origin_refs or [],
        "previous_result_manifest": previous_result,
        "gate_aggregate_manifest": None,
        "continuation_path": _continuation_relpath(task_id),
        "result_manifest_path": _result_manifest_relpath(task_id),
    }

    context_path = flows_dir / f"task-{task_id}-context.json"
    write_json(context_path, context)


# ---------------------------------------------------------------------------
# Core submission functions
# ---------------------------------------------------------------------------

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
    """Submit a linear chain of tasks.

    - Allocates instance_ids for each task
    - Allocates flow_id if not provided
    - Allocates chain_id if not provided
    - Wires linear depends_on (step[1] depends on step[0], etc.)
    - Writes flow_context JSON for each task at planspace/artifacts/flows/
    - Sets continuation_path for each task
    - Sets result_manifest_path for each task
    - ``freshness_token`` (P4): propagated to each submitted task
    - Returns list of task IDs
    """
    if not steps:
        return []

    flow_id = flow_id or _new_flow_id()
    chain_id = chain_id or _new_chain_id()
    refs = origin_refs or []

    task_ids: list[int] = []
    previous_task_id: int | None = None

    for step in steps:
        instance_id = _new_instance_id()
        depends_on = previous_task_id

        # Compute paths for this task — we need the task_id first,
        # so submit, then write context.  Use placeholder paths for
        # DB insertion; we update them after we know the id.
        # Actually, since submit_task returns the id, we can compute
        # the paths right after.
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
            flow_context_path=None,  # set below
            continuation_path=None,  # set below
            result_manifest_path=None,  # set below
            freshness_token=freshness_token,
        )

        # Now compute the real paths and update the row.
        ctx_path = _flow_context_relpath(tid)
        cont_path = _continuation_relpath(tid)
        res_path = _result_manifest_relpath(tid)

        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """UPDATE tasks
               SET flow_context_path=?, continuation_path=?,
                   result_manifest_path=?
               WHERE id=?""",
            (ctx_path, cont_path, res_path, tid),
        )
        conn.commit()
        conn.close()

        # Write flow context file if planspace provided
        if planspace is not None:
            _write_flow_context(
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
) -> str | None:
    """Submit parallel branches, optionally under a gate.

    - Allocates one child chain_id per branch
    - Resolves chain_ref via flow_catalog.resolve_chain_ref() if present
    - Creates gate row if gate spec provided
    - Registers each child chain as a gate member
    - Returns gate_id if a gate was created, None otherwise
    """
    if not branches:
        return None

    refs = origin_refs or []
    gate_id: str | None = None

    # Create gate if specified
    if gate is not None:
        gate_id = _new_gate_id()

    # Track (chain_id, last_task_id, label) for gate member registration
    branch_info: list[tuple[str, int, str]] = []

    for branch in branches:
        child_chain_id = _new_chain_id()

        # Resolve steps: either inline or from chain_ref
        if branch.chain_ref:
            steps = resolve_chain_ref(
                branch.chain_ref, branch.args, refs
            )
        else:
            steps = branch.steps

        # R82/P3: Compute per-branch freshness from first section-scoped step
        branch_freshness: str | None = None
        if planspace is not None:
            for step in steps:
                if step.concern_scope:
                    m = re.match(r'^section-(\d+)$', step.concern_scope)
                    if m:
                        branch_freshness = compute_section_freshness(
                            planspace, m.group(1),
                        )
                        break

        # Submit the branch as a chain
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
            last_tid = task_ids[-1]
            branch_info.append((child_chain_id, last_tid, branch.label))

    # Insert gate row and members if gate was requested
    if gate_id is not None and branch_info:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        # Insert gate
        syn = gate.synthesis if gate else None
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
                syn.task_type if syn else None,
                syn.problem_id if syn else None,
                syn.concern_scope if syn else None,
                syn.payload_path if syn else None,
                syn.priority if syn else None,
            ),
        )

        # Insert gate members
        for child_chain_id, leaf_tid, label in branch_info:
            conn.execute(
                """INSERT INTO gate_members(
                       gate_id, chain_id, slot_label, leaf_task_id)
                   VALUES(?, ?, ?, ?)""",
                (gate_id, child_chain_id, label or None, leaf_tid),
            )

        conn.commit()
        conn.close()

    return gate_id


# ---------------------------------------------------------------------------
# Result manifests
# ---------------------------------------------------------------------------

def _gate_aggregate_relpath(gate_id: str) -> str:
    return f"artifacts/flows/{gate_id}-aggregate.json"


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
    """Build result manifest dict for a completed/failed task."""
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


# ---------------------------------------------------------------------------
# Completion reconciliation
# ---------------------------------------------------------------------------

def reconcile_task_completion(
    db_path: Path,
    planspace: Path,
    task_id: int,
    status: str,
    output_path: str | None,
    error: str | None = None,
) -> None:
    """Called after a task completes or fails. This is THE closure.

    Steps:
    1. Read the task row from DB
    2. Write result manifest at result_manifest_path
    3. Handle failure path (cancel descendants, update gate member)
    4. Handle success path (read continuation, extend chain or fanout)
    5. If no continuation and this chain is gated, finalize the gate member
    """
    # 1. Read the task row
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

    # 2. Write result manifest
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
        manifest_file = planspace / result_manifest_path
        write_json(manifest_file, manifest)

    # Read origin_refs from flow context file (needed for gate operations)
    origin_refs = _read_origin_refs(planspace, task_id)

    # 3. Failure path
    if status == "failed":
        # Cancel all pending descendants in the same chain
        if chain_id:
            _cancel_chain_descendants(db_path, chain_id, task_id)

        # If this chain is a gate member, mark it failed and check gate
        if chain_id:
            gate_id = _find_gate_for_chain(db_path, chain_id)
            if gate_id:
                _update_gate_member(
                    db_path, gate_id, chain_id,
                    "failed", result_manifest_path,
                )
                _check_and_fire_gate(
                    db_path, planspace, gate_id, flow_id, origin_refs,
                )
        return

    # 4. Success path
    if status == "complete":
        # Read continuation file — fail closed on corruption
        continuation = None
        if continuation_path:
            cont_file = planspace / continuation_path
            if cont_file.exists():
                try:
                    continuation = parse_flow_signal(cont_file)
                except (ValueError, json.JSONDecodeError) as exc:
                    # Malformed continuation — preserve + fail closed
                    print(
                        f"[FLOW][WARN] Malformed continuation at {cont_file} "
                        f"({exc}) — renaming to .malformed.json",
                    )
                    rename_malformed(cont_file)
                    # Treat as task failure: cancel descendants, update gate
                    if chain_id:
                        _cancel_chain_descendants(db_path, chain_id, task_id)
                        gate_id = _find_gate_for_chain(db_path, chain_id)
                        if gate_id:
                            _update_gate_member(
                                db_path, gate_id, chain_id,
                                "failed", result_manifest_path,
                            )
                            _check_and_fire_gate(
                                db_path, planspace, gate_id, flow_id,
                                origin_refs,
                            )
                    return

        if continuation is not None:
            # Process continuation actions
            for action in continuation.actions:
                if isinstance(action, ChainAction) and action.steps:
                    # Extend the current chain
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
                    # The first new task depends on the completed task
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

                        # Update gate member leaf_task_id if gated
                        gate_id = _find_gate_for_chain(db_path, chain_id)
                        if gate_id:
                            _update_gate_member_leaf(
                                db_path, gate_id, chain_id, new_ids[-1],
                            )

                elif isinstance(action, FanoutAction) and action.branches:
                    # Submit fanout with current flow_id
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
            # No continuation — if this chain is gated, finalize
            if chain_id:
                gate_id = _find_gate_for_chain(db_path, chain_id)
                if gate_id:
                    # Only finalize if this task is the leaf
                    member_leaf = _get_gate_member_leaf(
                        db_path, gate_id, chain_id,
                    )
                    if member_leaf == task_id:
                        _update_gate_member(
                            db_path, gate_id, chain_id,
                            "complete", result_manifest_path,
                        )
                        _check_and_fire_gate(
                            db_path, planspace, gate_id, flow_id,
                            origin_refs,
                        )


# ---------------------------------------------------------------------------
# Internal helpers for reconciliation
# ---------------------------------------------------------------------------

def _read_origin_refs(planspace: Path, task_id: int) -> list[str]:
    """Read origin_refs from a task's flow context file.

    Returns [] on missing or malformed files.  On malformed, the file
    is renamed to .malformed.json and a warning is logged (corruption
    preservation), but origin_refs degradation is not task-fatal.
    """
    ctx_file = planspace / _flow_context_relpath(task_id)
    status, data = _read_flow_json(ctx_file)
    if status == "ok" and isinstance(data, dict):
        return data.get("origin_refs", [])
    return []


def _find_gate_for_chain(db_path: Path, chain_id: str) -> str | None:
    """Find the gate_id for a given chain_id, if any."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()
    cur.execute(
        "SELECT gate_id FROM gate_members WHERE chain_id = ?",
        (chain_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _get_gate_member_leaf(
    db_path: Path, gate_id: str, chain_id: str,
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


def _update_gate_member_leaf(
    db_path: Path, gate_id: str, chain_id: str, new_leaf_task_id: int,
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


def _cancel_chain_descendants(
    db_path: Path, chain_id: str, after_task_id: int,
) -> None:
    """Mark all pending tasks in chain_id with id > after_task_id as cancelled."""
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


def _update_gate_member(
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


def _check_and_fire_gate(
    db_path: Path,
    planspace: Path,
    gate_id: str,
    flow_id: str,
    origin_refs: list[str],
) -> None:
    """Check if all gate members are terminal. If so, fire the gate.

    1. Query all gate_members for this gate_id
    2. If all are "complete" or "failed":
       a. Build gate aggregate manifest
       b. Write it to gate's aggregate_manifest_path
       c. Update gate status to "ready"
       d. If gate has synthesis task configured:
          - Submit synthesis task
          - Update gate status to "fired", set fired_task_id
    3. If failure_policy="block" and any member is "failed":
       - Set gate status to "blocked"
       - Do NOT submit synthesis
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()

    # Read gate row
    cur.execute("SELECT * FROM gates WHERE gate_id = ?", (gate_id,))
    gate_row = cur.fetchone()
    if gate_row is None:
        conn.close()
        return
    gate = dict(gate_row)

    # Read all members
    cur.execute(
        "SELECT * FROM gate_members WHERE gate_id = ? ORDER BY chain_id",
        (gate_id,),
    )
    members = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Check if all members are terminal
    terminal_statuses = {"complete", "failed"}
    all_terminal = all(m["status"] in terminal_statuses for m in members)
    if not all_terminal:
        return

    any_failed = any(m["status"] == "failed" for m in members)

    # Check failure_policy="block" — if any failed, block the gate
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

    # Build aggregate manifest
    member_entries = []
    for m in members:
        member_entries.append({
            "chain_id": m["chain_id"],
            "slot_label": m["slot_label"],
            "status": m["status"],
            "result_manifest_path": m["result_manifest_path"],
        })

    aggregate = build_gate_aggregate_manifest(
        gate_id=gate_id,
        flow_id=flow_id,
        mode=gate["mode"],
        failure_policy=gate["failure_policy"],
        origin_refs=origin_refs,
        members=member_entries,
    )

    # Write aggregate manifest
    agg_relpath = _gate_aggregate_relpath(gate_id)
    agg_file = planspace / agg_relpath
    write_json(agg_file, aggregate)

    # Update gate status to "ready" and set aggregate path
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

    # If synthesis task configured, submit it and update gate to "fired"
    if gate["synthesis_task_type"]:
        syn_chain_id = _new_chain_id()
        syn_instance_id = _new_instance_id()

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
            result_manifest_path=_result_manifest_relpath(0),  # placeholder
        )

        # Update paths now that we know the task id
        syn_ctx_path = _flow_context_relpath(syn_tid)
        syn_cont_path = _continuation_relpath(syn_tid)
        syn_res_path = _result_manifest_relpath(syn_tid)

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

        # Write flow context for synthesis task
        _write_flow_context(
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
