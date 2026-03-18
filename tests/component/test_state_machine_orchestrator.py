"""Component tests for the state machine orchestrator.

Tests cover:
- Section state initialization (fresh + resume)
- DB helpers (set/get state, transitions)
- Task submission based on state
- Blocked section unblock checks
- State advancement on task completion (reconciler integration)
- Circuit breaker for self-transitions (via advance_section)
- Terminal state detection
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
            to_state=SectionState.PROPOSING,
            event=SectionEvent.bootstrap_complete,
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
        assert transitions[0]["to_state"] == SectionState.PROPOSING.value
        assert transitions[0]["event"] == SectionEvent.bootstrap_complete.value


class TestQueryHelpers:
    def test_get_actionable_sections(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        set_section_state(db_path, "02", SectionState.PROPOSING)
        set_section_state(db_path, "03", SectionState.READY)
        set_section_state(db_path, "04", SectionState.COMPLETE)
        set_section_state(db_path, "05", SectionState.BLOCKED)

        actionable = get_actionable_sections(db_path)
        nums = [num for num, _state in actionable]
        # PENDING, PROPOSING, READY, IMPLEMENTING, etc. are actionable
        # per the state machine module (everything except BLOCKED/COMPLETE/
        # FAILED/ESCALATED).
        assert "01" in nums  # PENDING
        assert "02" in nums  # PROPOSING
        assert "03" in nums  # READY
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

    def test_all_sections_terminal_empty(self, db_path: Path) -> None:
        assert all_sections_terminal(db_path)

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
    def test_pending_submits_propose(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._submit_for_state(
            db_path, planspace, "01", SectionState.PENDING,
            "/path/to/section-01.md",
        )

        assert flow_sub.submit_chain.called
        call_args = flow_sub.submit_chain.call_args
        steps = call_args[0][1]
        assert len(steps) == 1
        assert steps[0].task_type == "section.propose"
        assert steps[0].concern_scope == "section-01"

        # Verify state advanced away from PENDING
        new = get_section_state(db_path, "01")
        assert new != SectionState.PENDING

    def test_ready_submits_implement(self, db_path: Path, planspace: Path) -> None:
        set_section_state(db_path, "01", SectionState.READY)

        logger, artifact_io, flow_sub, pipeline_ctrl = _make_services()
        sm = StateMachineOrchestrator(
            logger_service=logger,
            artifact_io=artifact_io,
            flow_submitter=flow_sub,
            pipeline_control=pipeline_ctrl,
        )
        sm._submit_for_state(
            db_path, planspace, "01", SectionState.READY,
            "/path/to/section-01.md",
        )

        call_args = flow_sub.submit_chain.call_args
        steps = call_args[0][1]
        assert steps[0].task_type == "section.implement"

        new = get_section_state(db_path, "01")
        assert new == SectionState.IMPLEMENTING


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

        sm.run(db_path, planspace, {})
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

        sm.run(db_path, planspace, {"01": "/path/to/01.md"})
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

        sm.run(db_path, planspace, {"01": "/path/to/01.md"})

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

    def test_readiness_ready_advances_to_risk_eval(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.ASSESSING)
        result = advance_on_task_completion(
            db_path, "01", "section.readiness_check",
            success=True, context={"ready": True},
        )
        # ready=True maps to alignment_pass -> ASSESSING -> RISK_EVAL
        assert result == SectionState.RISK_EVAL.value
        assert get_section_state(db_path, "01") == SectionState.RISK_EVAL

    def test_readiness_not_ready_returns_to_proposing(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.ASSESSING)
        result = advance_on_task_completion(
            db_path, "01", "section.readiness_check",
            success=True, context={"ready": False},
        )
        # ready=False maps to alignment_fail -> ASSESSING -> PROPOSING
        assert result == SectionState.PROPOSING.value

    def test_implement_success_advances_to_verifying(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.IMPLEMENTING)
        result = advance_on_task_completion(
            db_path, "01", "section.implement", success=True,
        )
        assert result == SectionState.VERIFYING.value

    def test_verify_success_completes(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.VERIFYING)
        result = advance_on_task_completion(
            db_path, "01", "section.verify", success=True,
        )
        assert result == SectionState.COMPLETE.value

    def test_verify_failure_retries(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.VERIFYING)
        result = advance_on_task_completion(
            db_path, "01", "section.verify", success=False,
        )
        # verification_fail -> IMPLEMENTING per transition table
        assert result == SectionState.IMPLEMENTING.value

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


# ---------------------------------------------------------------------------
# Circuit breaker (via advance_section in section_state_machine)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_proposing_escalates_after_max_attempts(self, db_path: Path) -> None:
        limit = _CIRCUIT_BREAKER_LIMITS.get(SectionState.PROPOSING, 5)
        set_section_state(db_path, "01", SectionState.ASSESSING)
        escalated = False

        # Cycle through alignment_fail -> PROPOSING enough times to trip
        # the breaker.  The breaker fires when prior_entries + 1 > limit,
        # so we need limit + 1 attempts.
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

    def test_state_change_does_not_trigger_breaker(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PROPOSING)
        # proposal_complete goes PROPOSING -> ASSESSING (different state)
        new = advance_section(db_path, "01", SectionEvent.proposal_complete)
        assert new == SectionState.ASSESSING


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_section_progresses_through_states(self, db_path: Path) -> None:
        """Drive a section through the main happy path."""
        set_section_state(db_path, "01", SectionState.PENDING)

        # PENDING -> PROPOSING via bootstrap_complete
        advance_section(db_path, "01", SectionEvent.bootstrap_complete)
        assert get_section_state(db_path, "01") == SectionState.PROPOSING

        # PROPOSING -> ASSESSING via proposal_complete
        advance_section(db_path, "01", SectionEvent.proposal_complete)
        assert get_section_state(db_path, "01") == SectionState.ASSESSING

        # ASSESSING -> RISK_EVAL via alignment_pass
        advance_section(db_path, "01", SectionEvent.alignment_pass)
        assert get_section_state(db_path, "01") == SectionState.RISK_EVAL

        # RISK_EVAL -> IMPLEMENTING via risk_accepted
        advance_section(db_path, "01", SectionEvent.risk_accepted)
        assert get_section_state(db_path, "01") == SectionState.IMPLEMENTING

        # IMPLEMENTING -> VERIFYING via implementation_complete
        advance_section(db_path, "01", SectionEvent.implementation_complete)
        assert get_section_state(db_path, "01") == SectionState.VERIFYING

        # VERIFYING -> COMPLETE via verification_pass
        advance_section(db_path, "01", SectionEvent.verification_pass)
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

    def test_alignment_loop_then_pass(self, db_path: Path) -> None:
        """Section bounces ASSESSING -> PROPOSING then passes."""
        set_section_state(db_path, "03", SectionState.PROPOSING)

        # First: propose -> assess -> fail -> back to proposing
        advance_section(db_path, "03", SectionEvent.proposal_complete)
        assert get_section_state(db_path, "03") == SectionState.ASSESSING

        advance_section(db_path, "03", SectionEvent.alignment_fail)
        assert get_section_state(db_path, "03") == SectionState.PROPOSING

        # Second: propose -> assess -> pass
        advance_section(db_path, "03", SectionEvent.proposal_complete)
        assert get_section_state(db_path, "03") == SectionState.ASSESSING

        advance_section(db_path, "03", SectionEvent.alignment_pass)
        assert get_section_state(db_path, "03") == SectionState.RISK_EVAL


# ---------------------------------------------------------------------------
# Transition log
# ---------------------------------------------------------------------------


class TestTransitionLog:
    def test_transition_log_accumulates(self, db_path: Path) -> None:
        set_section_state(db_path, "01", SectionState.PENDING)
        advance_section(db_path, "01", SectionEvent.bootstrap_complete)
        advance_section(db_path, "01", SectionEvent.proposal_complete)
        advance_section(db_path, "01", SectionEvent.alignment_pass)

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
        assert rows[0]["to_state"] == SectionState.PROPOSING.value
        assert rows[1]["from_state"] == SectionState.PROPOSING.value
        assert rows[1]["to_state"] == SectionState.ASSESSING.value
        assert rows[2]["from_state"] == SectionState.ASSESSING.value
        assert rows[2]["to_state"] == SectionState.RISK_EVAL.value
