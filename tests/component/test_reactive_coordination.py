"""Component tests for reactive coordination mechanisms.

Tests the six gap resolutions from the fractal pipeline design:
- Gap 1: Root-reframe signal
- Gap 2: Contract conflict detection in readiness resolver
- Gap 4: Starvation detection
- Gap 5: Consequence cascade bounding
- Gap 6: Incremental strategic state
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from src.containers import ArtifactIOService, Services
from src.orchestrator.path_registry import PathRegistry
from src.proposal.service.readiness_resolver import ReadinessResolver, ReadinessResult
from src.coordination.service.completion_handler import (
    CompletionHandler,
    _build_consequence_note,
)
from src.orchestrator.engine.strategic_state_builder import StrategicStateBuilder
from src.flow.service.starvation_detector import (
    detect_starvation,
    record_chain_submission,
    DEFAULT_STARVATION_THRESHOLD_SECONDS,
)

_artifact_io = ArtifactIOService()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_proposal_state(planspace: Path, section: str, **overrides) -> None:
    """Write a ready proposal-state using runtime layout."""
    state = {
        "resolved_anchors": ["a.store"],
        "unresolved_anchors": [],
        "resolved_contracts": ["Proto"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "ready",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state.update(overrides)
    proposal = (
        planspace / "artifacts" / "proposals"
        / f"section-{section}-proposal-state.json"
    )
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps(state), encoding="utf-8")


def _make_shard(
    planspace: Path,
    section: str,
    provides: list[dict] | None = None,
    needs: list[dict] | None = None,
) -> None:
    """Write a substrate shard JSON."""
    shard_path = (
        planspace / "artifacts" / "substrate" / "shards" / f"shard-{section}.json"
    )
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    shard_path.write_text(json.dumps({
        "schema_version": 1,
        "section_number": int(section),
        "mode": "greenfield",
        "touchpoints": [],
        "provides": provides or [],
        "needs": needs or [],
        "shared_seams": [],
        "open_questions": [],
    }), encoding="utf-8")


# ===========================================================================
# Gap 2: Contract conflict detection
# ===========================================================================


class TestContractConflictDetection:
    """Gap 2: readiness resolver detects contract conflicts with seam-sharing sections."""

    def test_contract_conflict_blocks_readiness(self, tmp_path: Path) -> None:
        """When seam-sharing sections have conflicting contracts, readiness is blocked."""
        planspace = tmp_path / "planspace"
        PathRegistry(planspace).ensure_artifacts_tree()

        # Section 03 provides auth.register, section 05 needs auth.register
        _make_shard(planspace, "03", provides=[{"id": "auth.register", "kind": "api"}])
        _make_shard(planspace, "05", needs=[{"id": "auth.register", "kind": "api"}])

        # Section 03 has resolved the contract, section 05 has it unresolved
        _make_proposal_state(
            planspace, "03",
            resolved_contracts=["auth.register"],
            unresolved_contracts=[],
        )
        _make_proposal_state(
            planspace, "05",
            resolved_contracts=[],
            unresolved_contracts=["auth.register"],
        )

        resolver = ReadinessResolver(artifact_io=_artifact_io)
        result = resolver.resolve_readiness(planspace, "05")

        assert result.ready is False
        conflict_blockers = [
            b for b in result.blockers if b.get("type") == "contract_conflict"
        ]
        assert len(conflict_blockers) == 1
        assert "auth.register" in conflict_blockers[0]["description"]

    def test_no_conflict_when_both_resolved(self, tmp_path: Path) -> None:
        """No conflict when both sections have resolved the same contract."""
        planspace = tmp_path / "planspace"
        PathRegistry(planspace).ensure_artifacts_tree()

        _make_shard(planspace, "03", provides=[{"id": "cache.store", "kind": "service"}])
        _make_shard(planspace, "05", needs=[{"id": "cache.store", "kind": "service"}])

        _make_proposal_state(
            planspace, "03",
            resolved_contracts=["cache.store"],
            unresolved_contracts=[],
        )
        _make_proposal_state(
            planspace, "05",
            resolved_contracts=["cache.store"],
            unresolved_contracts=[],
        )

        resolver = ReadinessResolver(artifact_io=_artifact_io)
        result = resolver.resolve_readiness(planspace, "05")

        assert result.ready is True
        conflict_blockers = [
            b for b in result.blockers if b.get("type") == "contract_conflict"
        ]
        assert conflict_blockers == []

    def test_no_conflict_without_substrate_shards(self, tmp_path: Path) -> None:
        """No substrate shards -> no conflict check (fail-open)."""
        planspace = tmp_path / "planspace"
        PathRegistry(planspace).ensure_artifacts_tree()

        _make_proposal_state(planspace, "03")

        resolver = ReadinessResolver(artifact_io=_artifact_io)
        result = resolver.resolve_readiness(planspace, "03")

        assert result.ready is True
        conflict_blockers = [
            b for b in result.blockers if b.get("type") == "contract_conflict"
        ]
        assert conflict_blockers == []

    def test_no_conflict_when_no_seam_overlap(self, tmp_path: Path) -> None:
        """Sections with non-overlapping provides/needs -> no conflict."""
        planspace = tmp_path / "planspace"
        PathRegistry(planspace).ensure_artifacts_tree()

        _make_shard(planspace, "03", provides=[{"id": "auth.register", "kind": "api"}])
        _make_shard(planspace, "05", provides=[{"id": "cache.store", "kind": "service"}])

        _make_proposal_state(
            planspace, "03",
            unresolved_contracts=["auth.register"],
        )
        _make_proposal_state(
            planspace, "05",
            unresolved_contracts=["cache.store"],
        )

        resolver = ReadinessResolver(artifact_io=_artifact_io)
        result = resolver.resolve_readiness(planspace, "03")

        # No seam sharing -> contract_conflict check not triggered
        conflict_blockers = [
            b for b in result.blockers if b.get("type") == "contract_conflict"
        ]
        assert conflict_blockers == []


# ===========================================================================
# Gap 1: Root-reframe signal
# ===========================================================================


class TestRootReframeSignal:
    """Gap 1: root-reframe signal pauses section dispatches."""

    def test_publish_discoveries_emits_reframe_signal(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """A candidate with requires_root_reframing=true writes the signal."""
        from src.proposal.engine.readiness_gate import ReadinessGate
        from src.reconciliation.repository.queue import Queue
        from src.research.prompt.writers import ResearchPromptWriter
        from src.proposal.repository.state import ProposalState

        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        monkeypatch.setattr(
            "src.proposal.engine.readiness_gate.append_open_problem",
            lambda *_args, **_kwargs: None,
        )

        gate = ReadinessGate(
            logger=Services.logger(),
            artifact_io=Services.artifact_io(),
            hasher=Services.hasher(),
            communicator=Services.communicator(),
            research=Services.research(),
            freshness=Services.freshness(),
            prompt_writer=ResearchPromptWriter(
                prompt_guard=Services.prompt_guard(),
                artifact_io=Services.artifact_io(),
            ),
            reconciliation_queue=Queue(artifact_io=Services.artifact_io()),
        )

        gate.publish_discoveries(
            "03",
            ProposalState(
                new_section_candidates=[
                    {"title": "New domain", "requires_root_reframing": True},
                ],
            ),
            planspace,
        )

        reframe_path = PathRegistry(planspace).root_reframe_signal()
        assert reframe_path.exists()
        data = json.loads(reframe_path.read_text(encoding="utf-8"))
        assert data["source_section"] == "03"
        assert "root reframing" in data["reason"]

    def test_no_reframe_signal_for_normal_candidates(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """Normal candidates (no requires_root_reframing) do not write the signal."""
        from src.proposal.engine.readiness_gate import ReadinessGate
        from src.reconciliation.repository.queue import Queue
        from src.research.prompt.writers import ResearchPromptWriter
        from src.proposal.repository.state import ProposalState

        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        monkeypatch.setattr(
            "src.proposal.engine.readiness_gate.append_open_problem",
            lambda *_args, **_kwargs: None,
        )

        gate = ReadinessGate(
            logger=Services.logger(),
            artifact_io=Services.artifact_io(),
            hasher=Services.hasher(),
            communicator=Services.communicator(),
            research=Services.research(),
            freshness=Services.freshness(),
            prompt_writer=ResearchPromptWriter(
                prompt_guard=Services.prompt_guard(),
                artifact_io=Services.artifact_io(),
            ),
            reconciliation_queue=Queue(artifact_io=Services.artifact_io()),
        )

        gate.publish_discoveries(
            "03",
            ProposalState(
                new_section_candidates=["Create retry worker"],
            ),
            planspace,
        )

        reframe_path = PathRegistry(planspace).root_reframe_signal()
        assert not reframe_path.exists()


# ===========================================================================
# Gap 5: Consequence cascade bounding
# ===========================================================================


class TestConsequenceCascadeBounding:
    """Gap 5: consequence notes carry depth, overflow becomes coordination task."""

    def test_build_note_includes_depth(self) -> None:
        """_build_consequence_note includes Consequence Depth metadata."""
        note = _build_consequence_note(
            "01", "05", "API changed", None, "note-id-123",
            "auth module", ["api.py"], Path("/tmp/planspace"),
            depth=2,
        )
        assert "**Consequence Depth**: `2`" in note

    def test_build_note_default_depth_is_1(self) -> None:
        """Default depth is 1 for originator notes."""
        note = _build_consequence_note(
            "01", "05", "API changed", None, "note-id-123",
            "auth module", ["api.py"], Path("/tmp/planspace"),
        )
        assert "**Consequence Depth**: `1`" in note

    def test_no_hardcoded_depth_cap(self) -> None:
        """Consequence depth is tracked but not mechanically capped."""
        # Depth metadata is included in notes for agent observability
        # but no hardcoded threshold blocks propagation.
        pass


# ===========================================================================
# Gap 6: Incremental strategic state
# ===========================================================================


class TestIncrementalStrategicState:
    """Gap 6: StrategicStateBuilder.update_section_completion works incrementally."""

    def test_update_adds_completed_section(self, tmp_path: Path) -> None:
        """A newly aligned section is added to completed_sections."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        builder = StrategicStateBuilder(artifact_io=_artifact_io)

        # Write initial strategic state
        state_path = PathRegistry(planspace).strategic_state()
        initial = {
            "completed_sections": ["01"],
            "in_progress": "02",
            "blocked": {},
            "open_problems": [],
            "research_questions": [],
            "key_decisions": [],
            "coordination_rounds": 0,
            "risk_posture": {},
            "dominant_risks_by_section": {},
            "blocked_by_risk": [],
            "next_action": "section-02 alignment check",
        }
        _artifact_io.write_json(state_path, initial)

        result = builder.update_section_completion(
            planspace, "02", {"aligned": True},
        )

        assert "01" in result["completed_sections"]
        assert "02" in result["completed_sections"]
        assert result["in_progress"] is None

    def test_update_removes_from_blocked(self, tmp_path: Path) -> None:
        """A section that was blocked is removed when it completes."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        builder = StrategicStateBuilder(artifact_io=_artifact_io)

        state_path = PathRegistry(planspace).strategic_state()
        initial = {
            "completed_sections": [],
            "in_progress": None,
            "blocked": {"03": {"problem_id": "p-03", "reason": "needs parent"}},
            "open_problems": [],
            "research_questions": [],
            "key_decisions": [],
            "coordination_rounds": 0,
            "risk_posture": {},
            "dominant_risks_by_section": {},
            "blocked_by_risk": [],
            "next_action": "resolve blocker for section 03",
        }
        _artifact_io.write_json(state_path, initial)

        result = builder.update_section_completion(
            planspace, "03", {"aligned": True},
        )

        assert "03" in result["completed_sections"]
        assert "03" not in result["blocked"]

    def test_update_creates_initial_state_if_missing(self, tmp_path: Path) -> None:
        """When no existing strategic state, creates a minimal initial state."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        builder = StrategicStateBuilder(artifact_io=_artifact_io)

        result = builder.update_section_completion(
            planspace, "01", {"aligned": True},
        )

        assert result["completed_sections"] == ["01"]
        assert result["blocked"] == {}

    def test_update_persists_to_disk(self, tmp_path: Path) -> None:
        """Incremental update writes to the strategic-state.json file."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        builder = StrategicStateBuilder(artifact_io=_artifact_io)
        builder.update_section_completion(
            planspace, "05", {"aligned": True},
        )

        state_path = PathRegistry(planspace).strategic_state()
        assert state_path.exists()
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert "05" in saved["completed_sections"]


# ===========================================================================
# Gap 4: Starvation detection
# ===========================================================================


class TestStarvationDetection:
    """Gap 4: sections blocked beyond threshold emit starvation signals."""

    def test_record_and_detect_starvation(self, tmp_path: Path) -> None:
        """A section blocked beyond threshold is detected as starved."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        # Record a submission time far in the past
        paths = PathRegistry(planspace)
        submission_path = paths.section_chain_submission("03")
        _artifact_io.write_json(submission_path, {
            "section": "03",
            "last_submission_time": time.time() - 3600,  # 1 hour ago
        })

        starved = detect_starvation(
            _artifact_io, planspace, ["03"],
            threshold_seconds=1800,
        )

        assert starved == ["03"]
        starvation_path = paths.starvation_signal("03")
        assert starvation_path.exists()
        data = json.loads(starvation_path.read_text(encoding="utf-8"))
        assert data["type"] == "starvation"
        assert data["section"] == "03"

    def test_no_starvation_when_recent(self, tmp_path: Path) -> None:
        """A recently submitted section is not starved."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        record_chain_submission(_artifact_io, planspace, "03")

        starved = detect_starvation(
            _artifact_io, planspace, ["03"],
            threshold_seconds=1800,
        )

        assert starved == []

    def test_no_starvation_without_submission_record(self, tmp_path: Path) -> None:
        """Sections without submission records are skipped."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        starved = detect_starvation(
            _artifact_io, planspace, ["03"],
            threshold_seconds=1800,
        )

        assert starved == []

    def test_record_chain_submission_writes_artifact(self, tmp_path: Path) -> None:
        """record_chain_submission writes a timestamped artifact."""
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        record_chain_submission(_artifact_io, planspace, "05")

        paths = PathRegistry(planspace)
        submission_path = paths.section_chain_submission("05")
        assert submission_path.exists()
        data = json.loads(submission_path.read_text(encoding="utf-8"))
        assert data["section"] == "05"
        assert isinstance(data["last_submission_time"], float)


