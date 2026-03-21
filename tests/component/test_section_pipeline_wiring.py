"""Tests for SectionPipeline factory wiring and phase injection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from dependency_injector import providers

from conftest import (
    NoOpChangeTracker,
    NoOpCommunicator,
    NoOpFreshness,
    NoOpPipelineControl,
    NoOpSectionAlignment,
    StubPolicies,
    WritingGuard,
    make_dispatcher,
)
from conftest import NoOpFlow
from containers import CrossSectionService, Services
from implementation.repository.roal_index import RoalIndex
from implementation.service.risk_artifacts import RiskArtifacts
from implementation.service.section_reexplorer import SectionReexplorer
from orchestrator.engine.section_pipeline import SectionPipeline, build_section_pipeline
from proposal.engine.proposal_phase import ProposalPhase
from implementation.engine.implementation_phase import ImplementationPhase
from reconciliation.engine.reconciliation_phase import ReconciliationPhase
from reconciliation.engine.cross_section_reconciler import CrossSectionReconciler
from reconciliation.repository.queue import Queue
from reconciliation.repository.results import Results
from reconciliation.service.adjudicator import Adjudicator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _NoopCrossSection(CrossSectionService):
    def persist_decision(self, *_args, **_kwargs):
        return None

    def extract_section_summary(self, path):
        return ""

    def write_consequence_note(self, *_args, **_kwargs):
        return None


def _override_all_services():
    """Override all service providers needed by build_section_pipeline()."""
    Services.dispatcher.override(providers.Object(make_dispatcher(lambda *a, **k: "")))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.pipeline_control.override(providers.Object(NoOpPipelineControl()))
    Services.communicator.override(providers.Object(NoOpCommunicator()))
    Services.flow_ingestion.override(providers.Object(NoOpFlow()))
    Services.cross_section.override(providers.Object(_NoopCrossSection()))
    Services.change_tracker.override(providers.Object(NoOpChangeTracker()))
    Services.freshness.override(providers.Object(NoOpFreshness()))
    Services.section_alignment.override(providers.Object(NoOpSectionAlignment()))
    Services.policies.override(providers.Object(StubPolicies()))


def _reset_all_services():
    """Reset all overridden service providers."""
    Services.dispatcher.reset_override()
    Services.prompt_guard.reset_override()
    Services.pipeline_control.reset_override()
    Services.communicator.reset_override()
    Services.flow_ingestion.reset_override()
    Services.cross_section.reset_override()
    Services.change_tracker.reset_override()
    Services.freshness.reset_override()
    Services.section_alignment.reset_override()
    Services.policies.reset_override()


# ---------------------------------------------------------------------------
# Test: build_section_pipeline returns correct type
# ---------------------------------------------------------------------------


def test_build_section_pipeline_returns_section_pipeline_instance():
    _override_all_services()
    try:
        pipeline = build_section_pipeline()
        assert isinstance(pipeline, SectionPipeline)
    finally:
        _reset_all_services()


# ---------------------------------------------------------------------------
# Test: build_section_pipeline wires all 18 collaborators
# ---------------------------------------------------------------------------

_ALL_COLLABORATOR_ATTRS = [
    "_logger",
    "_artifact_io",
    "_pipeline_control",
    "_implementation_cycle",
    "_intent_initializer",
    "_microstrategy_generator",
    "_recurrence_emitter",
    "_triage_orchestrator",
    "_cross_section_reconciler",
    "_completion_handler",
    "_excerpt_extractor",
    "_problem_frame_gate",
    "_proposal_cycle",
    "_readiness_gate",
    "_tool_surface_writer",
    "_tool_validator",
    "_tool_bridge",
    "_readiness_resolver",
]


def test_build_section_pipeline_wires_all_collaborators():
    _override_all_services()
    try:
        pipeline = build_section_pipeline()
        for attr in _ALL_COLLABORATOR_ATTRS:
            value = getattr(pipeline, attr, None)
            assert value is not None, (
                f"SectionPipeline.{attr} is None — factory failed to wire it"
            )
    finally:
        _reset_all_services()


# ---------------------------------------------------------------------------
# Test: ProposalPhase accepts and uses injected pipeline
# ---------------------------------------------------------------------------


def test_proposal_phase_uses_injected_pipeline():
    mock_pipeline = MagicMock(spec=SectionPipeline)
    phase = ProposalPhase(
        logger_svc=MagicMock(),
        artifact_io=MagicMock(),
        communicator=MagicMock(),
        pipeline_control=MagicMock(),
        policies=MagicMock(),
        risk_assessment=MagicMock(),
        change_tracker=NoOpChangeTracker(),
        roal_index=MagicMock(spec=RoalIndex),
        section_reexplorer=MagicMock(spec=SectionReexplorer),
        section_pipeline=mock_pipeline,
    )
    assert phase._section_pipeline is mock_pipeline


# ---------------------------------------------------------------------------
# Test: ImplementationPhase accepts and uses injected pipeline
# ---------------------------------------------------------------------------


def test_implementation_phase_uses_injected_pipeline():
    mock_pipeline = MagicMock(spec=SectionPipeline)
    phase = ImplementationPhase(
        artifact_io=MagicMock(),
        change_tracker=NoOpChangeTracker(),
        communicator=MagicMock(),
        logger=MagicMock(),
        pipeline_control=MagicMock(),
        risk_assessment=MagicMock(),
        risk_artifacts=MagicMock(spec=RiskArtifacts),
        roal_index=MagicMock(spec=RoalIndex),
        section_pipeline=mock_pipeline,
    )
    assert phase._section_pipeline is mock_pipeline


# ---------------------------------------------------------------------------
# Test: ReconciliationPhase accepts and uses injected pipeline
# ---------------------------------------------------------------------------


def test_reconciliation_phase_uses_injected_pipeline():
    mock_pipeline = MagicMock(spec=SectionPipeline)
    phase = ReconciliationPhase(
        logger=MagicMock(),
        artifact_io=MagicMock(),
        pipeline_control=MagicMock(),
        change_tracker=NoOpChangeTracker(),
        communicator=MagicMock(),
        cross_section_reconciler=MagicMock(spec=CrossSectionReconciler),
        section_pipeline=mock_pipeline,
    )
    assert phase._section_pipeline is mock_pipeline


# ---------------------------------------------------------------------------
# Test: ProposalPhase falls back to factory when no pipeline injected
# ---------------------------------------------------------------------------


def test_proposal_phase_falls_back_to_factory():
    _override_all_services()
    try:
        phase = ProposalPhase(
            logger_svc=Services.logger(),
            artifact_io=Services.artifact_io(),
            communicator=Services.communicator(),
            pipeline_control=Services.pipeline_control(),
            policies=Services.policies(),
            risk_assessment=MagicMock(),
            change_tracker=NoOpChangeTracker(),
            roal_index=RoalIndex(artifact_io=Services.artifact_io()),
            section_reexplorer=MagicMock(spec=SectionReexplorer),
        )
        assert phase._section_pipeline is not None
        assert isinstance(phase._section_pipeline, SectionPipeline)
    finally:
        _reset_all_services()


# ---------------------------------------------------------------------------
# Test: ImplementationPhase falls back to factory when no pipeline injected
# ---------------------------------------------------------------------------


def test_implementation_phase_falls_back_to_factory():
    _override_all_services()
    try:
        phase = ImplementationPhase(
            artifact_io=Services.artifact_io(),
            change_tracker=NoOpChangeTracker(),
            communicator=Services.communicator(),
            logger=Services.logger(),
            pipeline_control=Services.pipeline_control(),
            risk_assessment=MagicMock(),
            risk_artifacts=RiskArtifacts(
                artifact_io=Services.artifact_io(),
                freshness=Services.freshness(),
            ),
            roal_index=RoalIndex(artifact_io=Services.artifact_io()),
        )
        assert phase._section_pipeline is not None
        assert isinstance(phase._section_pipeline, SectionPipeline)
    finally:
        _reset_all_services()


# ---------------------------------------------------------------------------
# Test: ReconciliationPhase falls back to factory when no pipeline injected
# ---------------------------------------------------------------------------


def test_reconciliation_phase_falls_back_to_factory():
    _override_all_services()
    try:
        phase = ReconciliationPhase(
            logger=Services.logger(),
            artifact_io=Services.artifact_io(),
            pipeline_control=Services.pipeline_control(),
            change_tracker=NoOpChangeTracker(),
            communicator=Services.communicator(),
            cross_section_reconciler=MagicMock(spec=CrossSectionReconciler),
        )
        assert phase._section_pipeline is not None
        assert isinstance(phase._section_pipeline, SectionPipeline)
    finally:
        _reset_all_services()
