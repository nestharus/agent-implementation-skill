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
from flow.types.context import FlowEnvelope

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
    env: FlowEnvelope,
    submit_chain,
    *,
    chain_id: str | None,
) -> list[int]:
    """Submit a chain action and return task IDs."""
    from dataclasses import replace as _replace

    if not action.steps:
        return []
    token: str | None = None
    section_scope = find_first_section_scope(action.steps)
    if section_scope and env.planspace is not None:
        token = Services.freshness().compute(env.planspace, section_scope)
    return submit_chain(
        _replace(env, freshness_token=token) if token else env,
        action.steps,
        chain_id=chain_id,
    )


def _submit_fanout_action(
    action: FanoutAction,
    env: FlowEnvelope,
    submit_fanout,
) -> None:
    """Submit a fanout action (branches + optional gate)."""
    from dataclasses import replace as _replace

    if not action.branches:
        return
    fanout_env = _replace(env, flow_id=env.flow_id or new_flow_id())
    submit_fanout(
        fanout_env,
        action.branches,
        gate=action.gate,
    )


def ingest_and_submit(
    planspace: Path,
    submitted_by: str,
    signal_path: Path,
    *,
    db_path: Path | None = None,
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
    if db_path is None:
        from orchestrator.path_registry import PathRegistry
        db_path = PathRegistry(planspace).run_db()
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

    env = FlowEnvelope(
        db_path=db_path,
        submitted_by=submitted_by,
        flow_id=flow_id,
        declared_by_task_id=declared_by_task_id,
        origin_refs=origin_refs or [],
        planspace=planspace,
    )

    all_task_ids: list[int] = []

    for action in decl.actions:
        if isinstance(action, ChainAction):
            all_task_ids.extend(_submit_chain_action(
                action, env, submit_chain,
                chain_id=chain_id,
            ))
        elif isinstance(action, FanoutAction):
            _submit_fanout_action(action, env, submit_fanout)
        else:
            Services.logger().log(f"  task_ingestion: WARNING — unknown action type "
                f"{type(action).__name__}, skipping")

    if all_task_ids:
        Services.logger().log(f"  task_ingestion: submitted {len(all_task_ids)} tasks "
            f"to queue (submitted_by={submitted_by})")

    return all_task_ids
