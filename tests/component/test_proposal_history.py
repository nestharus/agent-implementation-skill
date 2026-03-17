"""Component tests for ProposalHistoryRecorder and readiness gate integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import Services
from src.orchestrator.path_registry import PathRegistry
from src.proposal.service.proposal_history import ProposalHistoryRecorder
from src.proposal.engine.readiness_gate import ReadinessGate
from src.proposal.repository.state import ProposalState
from src.orchestrator.types import Section


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recorder() -> ProposalHistoryRecorder:
    return ProposalHistoryRecorder(artifact_io=Services.artifact_io())


def _make_gate() -> ReadinessGate:
    from src.reconciliation.repository.queue import Queue
    from src.research.prompt.writers import ResearchPromptWriter
    return ReadinessGate(
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


def _section(planspace: Path) -> Section:
    section = Section(
        number="03",
        path=planspace / "artifacts" / "sections" / "section-03.md",
    )
    section.path.write_text("# Section 03\n", encoding="utf-8")
    return section


def _write_proposal_state(planspace: Path, *, execution_ready: bool, blockers: dict | None = None) -> None:
    """Write a minimal valid proposal-state JSON."""
    state = {
        "resolved_anchors": [],
        "unresolved_contracts": [],
        "resolved_contracts": [],
        "unresolved_anchors": [],
        "user_root_questions": [],
        "shared_seam_candidates": [],
        "new_section_candidates": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "execution_ready": execution_ready,
        "readiness_rationale": "ready" if execution_ready else "blocked",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    if blockers:
        state.update(blockers)
    path = planspace / "artifacts" / "proposals" / "section-03-proposal-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# ProposalHistoryRecorder unit tests
# ---------------------------------------------------------------------------


class TestProposalHistoryRecorder:

    def test_append_round_creates_file(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        recorder = _recorder()
        recorder.append_round(planspace, "03", {
            "round_number": 1,
            "intent_mode": "proposal",
            "execution_ready": False,
            "blockers": ["unresolved_contracts: CacheProtocol"],
            "verification_findings": [],
            "disposition": "blocked",
        })

        history = recorder.read_history(planspace, "03")
        assert "## Round 1" in history
        assert "- Mode: proposal" in history
        assert "- Ready: False" in history
        assert "- Blockers: 1" in history
        assert "  - unresolved_contracts: CacheProtocol" in history
        assert "- Disposition: blocked" in history

    def test_append_multiple_rounds_accumulates(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        recorder = _recorder()
        recorder.append_round(planspace, "05", {
            "round_number": 1,
            "intent_mode": "proposal",
            "execution_ready": False,
            "blockers": ["blocking_research_questions: DB base missing"],
            "verification_findings": [],
            "disposition": "blocked",
        })
        recorder.append_round(planspace, "05", {
            "round_number": 2,
            "intent_mode": "proposal",
            "execution_ready": True,
            "blockers": [],
            "verification_findings": [],
            "disposition": "implemented",
        })

        history = recorder.read_history(planspace, "05")
        assert "## Round 1" in history
        assert "## Round 2" in history
        assert "- Disposition: blocked" in history
        assert "- Disposition: implemented" in history

    def test_read_history_returns_empty_when_missing(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()

        recorder = _recorder()
        assert recorder.read_history(planspace, "99") == ""

    def test_append_round_includes_verification_findings(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        recorder = _recorder()
        recorder.append_round(planspace, "03", {
            "round_number": 1,
            "intent_mode": "full",
            "execution_ready": False,
            "blockers": [],
            "verification_findings": ["import cycle detected", "missing test"],
            "disposition": "blocked",
        })

        history = recorder.read_history(planspace, "03")
        assert "- Verification findings: 2" in history
        assert "  - import cycle detected" in history
        assert "  - missing test" in history

    def test_append_round_handles_missing_keys_gracefully(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        recorder = _recorder()
        recorder.append_round(planspace, "03", {})

        history = recorder.read_history(planspace, "03")
        assert "## Round ?" in history
        assert "- Mode: unknown" in history
        assert "- Disposition: unknown" in history

    def test_history_path_matches_path_registry(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()

        recorder = _recorder()
        expected = PathRegistry(planspace).proposal_history("03")
        actual = recorder._history_path(planspace, "03")
        assert actual == expected


# ---------------------------------------------------------------------------
# ReadinessGate integration: history appended on resolve_and_route
# ---------------------------------------------------------------------------


class TestReadinessGateHistory:

    def test_blocked_section_appends_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        noop_communicator,
    ) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()
        _write_proposal_state(
            planspace,
            execution_ready=False,
            blockers={"unresolved_contracts": ["CacheProtocol"]},
        )
        section = _section(planspace)

        monkeypatch.setattr(
            "src.proposal.engine.readiness_gate.append_open_problem",
            lambda *_a, **_kw: None,
        )
        monkeypatch.setattr(
            "src.reconciliation.repository.queue.Queue.queue_reconciliation_request",
            lambda *_a, **_kw: None,
        )
        monkeypatch.setattr(
            "src.proposal.engine.readiness_gate.update_blocker_rollup",
            lambda *_a, **_kw: None,
        )

        gate = _make_gate()
        result = gate.resolve_and_route(section, planspace, "proposal")

        assert result.ready is False
        history_path = PathRegistry(planspace).proposal_history("03")
        assert history_path.exists()
        history = history_path.read_text(encoding="utf-8")
        assert "## Round 1" in history
        assert "- Ready: False" in history
        assert "- Disposition: blocked" in history
        assert "unresolved_contracts" in history

    def test_ready_section_appends_history(
        self,
        tmp_path: Path,
        noop_communicator,
    ) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()
        _write_proposal_state(planspace, execution_ready=True)
        section = _section(planspace)

        gate = _make_gate()
        result = gate.resolve_and_route(section, planspace, "proposal")

        assert result.ready is True
        history_path = PathRegistry(planspace).proposal_history("03")
        assert history_path.exists()
        history = history_path.read_text(encoding="utf-8")
        assert "## Round 1" in history
        assert "- Ready: True" in history
        assert "- Disposition: implemented" in history

    def test_multiple_rounds_increment_round_number(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        noop_communicator,
    ) -> None:
        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()
        section = _section(planspace)

        monkeypatch.setattr(
            "src.proposal.engine.readiness_gate.append_open_problem",
            lambda *_a, **_kw: None,
        )
        monkeypatch.setattr(
            "src.reconciliation.repository.queue.Queue.queue_reconciliation_request",
            lambda *_a, **_kw: None,
        )
        monkeypatch.setattr(
            "src.proposal.engine.readiness_gate.update_blocker_rollup",
            lambda *_a, **_kw: None,
        )

        gate = _make_gate()

        # Round 1: blocked
        _write_proposal_state(
            planspace,
            execution_ready=False,
            blockers={"unresolved_contracts": ["CacheProtocol"]},
        )
        gate.resolve_and_route(section, planspace, "proposal")

        # Round 2: ready
        _write_proposal_state(planspace, execution_ready=True)
        gate.resolve_and_route(section, planspace, "proposal")

        history = PathRegistry(planspace).proposal_history("03").read_text(encoding="utf-8")
        assert "## Round 1" in history
        assert "## Round 2" in history


# ---------------------------------------------------------------------------
# PathRegistry accessor
# ---------------------------------------------------------------------------


class TestPathRegistryProposalHistory:

    def test_proposal_history_path(self, tmp_path: Path) -> None:
        planspace = tmp_path / "planspace"
        registry = PathRegistry(planspace)
        path = registry.proposal_history("03")
        assert path == planspace / "artifacts" / "intent" / "sections" / "section-03" / "proposal-history.md"


# ---------------------------------------------------------------------------
# Context builder integration
# ---------------------------------------------------------------------------


class TestContextBuilderProposalHistory:

    def test_proposal_history_ref_present_when_file_exists(
        self, tmp_path: Path,
    ) -> None:
        from src.dispatch.prompt.context_builder import _build_intent_context

        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        # Create the proposal history file
        history_path = PathRegistry(planspace).proposal_history("03")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text("## Round 1\n- Disposition: blocked\n", encoding="utf-8")

        paths = PathRegistry(planspace)
        ctx = _build_intent_context(paths, "03")
        assert "proposal_history_ref" in ctx
        assert "proposal-history.md" in ctx["proposal_history_ref"]
        assert str(history_path) in ctx["proposal_history_ref"]

    def test_proposal_history_ref_empty_when_no_file(
        self, tmp_path: Path,
    ) -> None:
        from src.dispatch.prompt.context_builder import _build_intent_context

        planspace = tmp_path / "planspace"
        planspace.mkdir()
        PathRegistry(planspace).ensure_artifacts_tree()

        paths = PathRegistry(planspace)
        ctx = _build_intent_context(paths, "03")
        assert ctx["proposal_history_ref"] == ""
