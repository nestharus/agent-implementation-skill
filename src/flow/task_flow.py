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

from flow.flow_context import (
    FlowCorruptionError,
    build_flow_context,
    gate_aggregate_relpath as _gate_aggregate_relpath,
    read_flow_json as _read_flow_json,
    result_manifest_relpath as _result_manifest_relpath,
    flow_context_relpath as _flow_context_relpath,
    write_dispatch_prompt,
)
from flow.flow_reconciler import (
    build_gate_aggregate_manifest,
    build_result_manifest,
    cancel_chain_descendants as _cancel_chain_descendants,
    check_and_fire_gate as _check_and_fire_gate,
    find_gate_for_chain as _find_gate_for_chain,
    get_gate_member_leaf as _get_gate_member_leaf,
    read_origin_refs as _read_origin_refs,
    reconcile_task_completion,
    update_gate_member as _update_gate_member,
    update_gate_member_leaf as _update_gate_member_leaf,
)
from staleness.freshness_service import compute_section_freshness
from orchestrator.path_registry import PathRegistry
from flow.flow_submitter import (
    new_chain_id as _new_chain_id,
    new_flow_id as _new_flow_id,
    new_gate_id as _new_gate_id,
    new_instance_id as _new_instance_id,
    submit_chain,
    submit_fanout,
)
