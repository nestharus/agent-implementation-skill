"""Integration tests: Staleness -> Pipeline Control boundary.

Verifies that when section input hashes change between pipeline iterations
(because an upstream artifact was modified), the staleness system detects
the change and the pipeline control requeue logic responds correctly.

Uses real filesystem I/O, real hashing — no mocking except logger/tracker.
"""

from __future__ import annotations

import json
from pathlib import Path

from containers import ChangeTrackerService, LogService
from orchestrator.path_registry import PathRegistry
from orchestrator.service.pipeline_control import PipelineControl
from orchestrator.types import Section
from staleness.service.freshness_calculator import compute_section_freshness
from staleness.service.input_hasher import section_inputs_hash


class _NoOpLogger(LogService):
    def log(self, msg: str) -> None:
        pass


class _NoOpChangeTracker(ChangeTrackerService):
    def set_flag(self, planspace) -> None:
        pass

    def make_alignment_checker(self):
        return lambda _planspace: False

    def invalidate_excerpts(self, planspace) -> None:
        pass


def _make_sections_by_num(planspace: Path) -> dict[str, Section]:
    return {
        "01": Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        ),
    }


def _make_pipeline_control() -> PipelineControl:
    return PipelineControl(
        config=None,
        logger=_NoOpLogger(),
        change_tracker=_NoOpChangeTracker(),
    )


