"""Flow system public API — re-exports for external consumers.

Provides a single import point for flow submission, reconciliation,
and context construction.
"""

from __future__ import annotations

from flow.repository.context import (
    build_flow_context,
    read_flow_json as _read_flow_json,
    write_dispatch_prompt,
)
from flow.engine.reconciler import (
    build_gate_aggregate_manifest,
    build_result_manifest,
    reconcile_task_completion,
)
from flow.repository.gate_operations import (
    read_origin_refs as _read_origin_refs,
)
from flow.engine.submitter import (
    submit_chain,
    submit_fanout,
)