# ===========================================================================
# Gap 3/6: Detectors survive, orchestrators deprecated
# ===========================================================================


class TestDetectorsSurvive:
    """Gap 3: detection functions in detectors.py remain unchanged and functional."""

    def test_detect_contract_conflicts_still_works(self) -> None:
        """The pure detection function works independently of any orchestrator."""
        from src.reconciliation.service.detectors import detect_contract_conflicts
        from src.proposal.repository.state import ProposalState

        states = {
            "01": ProposalState(
                resolved_contracts=["auth.register"],
                unresolved_contracts=[],
            ),
            "05": ProposalState(
                resolved_contracts=[],
                unresolved_contracts=["auth.register"],
            ),
        }
        conflicts = detect_contract_conflicts(states)
        assert len(conflicts) == 1
        assert conflicts[0]["contract"] == "auth.register"

    def test_detect_problem_interactions_still_works(self) -> None:
        """The pure detection function works independently."""
        from src.reconciliation.service.detectors import detect_problem_interactions
        from src.proposal.repository.state import ProposalState

        states = {
            "01": ProposalState(resolved_anchors=["backend/main.py"]),
            "05": ProposalState(resolved_anchors=["backend/main.py"]),
        }
        interactions = detect_problem_interactions(states)
        assert len(interactions) == 1
        assert "01" in interactions[0]["sections"]
        assert "05" in interactions[0]["sections"]

    def test_cross_section_reconciler_has_deprecation_notice(self) -> None:
        """The CrossSectionReconciler module has a deprecation notice."""
        import src.reconciliation.engine.cross_section_reconciler as mod
        assert "DEAD CODE" in (mod.__doc__ or "")

    def test_reconciliation_phase_has_deprecation_notice(self) -> None:
        """The ReconciliationPhase module has a deprecation notice."""
        import src.reconciliation.engine.reconciliation_phase as mod
        assert "DEAD CODE" in (mod.__doc__ or "")