class TestInputHashChangesOnArtifactModification:
    """Hash computation detects changes to section input artifacts."""

    def test_hash_changes_when_governance_packet_modified(
        self, planspace: Path,
    ) -> None:
        """Governance packet modification causes hash change."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)

        h1 = section_inputs_hash("01", planspace, sections)

        # Write a governance packet
        packet = {
            "section": "01",
            "candidate_problems": [{"problem_id": "PRB-001"}],
            "candidate_patterns": [],
        }
        gov_packet = paths.governance_packet("01")
        gov_packet.parent.mkdir(parents=True, exist_ok=True)
        gov_packet.write_text(json.dumps(packet), encoding="utf-8")

        h2 = section_inputs_hash("01", planspace, sections)
        assert h1 != h2, "Hash should change when governance packet is added"

    def test_hash_changes_when_governance_packet_content_changes(
        self, planspace: Path,
    ) -> None:
        """Modifying governance packet content produces a different hash."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)
        gov_packet = paths.governance_packet("01")
        gov_packet.parent.mkdir(parents=True, exist_ok=True)

        gov_packet.write_text(
            json.dumps({"section": "01", "candidate_problems": []}),
            encoding="utf-8",
        )
        h1 = section_inputs_hash("01", planspace, sections)

        gov_packet.write_text(
            json.dumps({"section": "01", "candidate_problems": [{"problem_id": "PRB-002"}]}),
            encoding="utf-8",
        )
        h2 = section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_hash_changes_when_proposal_state_modified(
        self, planspace: Path,
    ) -> None:
        """Proposal state artifact modification causes hash change."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)

        h1 = section_inputs_hash("01", planspace, sections)

        ps = paths.proposal_state("01")
        ps.parent.mkdir(parents=True, exist_ok=True)
        ps.write_text(
            json.dumps({"status": "draft", "resolved": []}),
            encoding="utf-8",
        )

        h2 = section_inputs_hash("01", planspace, sections)
        assert h1 != h2, "Hash should change when proposal-state is added"

    def test_hash_changes_when_decision_modified(
        self, planspace: Path,
    ) -> None:
        """Decision artifact modification causes hash change."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)

        h1 = section_inputs_hash("01", planspace, sections)

        decision = paths.decision_md("01")
        decision.parent.mkdir(parents=True, exist_ok=True)
        decision.write_text("# Decision\nProceed with approach A.\n")

        h2 = section_inputs_hash("01", planspace, sections)
        assert h1 != h2

    def test_hash_stable_when_no_changes(self, planspace: Path) -> None:
        """Hash is deterministic when filesystem is unchanged."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)

        spec = paths.section_spec("01")
        spec.write_text("# Section 01\nAuthentication.\n")

        h1 = section_inputs_hash("01", planspace, sections)
        h2 = section_inputs_hash("01", planspace, sections)
        assert h1 == h2


class TestFreshnessTokenDetectsChanges:
    """Freshness tokens track section artifact changes."""

    def test_freshness_changes_when_proposal_added(
        self, planspace: Path,
    ) -> None:
        paths = PathRegistry(planspace)

        f1 = compute_section_freshness(planspace, "01")

        proposal = paths.proposal("01")
        proposal.parent.mkdir(parents=True, exist_ok=True)
        proposal.write_text("# Integration Proposal\nApproach A.\n")

        f2 = compute_section_freshness(planspace, "01")
        assert f1 != f2

    def test_freshness_changes_when_governance_packet_added(
        self, planspace: Path,
    ) -> None:
        paths = PathRegistry(planspace)

        f1 = compute_section_freshness(planspace, "01")

        gov = paths.governance_packet("01")
        gov.parent.mkdir(parents=True, exist_ok=True)
        gov.write_text(json.dumps({"section": "01", "items": []}))

        f2 = compute_section_freshness(planspace, "01")
        assert f1 != f2

    def test_freshness_stable_when_unchanged(self, planspace: Path) -> None:
        f1 = compute_section_freshness(planspace, "01")
        f2 = compute_section_freshness(planspace, "01")
        assert f1 == f2

    def test_freshness_token_is_truncated(self, planspace: Path) -> None:
        token = compute_section_freshness(planspace, "01")
        assert len(token) == 16, "Freshness token should be 16 chars"


class TestPipelineControlRequeue:
    """PipelineControl.requeue_changed_sections detects stale sections."""

    def test_requeues_section_with_changed_inputs(
        self, planspace: Path,
    ) -> None:
        """Section whose inputs changed since baseline is requeued."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)
        ctrl = _make_pipeline_control()

        # Compute initial hash and persist as baseline
        initial_hash = section_inputs_hash("01", planspace, sections)
        hash_file = paths.section_inputs_hashes_dir() / "01.hash"
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(initial_hash, encoding="utf-8")

        # Modify an input artifact (add proposal)
        proposal = paths.proposal("01")
        proposal.parent.mkdir(parents=True, exist_ok=True)
        proposal.write_text("# New proposal content\n")

        completed = {"01"}
        queue: list[str] = []
        requeued = ctrl.requeue_changed_sections(
            completed, queue, sections, planspace,
        )

        assert "01" in requeued
        assert "01" in queue
        assert "01" not in completed

    def test_no_requeue_when_inputs_unchanged(
        self, planspace: Path,
    ) -> None:
        """Section with unchanged inputs stays in completed set."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)
        ctrl = _make_pipeline_control()

        # Compute and persist current hash
        current_hash = section_inputs_hash("01", planspace, sections)
        hash_file = paths.section_inputs_hashes_dir() / "01.hash"
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(current_hash, encoding="utf-8")

        completed = {"01"}
        queue: list[str] = []
        requeued = ctrl.requeue_changed_sections(
            completed, queue, sections, planspace,
        )

        assert requeued == []
        assert "01" in completed
        assert "01" not in queue

    def test_requeue_updates_persisted_hash(
        self, planspace: Path,
    ) -> None:
        """After requeue, the persisted hash is updated to the new value."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)
        ctrl = _make_pipeline_control()

        hash_file = paths.section_inputs_hashes_dir() / "01.hash"
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text("stale-old-hash", encoding="utf-8")

        completed = {"01"}
        queue: list[str] = []
        ctrl.requeue_changed_sections(
            completed, queue, sections, planspace,
        )

        new_hash = section_inputs_hash("01", planspace, sections)
        persisted = hash_file.read_text(encoding="utf-8").strip()
        assert persisted == new_hash

    def test_current_section_prepended_on_requeue(
        self, planspace: Path,
    ) -> None:
        """current_section is prepended to front of queue."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)
        ctrl = _make_pipeline_control()

        hash_file = paths.section_inputs_hashes_dir() / "01.hash"
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text("old-hash", encoding="utf-8")

        completed = {"01"}
        queue: list[str] = ["02"]
        ctrl.requeue_changed_sections(
            completed, queue, sections, planspace,
            current_section="03",
        )

        assert queue[0] == "03", "current_section should be at front"

    def test_requeue_with_no_prior_baseline(
        self, planspace: Path,
    ) -> None:
        """Section with no persisted baseline hash is always requeued."""
        sections = _make_sections_by_num(planspace)
        ctrl = _make_pipeline_control()

        completed = {"01"}
        queue: list[str] = []
        requeued = ctrl.requeue_changed_sections(
            completed, queue, sections, planspace,
        )

        assert "01" in requeued


class TestEndToEndStalenessCycle:
    """Full cycle: initial hash -> modify artifact -> detect staleness."""

    def test_full_cycle_governance_packet_triggers_requeue(
        self, planspace: Path,
    ) -> None:
        """Add governance packet after baseline -> section becomes stale."""
        sections = _make_sections_by_num(planspace)
        paths = PathRegistry(planspace)
        ctrl = _make_pipeline_control()

        # Phase 1: compute and store baseline
        baseline = section_inputs_hash("01", planspace, sections)
        hash_file = paths.section_inputs_hashes_dir() / "01.hash"
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(baseline, encoding="utf-8")

        # Phase 2: upstream governance packet arrives
        gov = paths.governance_packet("01")
        gov.parent.mkdir(parents=True, exist_ok=True)
        gov.write_text(json.dumps({
            "section": "01",
            "candidate_problems": [{"problem_id": "PRB-001", "title": "Auth gap"}],
            "applicability_state": "matched",
        }))

        # Phase 3: requeue detects change
        completed = {"01"}
        queue: list[str] = []
        requeued = ctrl.requeue_changed_sections(
            completed, queue, sections, planspace,
        )

        assert "01" in requeued
        assert "01" not in completed

        # Phase 4: freshness also diverges
        fresh_after = compute_section_freshness(planspace, "01")
        # Remove the packet and check freshness differs
        gov.unlink()
        fresh_without = compute_section_freshness(planspace, "01")
        assert fresh_after != fresh_without
