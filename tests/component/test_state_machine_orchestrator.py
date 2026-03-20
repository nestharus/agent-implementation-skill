"""Component tests for the state machine orchestrator.

Tests cover:
- Section state initialization (fresh + resume)
- DB helpers (set/get state, transitions)
- Task submission based on state (all new states)
- Blocked section unblock checks
- State advancement on task completion (reconciler integration)
- Circuit breaker for self-transitions (via advance_section)
- Terminal state detection
- Context-dependent event routing for all new task types
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.engine.section_state_machine import (
    SectionEvent,
    SectionState,
    advance_section,
    get_actionable_sections,
    get_section_state,
    get_sections_in_state,
    set_section_state,
    record_transition,
    InvalidTransitionError,
    _CIRCUIT_BREAKER_LIMITS,
)
from orchestrator.engine.state_machine_orchestrator import (
    StateMachineOrchestrator,
    advance_on_task_completion,
    all_sections_terminal,
    get_all_section_states,
    get_blocked_sections,
    _STATE_TASK_MAP,
)
from flow.service.task_db_client import init_db
from orchestrator.path_registry import PathRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create a fresh run.db with both task and section_states schemas."""
    db = tmp_path / "run.db"
    init_db(db)
    return db


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    """Create a planspace with the standard artifact tree."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    return ps


def _make_services():
    """Build minimal mock services for the orchestrator."""
    logger = MagicMock()
    logger.log = MagicMock()
    artifact_io = MagicMock()
    flow_submitter = MagicMock()
    flow_submitter.submit_chain = MagicMock(return_value=[1])
    pipeline_control = MagicMock()
    pipeline_control.handle_pending_messages = MagicMock(return_value=False)
    return logger, artifact_io, flow_submitter, pipeline_control


# ---------------------------------------------------------------------------
# DB schema validation
# ---------------------------------------------------------------------------


class TestSectionStateSchema:
    def test_init_db_creates_section_tables(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "section_states" in tables
        assert "section_transitions" in tables


# ---------------------------------------------------------------------------
# State machine DB helpers (from section_state_machine)
# ---------------------------------------------------------------------------


class TestSetAndGetState:
    def test_set_creates_row(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        state = get_section_state(db_path, "01")
        assert state == SectionState.PENDING

    def test_set_updates_existing(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        set_section_state(db_path, "01", SectionState.PROPOSING)
        state = get_section_state(db_path, "01")
        assert state == SectionState.PROPOSING

    def test_get_nonexistent_returns_pending(self, db_path: Path) -> None:
        # section_state_machine returns PENDING for missing sections
        assert get_section_state(db_path, "99") == SectionState.PENDING


class TestRecordTransition:
    def test_records_transition(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        record_transition(
            db_path, "01",
            from_state=SectionState.PENDING,
            to_state=SectionState.EXCERPT_EXTRACTION,
            event=SectionEvent.excerpt_complete,
            attempt_number=1,
        )

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        transitions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM section_transitions WHERE section_number='01'"
            ).fetchall()
        ]
        conn.close()
        assert len(transitions) == 1
        assert transitions[0]["from_state"] == SectionState.PENDING.value
        assert transitions[0]["to_state"] == SectionState.EXCERPT_EXTRACTION.value
        assert transitions[0]["event"] == SectionEvent.excerpt_complete.value


class TestQueryHelpers:
    def test_get_actionable_sections(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        set_section_state(db_path, "02", SectionState.PROPOSING)
        set_section_state(db_path, "03", SectionState.READINESS)
        set_section_state(db_path, "04", SectionState.COMPLETE)
        set_section_state(db_path, "05", SectionState.BLOCKED)

        actionable = get_actionable_sections(db_path)
        nums = [num for num, _state in actionable]
        assert "01" in nums  # PENDING
        assert "02" in nums  # PROPOSING
        assert "03" in nums  # READINESS
        assert "04" not in nums  # COMPLETE (terminal)
        assert "05" not in nums  # BLOCKED

    def test_get_sections_in_state(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.BLOCKED)
        set_section_state(db_path, "02", SectionState.BLOCKED)
        set_section_state(db_path, "03", SectionState.PROPOSING)

        blocked = get_sections_in_state(db_path, SectionState.BLOCKED)
        assert len(blocked) == 2

    def test_all_sections_terminal_false(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.COMPLETE)
        set_section_state(db_path, "02", SectionState.PROPOSING)
        assert not all_sections_terminal(db_path)

    def test_all_sections_terminal_true(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.COMPLETE)
        set_section_state(db_path, "02", SectionState.FAILED)
        set_section_state(db_path, "03", SectionState.ESCALATED)
        assert all_sections_terminal(db_path)

    def test_all_sections_terminal_empty_returns_false(self, db_path: Path) -> None:
        """0 rows means bootstrap hasn't populated sections yet -- not terminal."""
        assert not all_sections_terminal(db_path)

    def test_get_all_section_states(self, db_path: Path) -> None:
        set_section_state(db_path, "03", SectionState.PENDING)
        set_section_state(db_path, "01", SectionState.PROPOSING)
        set_section_state(db_path, "02", SectionState.BLOCKED)
        rows = get_all_section_states(db_path)
        assert [r["section_number"] for r in rows] == ["01", "02", "03"]

    def test_get_blocked_sections(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.BLOCKED)
        set_section_state(db_path, "02", SectionState.PROPOSING)
        set_section_state(db_path, "03", SectionState.BLOCKED)
        blocked = get_blocked_sections(db_path)
        assert len(blocked) == 2
        assert blocked[0]["section_number"] == "01"
        assert blocked[1]["section_number"] == "03"


