"""Flow system public API — re-exports for external consumers.

Provides a single import point for flow-related standalone helpers.
"""

from __future__ import annotations

from flow.repository.flow_context_store import (
    write_dispatch_prompt,
)


def submit_chain(env, steps, **kwargs):
    """Submit a linear chain of tasks via the DI container."""
    from containers import Services
    return Services.flow_ingestion().submit_chain(env, steps, **kwargs)


def submit_fanout(env, branches, **kwargs):
    """Submit parallel branches via the DI container."""
    from containers import Services
    return Services.flow_ingestion().submit_fanout(env, branches, **kwargs)


def build_flow_context(planspace, flow_context_path=None, **kwargs):
    """Build a flow context via the DI container."""
    from containers import Services
    from flow.repository.flow_context_store import FlowContextStore
    return FlowContextStore(Services.artifact_io()).build_flow_context(
        planspace, flow_context_path=flow_context_path, **kwargs,
    )


def _make_reconciler():
    """Create a Reconciler wired from the DI container."""
    from containers import Services
    from flow.engine.flow_submitter import FlowSubmitter
    from flow.engine.reconciler import Reconciler
    from flow.repository.flow_context_store import FlowContextStore
    from flow.repository.gate_repository import GateRepository
    from implementation.service.traceability_writer import TraceabilityWriter
    artifact_io = Services.artifact_io()
    flow_context_store = FlowContextStore(artifact_io)
    flow_submitter = FlowSubmitter(
        freshness=Services.freshness(),
        flow_context_store=flow_context_store,
    )
    gate_repository = GateRepository(artifact_io)
    return Reconciler(
        artifact_io=artifact_io,
        research=Services.research(),
        prompt_guard=Services.prompt_guard(),
        flow_submitter=flow_submitter,
        gate_repository=gate_repository,
        traceability_writer=TraceabilityWriter(
            artifact_io=Services.artifact_io(),
            hasher=Services.hasher(),
            logger=Services.logger(),
            section_alignment=Services.section_alignment(),
        ),
    )


def reconcile_task_completion(db_path, planspace, task_id, status, output_path, **kwargs):
    """Reconcile a task completion via the DI container."""
    return _make_reconciler().reconcile_task_completion(
        db_path, planspace, task_id, status, output_path, **kwargs,
    )
