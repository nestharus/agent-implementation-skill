"""Component tests for research branch builder."""

from __future__ import annotations

from pathlib import Path

from containers import ArtifactIOService, Services
from flow.types.schema import BranchSpec, TaskSpec
from src.orchestrator.path_registry import PathRegistry
from src.research.engine.research_branch_builder import (
    ResearchBranchBuilder,
    ordered_ticket_ids,
)
from src.research.prompt.writers import ResearchPromptWriter
from tests.conftest import WritingGuard


def _write_common_artifacts(planspace: Path, section_number: str = "01") -> None:
    """Populate the minimal artifact files the branch builder reads."""
    paths = PathRegistry(planspace)
    paths.research_section_dir(section_number).mkdir(parents=True, exist_ok=True)
    paths.section_spec(section_number).write_text("# Section\n", encoding="utf-8")
    paths.problem_frame(section_number).write_text("# Problem\n", encoding="utf-8")


def _make_builder() -> ResearchBranchBuilder:
    artifact_io = ArtifactIOService()
    prompt_guard = WritingGuard()
    prompt_writer = ResearchPromptWriter(
        prompt_guard=prompt_guard,
        artifact_io=artifact_io,
    )
    return ResearchBranchBuilder(
        prompt_guard=prompt_guard,
        artifact_io=artifact_io,
        prompt_writer=prompt_writer,
    )


# -- ordered_ticket_ids ────────────────────────────────────────────────────────


def test_ordered_ticket_ids_from_parallel_groups() -> None:
    """Tickets listed in flow.parallel_groups appear in order."""
    plan = {
        "flow": {"parallel_groups": [["T-01", "T-02"], ["T-03"]]},
        "tickets": [
            {"ticket_id": "T-01"},
            {"ticket_id": "T-02"},
            {"ticket_id": "T-03"},
        ],
    }
    assert ordered_ticket_ids(plan) == ["T-01", "T-02", "T-03"]


def test_ordered_ticket_ids_appends_ungrouped() -> None:
    """Tickets not in parallel_groups are appended from the tickets list."""
    plan = {
        "flow": {"parallel_groups": [["T-01"]]},
        "tickets": [
            {"ticket_id": "T-01"},
            {"ticket_id": "T-02"},
        ],
    }
    assert ordered_ticket_ids(plan) == ["T-01", "T-02"]


def test_ordered_ticket_ids_empty_plan() -> None:
    """Empty plan yields empty list."""
    assert ordered_ticket_ids({}) == []
    assert ordered_ticket_ids({"tickets": [], "flow": {}}) == []


def test_ordered_ticket_ids_deduplicates() -> None:
    """Duplicate ticket IDs in both parallel_groups and tickets appear once."""
    plan = {
        "flow": {"parallel_groups": [["T-01", "T-01"]]},
        "tickets": [{"ticket_id": "T-01"}],
    }
    assert ordered_ticket_ids(plan) == ["T-01"]


def test_ordered_ticket_ids_skips_invalid_groups() -> None:
    """Non-list entries in parallel_groups are skipped."""
    plan = {
        "flow": {"parallel_groups": ["not-a-list", ["T-01"]]},
        "tickets": [],
    }
    assert ordered_ticket_ids(plan) == ["T-01"]


def test_ordered_ticket_ids_single_ticket() -> None:
    """Single ticket plan works."""
    plan = {
        "tickets": [{"ticket_id": "T-01"}],
    }
    assert ordered_ticket_ids(plan) == ["T-01"]


# -- build_branch: web type ────────────────────────────────────────────────────


def test_build_web_branch(tmp_path: Path) -> None:
    """Web research ticket produces a BranchSpec with chain_ref."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"ticket_id": "T-01", "research_type": "web", "questions": ["q1"]}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=None,
        ticket=ticket,
        ticket_index=0,
    )

    assert branch is not None
    assert isinstance(branch, BranchSpec)
    assert branch.label == "T-01"
    assert branch.chain_ref == "research_ticket_package"
    assert "concern_scope" in branch.args
    assert branch.args["concern_scope"] == "section-01"


# -- build_branch: code type ──────────────────────────────────────────────────


def test_build_code_branch(tmp_path: Path) -> None:
    """Code research ticket produces a BranchSpec with scan payload."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"ticket_id": "T-02", "research_type": "code", "questions": ["q2"]}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=None,
        ticket=ticket,
        ticket_index=1,
    )

    assert branch is not None
    assert branch.label == "T-02"
    assert branch.chain_ref == "research_code_ticket_package"
    assert "scan_payload_path" in branch.args
    assert "payload_path" in branch.args


