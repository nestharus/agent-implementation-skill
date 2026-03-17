"""Component tests for ROAL-scoped verification chain builder."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from containers import ArtifactIOService
from flow.types.schema import TaskSpec
from orchestrator.path_registry import PathRegistry
from verification.service.chain_builder import (
    VerificationChainBuilder,
    _P1_TEST_CAP,
    _P2_TEST_CAP,
    _resolve_posture,
)
from verification.service.verification_context import write_verification_context
from risk.types import PostureProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_builder() -> tuple[VerificationChainBuilder, MagicMock]:
    logger = MagicMock()
    logger.log = MagicMock()
    builder = VerificationChainBuilder(
        artifact_io=ArtifactIOService(),
        logger=logger,
    )
    return builder, logger


def _setup_planspace(tmp_path: Path) -> Path:
    """Create a minimal planspace with required directories."""
    planspace = tmp_path
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    return planspace


def _setup_consequence_notes(
    planspace: Path, to_section: str, from_sections: list[str],
) -> list[Path]:
    """Write dummy consequence notes targeting *to_section*."""
    paths = PathRegistry(planspace)
    notes_dir = paths.notes_dir()
    notes_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for src in from_sections:
        note = notes_dir / f"from-{src}-to-{to_section}.md"
        note.write_text(
            f"Consequence note from section {src} to {to_section}.",
            encoding="utf-8",
        )
        result.append(note)
    return result


def _setup_risk_assessment(planspace: Path, section_number: str) -> Path:
    """Write a stub risk assessment JSON for the section."""
    paths = PathRegistry(planspace)
    risk_path = paths.risk_assessment(f"section-{section_number}")
    risk_path.parent.mkdir(parents=True, exist_ok=True)
    risk_path.write_text(
        json.dumps({"assessment_id": f"ra-{section_number}", "dominant_risks": []}),
        encoding="utf-8",
    )
    return risk_path


# ---------------------------------------------------------------------------
# _resolve_posture
# ---------------------------------------------------------------------------

def test_resolve_posture_by_value() -> None:
    assert _resolve_posture("P0") == PostureProfile.P0_DIRECT
    assert _resolve_posture("P3") == PostureProfile.P3_GUARDED


def test_resolve_posture_by_name() -> None:
    assert _resolve_posture("P1_LIGHT") == PostureProfile.P1_LIGHT


def test_resolve_posture_unknown_defaults_p2() -> None:
    assert _resolve_posture("unknown") == PostureProfile.P2_STANDARD


# ---------------------------------------------------------------------------
# P4 — locked (no verification)
# ---------------------------------------------------------------------------

def test_p4_returns_empty_chain(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, logger = _make_builder()

    chain = builder.build_verification_chain(
        section_number="05",
        planspace=planspace,
        roal_posture="P4",
        has_incoming_consequence_notes=False,
    )

    assert chain == []
    logger.log.assert_called_once()
    assert "P4" in logger.log.call_args[0][0]


# ---------------------------------------------------------------------------
# P0 — minimal (structural imports_only only)
# ---------------------------------------------------------------------------

def test_p0_chain_has_single_structural_task(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _logger = _make_builder()

    chain = builder.build_verification_chain(
        section_number="01",
        planspace=planspace,
        roal_posture="P0",
        has_incoming_consequence_notes=False,
    )

    assert len(chain) == 1
    assert chain[0].task_type == "verification.structural"
    assert chain[0].concern_scope == "section-01"
    assert chain[0].priority == "low"

    # Verify the context file was written with imports_only scope
    ctx = json.loads(Path(chain[0].payload_path).read_text(encoding="utf-8"))
    assert ctx["scope"] == "imports_only"
    assert ctx["section_number"] == "01"


def test_p0_no_behavioral_even_with_consequence_notes(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    _setup_consequence_notes(planspace, "01", ["02"])
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="01",
        planspace=planspace,
        roal_posture="P0",
        has_incoming_consequence_notes=True,
    )

    task_types = [t.task_type for t in chain]
    assert "testing.behavioral" not in task_types
    assert "verification.integration" not in task_types


# ---------------------------------------------------------------------------
# P1 — relaxed (structural full; behavioral conditional on notes)
# ---------------------------------------------------------------------------

def test_p1_no_behavioral_without_notes(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="02",
        planspace=planspace,
        roal_posture="P1",
        has_incoming_consequence_notes=False,
    )

    assert len(chain) == 1
    assert chain[0].task_type == "verification.structural"
    ctx = json.loads(Path(chain[0].payload_path).read_text(encoding="utf-8"))
    assert ctx["scope"] == "full"


def test_p1_includes_behavioral_with_notes(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    _setup_consequence_notes(planspace, "02", ["03"])
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="02",
        planspace=planspace,
        roal_posture="P1",
        has_incoming_consequence_notes=True,
    )

    assert len(chain) == 2
    assert chain[0].task_type == "verification.structural"
    assert chain[1].task_type == "testing.behavioral"

    # Verify 2-test cap
    ctx = json.loads(Path(chain[1].payload_path).read_text(encoding="utf-8"))
    assert ctx["max_tests"] == _P1_TEST_CAP


def test_p1_no_integration(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="02",
        planspace=planspace,
        roal_posture="P1",
        has_incoming_consequence_notes=True,
    )

    task_types = [t.task_type for t in chain]
    assert "verification.integration" not in task_types


# ---------------------------------------------------------------------------
# P2 — standard (structural + integration + behavioral)
# ---------------------------------------------------------------------------

def test_p2_full_chain(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    _setup_consequence_notes(planspace, "03", ["01", "04"])
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="03",
        planspace=planspace,
        roal_posture="P2",
        has_incoming_consequence_notes=True,
    )

    assert len(chain) == 3
    assert chain[0].task_type == "verification.structural"
    assert chain[1].task_type == "verification.integration"
    assert chain[2].task_type == "testing.behavioral"

    # All normal priority
    for task in chain:
        assert task.priority == "normal"


def test_p2_integration_scope_is_consequence_notes(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    _setup_consequence_notes(planspace, "03", ["01"])
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="03",
        planspace=planspace,
        roal_posture="P2",
        has_incoming_consequence_notes=True,
    )

    integration = [t for t in chain if t.task_type == "verification.integration"][0]
    ctx = json.loads(Path(integration.payload_path).read_text(encoding="utf-8"))
    assert ctx["scope"] == "consequence_notes"
    assert len(ctx["consequence_note_paths"]) == 1


def test_p2_behavioral_5_test_cap(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="03",
        planspace=planspace,
        roal_posture="P2",
        has_incoming_consequence_notes=False,
    )

    behavioral = [t for t in chain if t.task_type == "testing.behavioral"][0]
    ctx = json.loads(Path(behavioral.payload_path).read_text(encoding="utf-8"))
    assert ctx["max_tests"] == _P2_TEST_CAP
    assert "risk_context_path" not in ctx


def test_p2_behavioral_no_codemap_refresh(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="03",
        planspace=planspace,
        roal_posture="P2",
        has_incoming_consequence_notes=False,
    )

    behavioral = [t for t in chain if t.task_type == "testing.behavioral"][0]
    ctx = json.loads(Path(behavioral.payload_path).read_text(encoding="utf-8"))
    assert "codemap_refresh" not in ctx


# ---------------------------------------------------------------------------
# P3 — guarded (expanded integration, risk context, codemap refresh)
# ---------------------------------------------------------------------------

def test_p3_chain_with_expanded_scope(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    _setup_consequence_notes(planspace, "04", ["05", "06"])
    _setup_risk_assessment(planspace, "04")
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="04",
        planspace=planspace,
        roal_posture="P3",
        has_incoming_consequence_notes=True,
    )

    assert len(chain) == 3
    assert chain[0].task_type == "verification.structural"
    assert chain[1].task_type == "verification.integration"
    assert chain[2].task_type == "testing.behavioral"

    # All high priority
    for task in chain:
        assert task.priority == "high"


def test_p3_integration_expanded_scope(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="04",
        planspace=planspace,
        roal_posture="P3",
        has_incoming_consequence_notes=False,
    )

    integration = [t for t in chain if t.task_type == "verification.integration"][0]
    ctx = json.loads(Path(integration.payload_path).read_text(encoding="utf-8"))
    assert ctx["scope"] == "expanded"


def test_p3_behavioral_gets_risk_context(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    risk_path = _setup_risk_assessment(planspace, "04")
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="04",
        planspace=planspace,
        roal_posture="P3",
        has_incoming_consequence_notes=False,
    )

    behavioral = [t for t in chain if t.task_type == "testing.behavioral"][0]
    ctx = json.loads(Path(behavioral.payload_path).read_text(encoding="utf-8"))
    assert ctx["risk_context_path"] == str(risk_path)
    assert ctx["codemap_refresh"] is True
    assert ctx["max_tests"] == _P2_TEST_CAP


def test_p3_behavioral_without_risk_file_omits_path(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    # Do NOT create risk assessment file
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="04",
        planspace=planspace,
        roal_posture="P3",
        has_incoming_consequence_notes=False,
    )

    behavioral = [t for t in chain if t.task_type == "testing.behavioral"][0]
    ctx = json.loads(Path(behavioral.payload_path).read_text(encoding="utf-8"))
    assert "risk_context_path" not in ctx


# ---------------------------------------------------------------------------
# TaskSpec shape
# ---------------------------------------------------------------------------

def test_all_task_specs_have_payload_paths(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    _setup_consequence_notes(planspace, "10", ["11"])
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="10",
        planspace=planspace,
        roal_posture="P2",
        has_incoming_consequence_notes=True,
    )

    for task in chain:
        assert task.payload_path, f"{task.task_type} missing payload_path"
        assert Path(task.payload_path).exists(), (
            f"{task.task_type}: payload path does not exist: {task.payload_path}"
        )
        assert task.concern_scope == "section-10"


def test_concern_scope_uses_section_prefix(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    builder, _ = _make_builder()

    chain = builder.build_verification_chain(
        section_number="07",
        planspace=planspace,
        roal_posture="P0",
        has_incoming_consequence_notes=False,
    )

    assert chain[0].concern_scope == "section-07"


# ---------------------------------------------------------------------------
# write_verification_context
# ---------------------------------------------------------------------------

def test_write_verification_context_round_trips(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    ctx_path = write_verification_context(
        ArtifactIOService(),
        planspace,
        section_number="08",
        task_type="structural",
        scope="imports_only",
    )

    assert ctx_path.exists()
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["section_number"] == "08"
    assert data["task_type"] == "structural"
    assert data["scope"] == "imports_only"
    assert "codemap_path" in data
    assert "section_spec_path" in data
    assert "problem_frame_path" in data
    assert "proposal_state_path" in data
    assert "impl_modified_path" in data


def test_write_verification_context_optional_fields(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    ctx_path = write_verification_context(
        ArtifactIOService(),
        planspace,
        section_number="09",
        task_type="behavioral",
        scope="full",
        consequence_note_paths=["/tmp/note1.md"],
        risk_context_path="/tmp/risk.json",
        codemap_refresh=True,
        max_tests=5,
    )

    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert data["consequence_note_paths"] == ["/tmp/note1.md"]
    assert data["risk_context_path"] == "/tmp/risk.json"
    assert data["codemap_refresh"] is True
    assert data["max_tests"] == 5


def test_write_verification_context_omits_none_optionals(tmp_path: Path) -> None:
    planspace = _setup_planspace(tmp_path)
    ctx_path = write_verification_context(
        ArtifactIOService(),
        planspace,
        section_number="09",
        task_type="structural",
        scope="full",
    )

    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert "consequence_note_paths" not in data
    assert "risk_context_path" not in data
    assert "codemap_refresh" not in data
    assert "max_tests" not in data


# ---------------------------------------------------------------------------
# PathRegistry.verification_context accessor
# ---------------------------------------------------------------------------

def test_path_registry_verification_context(tmp_path: Path) -> None:
    paths = PathRegistry(tmp_path)
    result = paths.verification_context("05", "structural")
    assert result == (
        tmp_path / "artifacts" / "verification"
        / "section-05-structural-context.json"
    )
