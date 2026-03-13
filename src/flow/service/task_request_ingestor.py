"""Task-request ingestion — reads agent-emitted task-request files and submits.

Agents write task-request JSON files to paths like:
    artifacts/signals/task-requests-proposal-NN.json
    artifacts/signals/task-requests-impl-NN.json
    artifacts/signals/task-requests-micro-NN.json
    artifacts/signals/task-requests-reexplore-NN.json
    artifacts/signals/task-requests-coord-NN.json

This module closes the loop by reading those files and submitting the
requested tasks into the queue with flow metadata.  The task_dispatcher.py
poll loop handles actual dispatch.

Supports both legacy (v1) single-task JSON and v2 flow declarations.
Legacy requests are submitted as single-step chains; v2 declarations
are fully processed (chains and fanouts).
"""

from __future__ import annotations

from pathlib import Path

from flow.service.flow_signal_parser import (
    find_first_section_scope,
    ingest_task_requests as _ingest_task_requests,
    parse_signal_file as _parse_signal_file,
)
from flow.types.schema import (
    ChainAction,
    FanoutAction,
)
from flow.engine.flow_submitter import new_flow_id

from containers import Services

def ingest_task_requests(signal_path: Path) -> list[dict]:
    """Read and parse a task-request signal file.

    Supports both a single JSON object and JSONL (one object per line),
    as well as v2 flow envelopes.  Parsing is delegated to
    ``flow_schema.parse_flow_signal`` which normalizes all formats
    into a ``FlowDeclaration``.

    For legacy (v1) declarations the extracted task dicts are returned
    for dispatch.  For v2 declarations, validation is performed and a
    warning is logged — dispatch is not yet supported (Task 6).

    Fail-closed: on parse errors, renames to .malformed.json + logs
    warning and returns empty list.  Entries missing ``task_type`` are
    skipped with a warning.  The signal file is deleted after a
    successful read to prevent re-processing.

    .. deprecated::
        Use :func:`ingest_and_submit` instead, which submits tasks into
        the queue with flow metadata rather than returning raw dicts.
    """
    return _ingest_task_requests(signal_path)


def _submit_chain_action(
    action: ChainAction,
    planspace: Path,
    db_path: Path,
    submitted_by: str,
    submit_chain,
    *,
    flow_id: str | None,
    chain_id: str | None,
    declared_by_task_id: int | None,
    refs: list[str],
) -> list[int]:
    """Submit a chain action and return task IDs."""
    if not action.steps:
        return []
    token: str | None = None
    section_scope = find_first_section_scope(action.steps)
    if section_scope:
        token = Services.freshness().compute(planspace, section_scope)
    return submit_chain(
        db_path,
        submitted_by,
        action.steps,
        flow_id=flow_id,
        chain_id=chain_id,
        declared_by_task_id=declared_by_task_id,
        origin_refs=refs,
        planspace=planspace,
        freshness_token=token,
    )


def _submit_fanout_action(
    action: FanoutAction,
    db_path: Path,
    submitted_by: str,
    submit_fanout,
    *,
    flow_id: str | None,
    declared_by_task_id: int | None,
    refs: list[str],
    planspace: Path,
) -> None:
    """Submit a fanout action (branches + optional gate)."""
    if not action.branches:
        return
    fanout_flow_id = flow_id or new_flow_id()
    submit_fanout(
        db_path,
        submitted_by,
        action.branches,
        flow_id=fanout_flow_id,
        declared_by_task_id=declared_by_task_id,
        origin_refs=refs,
        gate=action.gate,
        planspace=planspace,
    )


def ingest_and_submit(
    planspace: Path,
    db_path: Path,
    submitted_by: str,
    signal_path: Path,
    *,
    flow_id: str | None = None,
    chain_id: str | None = None,
    declared_by_task_id: int | None = None,
    origin_refs: list[str] | None = None,
) -> list[int]:
    """Submit agent-emitted task requests into the queue with flow metadata.

    Reads task-request JSON files, parses them via ``parse_flow_signal``,
    and submits them through ``submit_chain``/``submit_fanout`` from
    flow_facade.py.  The task_dispatcher.py poll loop handles actual dispatch.

    For legacy v1 tasks: each is submitted as a single-step chain.
    For v2 declarations: chain/fanout actions are fully processed.

    Flow metadata (flow_id, chain_id, origin_refs) is propagated from
    the calling context so submitted tasks carry provenance.

    Returns list of submitted task IDs.
    """
    decl = _parse_signal_file(signal_path)
    if decl is None:
        return []

    # Lazy import to break circular dependency:
    # task_dispatcher → flow_facade → reconciler → plan_executor
    # → section_pipeline → section_reexplorer → task_request_ingestor → flow_facade
    from flow.service.flow_facade import (
        submit_chain,
        submit_fanout,
    )

    all_task_ids: list[int] = []
    refs = origin_refs or []

    for action in decl.actions:
        if isinstance(action, ChainAction):
            all_task_ids.extend(_submit_chain_action(
                action, planspace, db_path, submitted_by, submit_chain,
                flow_id=flow_id, chain_id=chain_id,
                declared_by_task_id=declared_by_task_id, refs=refs,
            ))
        elif isinstance(action, FanoutAction):
            _submit_fanout_action(
                action, db_path, submitted_by, submit_fanout,
                flow_id=flow_id, declared_by_task_id=declared_by_task_id,
                refs=refs, planspace=planspace,
            )
        else:
            Services.logger().log(f"  task_ingestion: WARNING — unknown action type "
                f"{type(action).__name__}, skipping")

    if all_task_ids:
        Services.logger().log(f"  task_ingestion: submitted {len(all_task_ids)} tasks "
            f"to queue (submitted_by={submitted_by})")

    return all_task_ids
