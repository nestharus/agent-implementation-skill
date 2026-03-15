"""Expansion cycle public wrappers."""

from __future__ import annotations

from pathlib import Path

from intent.engine.expansion_orchestrator import ExpansionOrchestrator


def _build_expansion_orchestrator() -> ExpansionOrchestrator:
    from containers import Services
    from intent.service.expanders import Expanders
    from intent.service.philosophy_bootstrap_state import PhilosophyBootstrapState
    from intent.service.philosophy_grounding import PhilosophyGrounding
    from intent.service.surface_registry import SurfaceRegistry

    artifact_io = Services.artifact_io()
    logger = Services.logger()
    hasher = Services.hasher()

    bootstrap_state = PhilosophyBootstrapState(artifact_io=artifact_io)
    grounding = PhilosophyGrounding(
        artifact_io=artifact_io,
        bootstrap_state=bootstrap_state,
        hasher=hasher,
        logger=logger,
    )
    expanders = Expanders(
        artifact_io=artifact_io,
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        grounding=grounding,
        logger=logger,
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        signals=Services.signals(),
        task_router=Services.task_router(),
    )
    surface_registry = SurfaceRegistry(
        artifact_io=artifact_io,
        hasher=hasher,
        logger=logger,
        signals=Services.signals(),
    )
    return ExpansionOrchestrator(
        artifact_io=artifact_io,
        expanders=expanders,
        logger=logger,
        pipeline_control=Services.pipeline_control(),
        surface_registry=surface_registry,
    )


def run_expansion_cycle(
    section_number: str,
    planspace: Path,
    codespace: Path,
    *,
    budgets: dict | None = None,
) -> dict:
    return _build_expansion_orchestrator().run_expansion_cycle(
        section_number,
        planspace,
        codespace,
        budgets=budgets,
    )


def handle_user_gate(
    section_number: str,
    planspace: Path,
    delta_result: dict,
) -> str | None:
    return _build_expansion_orchestrator().handle_user_gate(
        section_number,
        planspace,
        delta_result,
    )
