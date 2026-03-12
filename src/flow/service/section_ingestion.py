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
from flow.engine.submitter import new_flow_id

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
    return _ingest_task_requests(signal_path, logger=Services.logger().log)


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
    task_flow.py.  The task_dispatcher.py poll loop handles actual dispatch.

    For legacy v1 tasks: each is submitted as a single-step chain.
    For v2 declarations: chain/fanout actions are fully processed.

    Flow metadata (flow_id, chain_id, origin_refs) is propagated from
    the calling context so submitted tasks carry provenance.

    Returns list of submitted task IDs.
    """
    decl = _parse_signal_file(signal_path, logger=Services.logger().log)
    if decl is None:
        return []

    # Lazy import to break circular dependency:
    # task_dispatcher → task_flow → flow_reconciler → plan_executor
    # → section_engine → reexplore → task_ingestion → task_flow
    from flow.service.task_flow import (
        submit_chain,
        submit_fanout,
    )

    all_task_ids: list[int] = []
    refs = origin_refs or []

    for action in decl.actions:
        if isinstance(action, ChainAction):
            if not action.steps:
                continue
            # P4: compute freshness token for section-scoped tasks
            token: str | None = None
            section_scope = find_first_section_scope(action.steps)
            if section_scope:
                token = Services.freshness().compute(
                    planspace, section_scope,
                )
            task_ids = submit_chain(
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
            all_task_ids.extend(task_ids)
        elif isinstance(action, FanoutAction):
            if not action.branches:
                continue
            # Fanout requires a flow_id — allocate one if not provided
            fanout_flow_id = flow_id
            if not fanout_flow_id:
                fanout_flow_id = new_flow_id()
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
            # Fanout returns gate_id not task_ids; the individual
            # branch task_ids are not directly returned here but are
            # in the DB for the dispatcher to find.
        else:
            Services.logger().log(f"  task_ingestion: WARNING — unknown action type "
                f"{type(action).__name__}, skipping")

    if all_task_ids:
        Services.logger().log(f"  task_ingestion: submitted {len(all_task_ids)} tasks "
            f"to queue (submitted_by={submitted_by})")

    return all_task_ids