# ---------------------------------------------------------------------------
# StateMachineOrchestrator
# ---------------------------------------------------------------------------


class TestInitializeSections:
    def test_fresh_init(self, db_path: Path) -> None:
        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm.initialize_sections(db_path, ["01", "02", "03"])
        states = get_all_section_states(db_path)
        assert len(states) == 3
        assert all(s["state"] == SectionState.PENDING.value for s in states)

    def test_resume_preserves_existing_state(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm.initialize_sections(db_path, ["01", "02"])

        s1 = get_section_state(db_path, "01")
        s2 = get_section_state(db_path, "02")
        assert s1 == SectionState.PROPOSING  # preserved
        assert s2 == SectionState.PENDING  # new


class TestSubmitForState:
    def test_pending_submits_excerpt(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        paths = PathRegistry(planspace)
        sm._submit_for_state(
            db_path, planspace, "01", SectionState.PENDING, paths,
        )

        assert flow_sub.submit_chain.called
        call_args = flow_sub.submit_chain.call_args
        steps = call_args[0][1]
        assert len(steps) == 1
        assert steps[0].task_type == "section.excerpt"
        assert steps[0].concern_scope == "section-01"

    def test_proposing_submits_propose(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        paths = PathRegistry(planspace)
        sm._submit_for_state(
            db_path, planspace, "01", SectionState.PROPOSING, paths,
        )

        call_args = flow_sub.submit_chain.call_args
        steps = call_args[0][1]
        assert steps[0].task_type == "section.propose"

    def test_implementing_submits_implement(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.IMPLEMENTING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        paths = PathRegistry(planspace)
        sm._submit_for_state(
            db_path, planspace, "01", SectionState.IMPLEMENTING, paths,
        )

        call_args = flow_sub.submit_chain.call_args
        steps = call_args[0][1]
        assert steps[0].task_type == "section.implement"

    @pytest.mark.parametrize("state, expected_task_type", [
        (SectionState.EXCERPT_EXTRACTION, "section.excerpt"),
        (SectionState.PROBLEM_FRAME, "section.problem_frame"),
        (SectionState.INTENT_TRIAGE, "section.intent_triage"),
        (SectionState.PHILOSOPHY_BOOTSTRAP, "section.philosophy"),
        (SectionState.INTENT_PACK, "section.intent_pack"),
        (SectionState.ASSESSING, "section.assess"),
        (SectionState.RISK_EVAL, "section.risk_eval"),
        (SectionState.MICROSTRATEGY, "section.microstrategy"),
        (SectionState.IMPL_ASSESSING, "section.impl_assess"),
        (SectionState.VERIFYING, "section.verify"),
        (SectionState.POST_COMPLETION, "section.post_complete"),
    ])
    def test_state_submits_correct_task(
        self, db_path: Path, planspace: Path,
        state: SectionState, expected_task_type: str,
    ) -> None:
        set_section_state(db_path, "01", state)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        paths = PathRegistry(planspace)
        sm._submit_for_state(
            db_path, planspace, "01", state, paths,
        )

        call_args = flow_sub.submit_chain.call_args
        steps = call_args[0][1]
        assert steps[0].task_type == expected_task_type

    def test_readiness_does_not_submit(self, db_path: Path, planspace: Path) -> None:
        """READINESS is script-only -- no task submitted."""
        set_section_state(db_path, "01", SectionState.READINESS)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        paths = PathRegistry(planspace)
        sm._submit_for_state(
            db_path, planspace, "01", SectionState.READINESS, paths,
        )

        assert not flow_sub.submit_chain.called


class TestStateTaskMapCoverage:
    """Verify _STATE_TASK_MAP covers all non-terminal, non-blocked, non-script states."""

    def test_all_agent_states_have_task_type(self) -> None:
        """States that dispatch an agent must have a task type mapping."""
        # States that intentionally skip: READINESS (script), terminal/non-actionable
        skip = {
            SectionState.READINESS,
            SectionState.COMPLETE, SectionState.FAILED,
            SectionState.ESCALATED, SectionState.BLOCKED,
            SectionState.AWAITING_CHILDREN,
            SectionState.SCOPE_EXPANSION,
        }
        for state in SectionState:
            if state not in skip:
                assert state in _STATE_TASK_MAP, (
                    f"{state.value} is not in _STATE_TASK_MAP"
                )


class TestMainLoop:
    def test_loop_exits_when_all_terminal(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.COMPLETE)
        set_section_state(db_path, "02", SectionState.FAILED)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._sleep = lambda _: None

        sm.run(db_path, planspace)
        log_calls = [str(c) for c in logger.log.call_args_list]
        assert any("terminal" in c.lower() or "complete" in c.lower() for c in log_calls)

    def test_loop_aborts_on_parent_signal(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        pipeline_ctrl.handle_pending_messages = MagicMock(return_value=True)
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._sleep = lambda _: None

        sm.run(db_path, planspace)
        assert not flow_sub.submit_chain.called

    def test_loop_submits_pending_then_advances(
        self, db_path: Path, planspace: Path,
    ) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        call_count = 0

        def mock_sleep(_interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                set_section_state(db_path, "01", SectionState.COMPLETE)

        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._sleep = mock_sleep

        sm.run(db_path, planspace)

        assert flow_sub.submit_chain.called


# ---------------------------------------------------------------------------
# Blocked section unblock checks
# ---------------------------------------------------------------------------


class TestCheckUnblock:
    def test_research_dossier_unblocks(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ctx = {"blocker_type": "blocking_research_questions"}
        set_section_state(
            db_path, "05", SectionState.BLOCKED,
            blocked_reason="research", context=ctx,
        )

        paths = PathRegistry(planspace)
        dossier = paths.research_dossier("05")
        dossier.parent.mkdir(parents=True, exist_ok=True)
        dossier.write_text("Research findings...", encoding="utf-8")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )

        blocked = get_blocked_sections(db_path)
        row = blocked[0]
        sm._check_unblock(db_path, planspace, "05", row)

        updated = get_section_state(db_path, "05")
        assert updated == SectionState.PROPOSING

    def test_no_dossier_stays_blocked(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ctx = {"blocker_type": "blocking_research_questions"}
        set_section_state(
            db_path, "05", SectionState.BLOCKED,
            blocked_reason="research", context=ctx,
        )

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )

        blocked = get_blocked_sections(db_path)
        row = blocked[0]
        sm._check_unblock(db_path, planspace, "05", row)

        updated = get_section_state(db_path, "05")
        assert updated == SectionState.BLOCKED

    def test_readiness_ready_unblocks(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ctx = {"blocker_type": "readiness_failed"}
        set_section_state(
            db_path, "03", SectionState.BLOCKED,
            blocked_reason="readiness", context=ctx,
        )

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        artifact_io.read_json = MagicMock(return_value={"ready": True})
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )

        blocked = get_blocked_sections(db_path)
        row = blocked[0]
        sm._check_unblock(db_path, planspace, "03", row)

        updated = get_section_state(db_path, "03")
        assert updated == SectionState.PROPOSING

    def test_verification_pass_unblocks(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ctx = {"blocker_type": "verification_failure"}
        set_section_state(
            db_path, "07", SectionState.BLOCKED,
            blocked_reason="verification", context=ctx,
        )

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        artifact_io.read_json = MagicMock(return_value={"status": "pass"})
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )

        blocked = get_blocked_sections(db_path)
        row = blocked[0]
        sm._check_unblock(db_path, planspace, "07", row)

        updated = get_section_state(db_path, "07")
        assert updated == SectionState.PROPOSING


# ---------------------------------------------------------------------------
# advance_on_task_completion
# ---------------------------------------------------------------------------


class TestAdvanceOnTaskCompletion:
    """Test all task types route to the correct event."""

    # --- simple task types (direct event map) ---

    def test_excerpt_success_advances(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        result = advance_on_task_completion(
            db_path, "01", "section.excerpt", success=True,
        )
        assert result == SectionState.EXCERPT_EXTRACTION.value

    def test_excerpt_from_extraction_advances(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.EXCERPT_EXTRACTION)
        result = advance_on_task_completion(
            db_path, "01", "section.excerpt", success=True,
        )
        assert result == SectionState.PROBLEM_FRAME.value

    def test_triage_success_advances(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.INTENT_TRIAGE)
        result = advance_on_task_completion(
            db_path, "01", "section.intent_triage", success=True,
        )
        assert result == SectionState.PHILOSOPHY_BOOTSTRAP.value

    def test_intent_pack_success_advances(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.INTENT_PACK)
        result = advance_on_task_completion(
            db_path, "01", "section.intent_pack", success=True,
        )
        assert result == SectionState.PROPOSING.value

    def test_propose_success_advances_to_assessing(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)
        result = advance_on_task_completion(
            db_path, "01", "section.propose", success=True,
        )
        assert result == SectionState.ASSESSING.value
        assert get_section_state(db_path, "01") == SectionState.ASSESSING

    def test_propose_failure_goes_to_failed(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)
        result = advance_on_task_completion(
            db_path, "01", "section.propose", success=False,
        )
        assert result == SectionState.FAILED.value

    def test_microstrategy_success_advances(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.MICROSTRATEGY)
        result = advance_on_task_completion(
            db_path, "01", "section.microstrategy", success=True,
        )
        assert result == SectionState.IMPLEMENTING.value

    def test_implement_success_advances_to_impl_assessing(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.IMPLEMENTING)
        result = advance_on_task_completion(
            db_path, "01", "section.implement", success=True,
        )
        assert result == SectionState.IMPL_ASSESSING.value

    def test_verify_success_advances_to_post_completion(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.VERIFYING)
        result = advance_on_task_completion(
            db_path, "01", "section.verify", success=True,
        )
        assert result == SectionState.POST_COMPLETION.value

    def test_verify_failure_retries(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.VERIFYING)
        result = advance_on_task_completion(
            db_path, "01", "section.verify", success=False,
        )
        assert result == SectionState.IMPLEMENTING.value

    def test_post_complete_success_completes(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.POST_COMPLETION)
        result = advance_on_task_completion(
            db_path, "01", "section.post_complete", success=True,
        )
        assert result == SectionState.COMPLETE.value

    # --- context-dependent task types ---

    def test_problem_frame_valid(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROBLEM_FRAME)
        result = advance_on_task_completion(
            db_path, "01", "section.problem_frame",
            success=True, context={"valid": True},
        )
        assert result == SectionState.INTENT_TRIAGE.value

    def test_problem_frame_invalid(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROBLEM_FRAME)
        result = advance_on_task_completion(
            db_path, "01", "section.problem_frame",
            success=True, context={"valid": False},
        )
        assert result == SectionState.BLOCKED.value

    def test_philosophy_ready(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PHILOSOPHY_BOOTSTRAP)
        result = advance_on_task_completion(
            db_path, "01", "section.philosophy",
            success=True, context={"ready": True},
        )
        assert result == SectionState.INTENT_PACK.value

    def test_philosophy_blocked(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PHILOSOPHY_BOOTSTRAP)
        result = advance_on_task_completion(
            db_path, "01", "section.philosophy",
            success=True, context={"ready": False},
        )
        assert result == SectionState.BLOCKED.value

    def test_assess_aligned(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.ASSESSING)
        result = advance_on_task_completion(
            db_path, "01", "section.assess",
            success=True, context={"aligned": True},
        )
        assert result == SectionState.READINESS.value

    def test_assess_not_aligned(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.ASSESSING)
        result = advance_on_task_completion(
            db_path, "01", "section.assess",
            success=True, context={"aligned": False},
        )
        assert result == SectionState.PROPOSING.value

    def test_risk_eval_accepted(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.RISK_EVAL)
        result = advance_on_task_completion(
            db_path, "01", "section.risk_eval",
            success=True, context={"outcome": "accepted"},
        )
        assert result == SectionState.MICROSTRATEGY.value

    def test_risk_eval_deferred(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.RISK_EVAL)
        result = advance_on_task_completion(
            db_path, "01", "section.risk_eval",
            success=True, context={"outcome": "deferred"},
        )
        assert result == SectionState.BLOCKED.value

    def test_risk_eval_reopened(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.RISK_EVAL)
        result = advance_on_task_completion(
            db_path, "01", "section.risk_eval",
            success=True, context={"outcome": "reopened"},
        )
        assert result == SectionState.BLOCKED.value

    def test_impl_assess_aligned(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.IMPL_ASSESSING)
        result = advance_on_task_completion(
            db_path, "01", "section.impl_assess",
            success=True, context={"aligned": True},
        )
        assert result == SectionState.VERIFYING.value

    def test_impl_assess_not_aligned(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.IMPL_ASSESSING)
        result = advance_on_task_completion(
            db_path, "01", "section.impl_assess",
            success=True, context={"aligned": False},
        )
        assert result == SectionState.IMPLEMENTING.value

    # --- legacy readiness_check support ---

    def test_readiness_check_ready(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.READINESS)
        result = advance_on_task_completion(
            db_path, "01", "section.readiness_check",
            success=True, context={"ready": True},
        )
        assert result == SectionState.RISK_EVAL.value

    def test_readiness_check_not_ready(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.READINESS)
        result = advance_on_task_completion(
            db_path, "01", "section.readiness_check",
            success=True, context={"ready": False},
        )
        assert result == SectionState.BLOCKED.value

    # --- error handling ---

    def test_wrong_state_returns_none(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.COMPLETE)
        result = advance_on_task_completion(
            db_path, "01", "section.propose", success=True,
        )
        assert result is None

    def test_unknown_task_type_returns_none(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)
        result = advance_on_task_completion(
            db_path, "01", "research.plan", success=True,
        )
        assert result is None

    def test_failure_for_context_task_goes_to_failed(self, db_path: Path) -> None:
        """All context-dependent task types go to FAILED on failure."""
        for task_type, state in [
            ("section.problem_frame", SectionState.PROBLEM_FRAME),
            ("section.philosophy", SectionState.PHILOSOPHY_BOOTSTRAP),
            ("section.assess", SectionState.ASSESSING),
            ("section.risk_eval", SectionState.RISK_EVAL),
            ("section.impl_assess", SectionState.IMPL_ASSESSING),
        ]:
            set_section_state(db_path, "01", state)
            result = advance_on_task_completion(
                db_path, "01", task_type, success=False,
            )
            assert result == SectionState.FAILED.value, (
                f"{task_type} failure should go to FAILED"
            )


# ---------------------------------------------------------------------------
# Circuit breaker (via advance_section in section_state_machine)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_proposing_escalates_after_max_attempts(self, db_path: Path) -> None:
        limit = _CIRCUIT_BREAKER_LIMITS.get(SectionState.PROPOSING, 5)
        set_section_state(db_path, "01", SectionState.ASSESSING)
        escalated = False

        for _i in range(limit + 2):
            new_state = advance_section(
                db_path, "01", SectionEvent.alignment_fail,
            )
            if new_state == SectionState.ESCALATED:
                escalated = True
                break
            # Return to ASSESSING to set up the next cycle
            set_section_state(db_path, "01", SectionState.ASSESSING)

        assert escalated, (
            f"Expected ESCALATED after {limit} entries into PROPOSING, "
            f"but ended in {get_section_state(db_path, '01').value}"
        )

    def test_implementing_escalates_after_max_attempts(self, db_path: Path) -> None:
        limit = _CIRCUIT_BREAKER_LIMITS.get(SectionState.IMPLEMENTING, 3)
        set_section_state(db_path, "01", SectionState.IMPL_ASSESSING)
        escalated = False

        for _i in range(limit + 2):
            new_state = advance_section(
                db_path, "01", SectionEvent.impl_alignment_fail,
            )
            if new_state == SectionState.ESCALATED:
                escalated = True
                break
            set_section_state(db_path, "01", SectionState.IMPL_ASSESSING)

        assert escalated, (
            f"Expected ESCALATED after {limit} entries into IMPLEMENTING"
        )

    def test_state_change_does_not_trigger_breaker(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)
        # proposal_complete goes PROPOSING -> ASSESSING (different state)
        new = advance_section(db_path, "01", SectionEvent.proposal_complete)
        assert new == SectionState.ASSESSING


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_section_progresses_through_all_states(self, db_path: Path) -> None:
        """Drive a section through the full happy path with all new states."""
        set_section_state(db_path, "01", SectionState.PENDING)

        # PENDING -> EXCERPT_EXTRACTION
        advance_section(db_path, "01", SectionEvent.excerpt_complete)
        assert get_section_state(db_path, "01") == SectionState.EXCERPT_EXTRACTION

        # EXCERPT_EXTRACTION -> PROBLEM_FRAME
        advance_section(db_path, "01", SectionEvent.excerpt_complete)
        assert get_section_state(db_path, "01") == SectionState.PROBLEM_FRAME

        # PROBLEM_FRAME -> INTENT_TRIAGE
        advance_section(db_path, "01", SectionEvent.problem_frame_valid)
        assert get_section_state(db_path, "01") == SectionState.INTENT_TRIAGE

        # INTENT_TRIAGE -> PHILOSOPHY_BOOTSTRAP
        advance_section(db_path, "01", SectionEvent.triage_complete)
        assert get_section_state(db_path, "01") == SectionState.PHILOSOPHY_BOOTSTRAP

        # PHILOSOPHY_BOOTSTRAP -> INTENT_PACK
        advance_section(db_path, "01", SectionEvent.philosophy_ready)
        assert get_section_state(db_path, "01") == SectionState.INTENT_PACK

        # INTENT_PACK -> PROPOSING
        advance_section(db_path, "01", SectionEvent.intent_pack_complete)
        assert get_section_state(db_path, "01") == SectionState.PROPOSING

        # PROPOSING -> ASSESSING
        advance_section(db_path, "01", SectionEvent.proposal_complete)
        assert get_section_state(db_path, "01") == SectionState.ASSESSING

        # ASSESSING -> READINESS
        advance_section(db_path, "01", SectionEvent.alignment_pass)
        assert get_section_state(db_path, "01") == SectionState.READINESS

        # READINESS -> RISK_EVAL
        advance_section(db_path, "01", SectionEvent.readiness_pass)
        assert get_section_state(db_path, "01") == SectionState.RISK_EVAL

        # RISK_EVAL -> MICROSTRATEGY
        advance_section(db_path, "01", SectionEvent.risk_accepted)
        assert get_section_state(db_path, "01") == SectionState.MICROSTRATEGY

        # MICROSTRATEGY -> IMPLEMENTING
        advance_section(db_path, "01", SectionEvent.microstrategy_complete)
        assert get_section_state(db_path, "01") == SectionState.IMPLEMENTING

        # IMPLEMENTING -> IMPL_ASSESSING
        advance_section(db_path, "01", SectionEvent.implementation_complete)
        assert get_section_state(db_path, "01") == SectionState.IMPL_ASSESSING

        # IMPL_ASSESSING -> VERIFYING
        advance_section(db_path, "01", SectionEvent.impl_alignment_pass)
        assert get_section_state(db_path, "01") == SectionState.VERIFYING

        # VERIFYING -> POST_COMPLETION
        advance_section(db_path, "01", SectionEvent.verification_pass)
        assert get_section_state(db_path, "01") == SectionState.POST_COMPLETION

        # POST_COMPLETION -> COMPLETE
        advance_section(db_path, "01", SectionEvent.post_completion_done)
        assert get_section_state(db_path, "01") == SectionState.COMPLETE

        assert all_sections_terminal(db_path)

    def test_section_blocks_and_unblocks(self, db_path: Path) -> None:
        set_section_state(db_path, "02", SectionState.RISK_EVAL)

        # RISK_EVAL -> BLOCKED via risk_deferred
        advance_section(db_path, "02", SectionEvent.risk_deferred)
        assert get_section_state(db_path, "02") == SectionState.BLOCKED

        # BLOCKED -> PROPOSING via info_available
        advance_section(db_path, "02", SectionEvent.info_available)
        assert get_section_state(db_path, "02") == SectionState.PROPOSING

    def test_proposal_alignment_loop_then_pass(self, db_path: Path) -> None:
        """Section bounces ASSESSING -> PROPOSING then passes."""
        set_section_state(db_path, "03", SectionState.PROPOSING)

        # First: propose -> assess -> fail -> back to proposing
        advance_section(db_path, "03", SectionEvent.proposal_complete)
        assert get_section_state(db_path, "03") == SectionState.ASSESSING

        advance_section(db_path, "03", SectionEvent.alignment_fail)
        assert get_section_state(db_path, "03") == SectionState.PROPOSING

        # Second: propose -> assess -> pass -> readiness
        advance_section(db_path, "03", SectionEvent.proposal_complete)
        assert get_section_state(db_path, "03") == SectionState.ASSESSING

        advance_section(db_path, "03", SectionEvent.alignment_pass)
        assert get_section_state(db_path, "03") == SectionState.READINESS

    def test_impl_alignment_loop_then_pass(self, db_path: Path) -> None:
        """Section bounces IMPL_ASSESSING -> IMPLEMENTING then passes."""
        set_section_state(db_path, "04", SectionState.IMPLEMENTING)

        # First: implement -> impl_assess -> fail -> back to implementing
        advance_section(db_path, "04", SectionEvent.implementation_complete)
        assert get_section_state(db_path, "04") == SectionState.IMPL_ASSESSING

        advance_section(db_path, "04", SectionEvent.impl_alignment_fail)
        assert get_section_state(db_path, "04") == SectionState.IMPLEMENTING

        # Second: implement -> impl_assess -> pass -> verifying
        advance_section(db_path, "04", SectionEvent.implementation_complete)
        assert get_section_state(db_path, "04") == SectionState.IMPL_ASSESSING

        advance_section(db_path, "04", SectionEvent.impl_alignment_pass)
        assert get_section_state(db_path, "04") == SectionState.VERIFYING


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


def _set_parent_section(db_path: Path, section_number: str, parent: str) -> None:
    """Set the parent_section column for a section row (test helper)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE section_states SET parent_section = ? WHERE section_number = ?",
        (parent, section_number),
    )
    conn.commit()
    conn.close()


class TestCheckOrphanedChildren:
    """Tests for _check_orphaned_children."""

    def test_orphan_failed_when_parent_complete(self, db_path: Path) -> None:
        """Child in PROPOSING should be FAILED when parent is COMPLETE."""
        set_section_state(db_path, "3", SectionState.COMPLETE)
        set_section_state(db_path, "3.1", SectionState.PROPOSING)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "3.1") == SectionState.FAILED

    def test_orphan_failed_when_parent_failed(self, db_path: Path) -> None:
        """Child should be FAILED when parent is FAILED."""
        set_section_state(db_path, "3", SectionState.FAILED)
        set_section_state(db_path, "3.1", SectionState.IMPLEMENTING)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "3.1") == SectionState.FAILED

    def test_orphan_failed_when_parent_escalated(self, db_path: Path) -> None:
        """Child should be FAILED when parent is ESCALATED."""
        set_section_state(db_path, "3", SectionState.ESCALATED)
        set_section_state(db_path, "3.1", SectionState.BLOCKED)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "3.1") == SectionState.FAILED

    def test_orphan_sets_error_parent_terminated(self, db_path: Path) -> None:
        """Orphaned child should have error='parent_terminated'."""
        set_section_state(db_path, "3", SectionState.COMPLETE)
        set_section_state(db_path, "3.1", SectionState.PROPOSING)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            "SELECT * FROM section_states WHERE section_number = '3.1'"
        ).fetchone())
        conn.close()
        assert row["error"] == "parent_terminated"

    def test_already_terminal_child_not_touched(self, db_path: Path) -> None:
        """A child that is already COMPLETE is not moved to FAILED."""
        set_section_state(db_path, "3", SectionState.COMPLETE)
        set_section_state(db_path, "3.1", SectionState.COMPLETE)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "3.1") == SectionState.COMPLETE

    def test_active_parent_children_not_touched(self, db_path: Path) -> None:
        """Children of an active parent are not orphaned."""
        set_section_state(db_path, "3", SectionState.PROPOSING)
        set_section_state(db_path, "3.1", SectionState.IMPLEMENTING)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "3.1") == SectionState.IMPLEMENTING

    def test_root_sections_unaffected(self, db_path: Path) -> None:
        """Root sections (no parent) are never touched by orphan cleanup."""
        set_section_state(db_path, "1", SectionState.PROPOSING)
        set_section_state(db_path, "2", SectionState.IMPLEMENTING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "1") == SectionState.PROPOSING
        assert get_section_state(db_path, "2") == SectionState.IMPLEMENTING

    def test_no_rows_is_noop(self, db_path: Path) -> None:
        """Empty section_states does not error."""
        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)
        # No error raised

    def test_multiple_orphans(self, db_path: Path) -> None:
        """Multiple children of a terminated parent are all failed."""
        set_section_state(db_path, "3", SectionState.FAILED)
        set_section_state(db_path, "3.1", SectionState.PROPOSING)
        set_section_state(db_path, "3.2", SectionState.IMPLEMENTING)
        set_section_state(db_path, "3.3", SectionState.COMPLETE)
        _set_parent_section(db_path, "3.1", "3")
        _set_parent_section(db_path, "3.2", "3")
        _set_parent_section(db_path, "3.3", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        assert get_section_state(db_path, "3.1") == SectionState.FAILED
        assert get_section_state(db_path, "3.2") == SectionState.FAILED
        # 3.3 was already COMPLETE -- not touched
        assert get_section_state(db_path, "3.3") == SectionState.COMPLETE

    def test_logs_orphan_cleanup(self, db_path: Path) -> None:
        """Orphan cleanup logs a message for each orphaned child."""
        set_section_state(db_path, "3", SectionState.COMPLETE)
        set_section_state(db_path, "3.1", SectionState.PROPOSING)
        _set_parent_section(db_path, "3.1", "3")

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._check_orphaned_children(db_path)

        log_calls = [str(c) for c in logger.log.call_args_list]
        assert any("orphan cleanup" in c.lower() for c in log_calls)


# ---------------------------------------------------------------------------
# Transition log
# ---------------------------------------------------------------------------


class TestTransitionLog:
    def test_transition_log_accumulates(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        advance_section(db_path, "01", SectionEvent.excerpt_complete)
        advance_section(db_path, "01", SectionEvent.excerpt_complete)
        advance_section(db_path, "01", SectionEvent.problem_frame_valid)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r) for r in conn.execute(
                "SELECT * FROM section_transitions WHERE section_number='01' "
                "ORDER BY id"
            ).fetchall()
        ]
        conn.close()

        assert len(rows) == 3
        assert rows[0]["from_state"] == SectionState.PENDING.value
        assert rows[0]["to_state"] == SectionState.EXCERPT_EXTRACTION.value
        assert rows[1]["from_state"] == SectionState.EXCERPT_EXTRACTION.value
        assert rows[1]["to_state"] == SectionState.PROBLEM_FRAME.value
        assert rows[2]["from_state"] == SectionState.PROBLEM_FRAME.value
        assert rows[2]["to_state"] == SectionState.INTENT_TRIAGE.value