# -- build_branch: both type ──────────────────────────────────────────────────


def test_build_both_branch(tmp_path: Path) -> None:
    """'both' research type produces a BranchSpec with sequential steps."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"ticket_id": "T-03", "research_type": "both", "questions": ["q3"]}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=None,
        ticket=ticket,
        ticket_index=2,
    )

    assert branch is not None
    assert branch.label == "T-03"
    assert len(branch.steps) == 3
    assert all(isinstance(s, TaskSpec) for s in branch.steps)
    # First step is web research, middle is scan, last is final research
    assert branch.steps[0].task_type == "research.domain_ticket"
    assert branch.steps[1].task_type == "scan.explore"
    assert branch.steps[2].task_type == "research.domain_ticket"


def test_build_user_branch(tmp_path: Path) -> None:
    """User research ticket produces an awaiting-input task branch."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"ticket_id": "T-04", "research_type": "user", "questions": ["Need a user tradeoff decision?"]}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=None,
        ticket=ticket,
        ticket_index=4,
    )

    assert branch is not None
    assert branch.label == "T-04"
    assert len(branch.steps) == 1
    assert branch.steps[0].task_type == "research.user_input"
    prompt_path = Path(branch.steps[0].payload_path)
    assert prompt_path.exists()
    assert prompt_path.with_name(prompt_path.name.replace("-prompt.md", "-spec.json")).exists()


# -- build_branch: unknown type returns None ──────────────────────────────────


def test_build_branch_unknown_type_returns_none(tmp_path: Path) -> None:
    """Unknown research_type returns None."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"ticket_id": "T-99", "research_type": "unknown_type"}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=None,
        ticket=ticket,
        ticket_index=0,
    )

    assert branch is None


# -- build_branch: default ticket_id when missing ────────────────────────────


def test_build_branch_default_ticket_id(tmp_path: Path) -> None:
    """Missing ticket_id gets a generated default like T-00."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"research_type": "web", "questions": ["q1"]}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=None,
        ticket=ticket,
        ticket_index=0,
    )

    assert branch is not None
    assert branch.label == "T-00"


# -- emit_not_researchable_signals ─────────────────────────────────────────────


def test_emit_not_researchable_signals_writes_blocker_files(tmp_path: Path) -> None:
    """Non-researchable items produce blocker signal JSON files."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    builder = _make_builder()
    items = [
        {"question": "Why is the sky blue?", "reason": "Out of scope"},
        {"question": "What is love?", "reason": "Not code-related"},
    ]
    builder.emit_not_researchable_signals("01", planspace, items)

    signals_dir = PathRegistry(planspace).signals_dir()
    signal_files = sorted(signals_dir.glob("section-01-research-blocker-*.json"))
    assert len(signal_files) == 2


def test_emit_not_researchable_signals_empty_list(tmp_path: Path) -> None:
    """Empty items list writes no signal files."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    builder = _make_builder()
    builder.emit_not_researchable_signals("01", planspace, [])

    signals_dir = PathRegistry(planspace).signals_dir()
    signal_files = list(signals_dir.glob("section-01-research-blocker-*.json"))
    assert len(signal_files) == 0


# -- build_branch with codespace ──────────────────────────────────────────────


def test_build_code_branch_with_codespace(tmp_path: Path) -> None:
    """Code branch with codespace includes codespace reference in scan prompt."""
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    codespace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _write_common_artifacts(planspace)

    builder = _make_builder()
    ticket = {"ticket_id": "T-04", "research_type": "code", "questions": ["q4"]}
    branch = builder.build_branch(
        section_number="01",
        planspace=planspace,
        codespace=codespace,
        ticket=ticket,
        ticket_index=3,
    )

    assert branch is not None
    assert branch.chain_ref == "research_code_ticket_package"
    # Verify the scan prompt file was written and contains codespace reference
    scan_path = Path(branch.args["scan_payload_path"])
    assert scan_path.exists()
    content = scan_path.read_text(encoding="utf-8")
    assert str(codespace) in content
