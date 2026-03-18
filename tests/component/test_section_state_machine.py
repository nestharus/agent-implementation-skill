"""Component tests for the section state machine.

Verifies:
- State transitions follow the transition table
- Invalid transitions raise InvalidTransitionError
- Circuit breaker fires at threshold
- DB operations (get/set/record) work correctly
- Wildcard transitions (error, timeout) from any non-terminal state
- Query helpers (get_sections_in_state, get_actionable_sections)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flow.service.task_db_client import init_db
from orchestrator.engine.section_state_machine import (
    TRANSITIONS,
    CircuitBreakerTripped,
    InvalidTransitionError,
    SectionEvent,
    SectionState,
    Transition,
    _CIRCUIT_BREAKER_LIMITS,
    _count_entries_into_state,
    advance_section,
    get_actionable_sections,
    get_section_state,
    get_sections_in_state,
    record_transition,
    set_section_state,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Create a fresh DB with the full schema (including section tables)."""
    db_path = tmp_path / "run.db"
    init_db(db_path)
    return db_path


# ------------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------------

class TestDBHelpers:
    """Tests for get_section_state, set_section_state, record_transition."""

    def test_get_returns_pending_for_unknown_section(self, db: Path) -> None:
        assert get_section_state(db, "99") == SectionState.PENDING

    def test_set_and_get_round_trip(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.PROPOSING)
        assert get_section_state(db, "01") == SectionState.PROPOSING

    def test_set_overwrites_previous_state(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.PROPOSING)
        set_section_state(db, "01", SectionState.ASSESSING)
        assert get_section_state(db, "01") == SectionState.ASSESSING

    def test_set_with_error_and_blocked_reason(self, db: Path) -> None:
        set_section_state(
            db, "03", SectionState.FAILED,
            error="something broke",
            blocked_reason=None,
            context={"detail": "traceback"},
        )
        assert get_section_state(db, "03") == SectionState.FAILED

    def test_record_transition_persists(self, db: Path) -> None:
        record_transition(
            db, "01",
            SectionState.PENDING, SectionState.PROPOSING,
            SectionEvent.bootstrap_complete,
            context={"intent_mode": "comprehensive"},
            attempt_number=1,
        )
        # Verify via raw query that the row exists
        from flow.service.task_db_client import task_db
        with task_db(db) as conn:
            row = conn.execute(
                "SELECT from_state, to_state, event, context_json, attempt_number "
                "FROM section_transitions WHERE section_number = '01'"
            ).fetchone()
        assert row is not None
        assert row[0] == "pending"
        assert row[1] == "proposing"
        assert row[2] == "bootstrap_complete"
        assert '"intent_mode"' in row[3]
        assert row[4] == 1


# ------------------------------------------------------------------
# Transition table coverage
# ------------------------------------------------------------------

class TestTransitionTable:
    """Every explicit transition in the table fires correctly."""

    @pytest.mark.parametrize(
        "initial, event, expected",
        [
            (SectionState.PENDING, SectionEvent.bootstrap_complete, SectionState.PROPOSING),
            (SectionState.PROPOSING, SectionEvent.proposal_complete, SectionState.ASSESSING),
            (SectionState.ASSESSING, SectionEvent.alignment_pass, SectionState.RISK_EVAL),
            (SectionState.ASSESSING, SectionEvent.alignment_fail, SectionState.PROPOSING),
            (SectionState.RISK_EVAL, SectionEvent.risk_accepted, SectionState.IMPLEMENTING),
            (SectionState.RISK_EVAL, SectionEvent.risk_deferred, SectionState.BLOCKED),
            (SectionState.RISK_EVAL, SectionEvent.risk_reopened, SectionState.BLOCKED),
            (SectionState.IMPLEMENTING, SectionEvent.implementation_complete, SectionState.VERIFYING),
            (SectionState.VERIFYING, SectionEvent.verification_pass, SectionState.COMPLETE),
            (SectionState.VERIFYING, SectionEvent.verification_fail, SectionState.IMPLEMENTING),
            (SectionState.BLOCKED, SectionEvent.info_available, SectionState.PROPOSING),
        ],
        ids=[
            "pending->proposing",
            "proposing->assessing",
            "assessing->risk_eval",
            "assessing->proposing(fail)",
            "risk_eval->implementing",
            "risk_eval->blocked(deferred)",
            "risk_eval->blocked(reopened)",
            "implementing->verifying",
            "verifying->complete",
            "verifying->implementing(fail)",
            "blocked->proposing",
        ],
    )
    def test_transition(
        self, db: Path, initial: SectionState,
        event: SectionEvent, expected: SectionState,
    ) -> None:
        set_section_state(db, "01", initial)
        result = advance_section(db, "01", event)
        assert result == expected
        assert get_section_state(db, "01") == expected


class TestInvalidTransitions:
    """Events that have no transition from the current state raise."""

    def test_complete_rejects_all_non_wildcard_events(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.COMPLETE)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.bootstrap_complete)

    def test_failed_rejects_all_non_wildcard_events(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.FAILED)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.proposal_complete)

    def test_pending_rejects_proposal_complete(self, db: Path) -> None:
        # PENDING only accepts bootstrap_complete (plus wildcards)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.proposal_complete)

    def test_implementing_rejects_alignment_pass(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.IMPLEMENTING)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.alignment_pass)

    def test_complete_rejects_error_wildcard(self, db: Path) -> None:
        """Terminal states reject even wildcard events."""
        set_section_state(db, "01", SectionState.COMPLETE)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.error)

    def test_failed_rejects_timeout_wildcard(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.FAILED)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.timeout)


# ------------------------------------------------------------------
# Wildcard transitions (error, timeout)
# ------------------------------------------------------------------

class TestWildcardTransitions:
    """Error and timeout events apply to any non-terminal state."""

    @pytest.mark.parametrize("state", [
        SectionState.PENDING,
        SectionState.PROPOSING,
        SectionState.ASSESSING,
        SectionState.RISK_EVAL,
        SectionState.IMPLEMENTING,
        SectionState.VERIFYING,
        SectionState.BLOCKED,
        SectionState.ESCALATED,
    ])
    def test_error_transitions_to_failed(self, db: Path, state: SectionState) -> None:
        set_section_state(db, "01", state)
        result = advance_section(db, "01", SectionEvent.error, {"error": "boom"})
        assert result == SectionState.FAILED

    @pytest.mark.parametrize("state", [
        SectionState.PENDING,
        SectionState.PROPOSING,
        SectionState.ASSESSING,
        SectionState.RISK_EVAL,
        SectionState.IMPLEMENTING,
        SectionState.VERIFYING,
        SectionState.BLOCKED,
        SectionState.ESCALATED,
    ])
    def test_timeout_transitions_to_escalated(self, db: Path, state: SectionState) -> None:
        set_section_state(db, "01", state)
        result = advance_section(db, "01", SectionEvent.timeout)
        assert result == SectionState.ESCALATED


# ------------------------------------------------------------------
# Circuit breaker
# ------------------------------------------------------------------

class TestCircuitBreaker:
    """Self-transitions escalate when the retry threshold is exceeded."""

    def test_proposing_escalates_after_5_retries(self, db: Path) -> None:
        """ASSESSING -> PROPOSING (alignment_fail) should escalate on attempt 6.

        Limit is 5.  After 5 entries into PROPOSING, the 6th attempt
        is redirected to ESCALATED.
        """
        set_section_state(db, "01", SectionState.PROPOSING)

        # 5 cycles of PROPOSING -> ASSESSING -> PROPOSING
        for i in range(5):
            advance_section(db, "01", SectionEvent.proposal_complete)  # -> ASSESSING
            result = advance_section(db, "01", SectionEvent.alignment_fail)  # -> PROPOSING
            # All 5 should still land in PROPOSING (entries 1..5)
            assert result == SectionState.PROPOSING, f"iteration {i} failed"

        # 6th attempt: PROPOSING -> ASSESSING -> would-be PROPOSING -> ESCALATED
        advance_section(db, "01", SectionEvent.proposal_complete)  # -> ASSESSING
        result = advance_section(db, "01", SectionEvent.alignment_fail)
        assert result == SectionState.ESCALATED

    def test_implementing_escalates_after_3_retries(self, db: Path) -> None:
        """VERIFYING -> IMPLEMENTING (verification_fail) escalates on attempt 4.

        Limit is 3.  After 3 entries into IMPLEMENTING, the 4th attempt
        is redirected to ESCALATED.
        """
        set_section_state(db, "01", SectionState.IMPLEMENTING)

        # 3 cycles of IMPLEMENTING -> VERIFYING -> IMPLEMENTING
        for i in range(3):
            advance_section(db, "01", SectionEvent.implementation_complete)  # -> VERIFYING
            result = advance_section(db, "01", SectionEvent.verification_fail)
            assert result == SectionState.IMPLEMENTING, f"iteration {i} failed"

        # 4th attempt: IMPLEMENTING -> VERIFYING -> would-be IMPLEMENTING -> ESCALATED
        advance_section(db, "01", SectionEvent.implementation_complete)
        result = advance_section(db, "01", SectionEvent.verification_fail)
        assert result == SectionState.ESCALATED

    def test_breaker_does_not_fire_below_threshold(self, db: Path) -> None:
        """One self-transition does not trigger the breaker."""
        set_section_state(db, "01", SectionState.PROPOSING)
        advance_section(db, "01", SectionEvent.proposal_complete)  # -> ASSESSING
        result = advance_section(db, "01", SectionEvent.alignment_fail)  # -> PROPOSING
        assert result == SectionState.PROPOSING

    def test_breaker_not_applied_to_non_limited_states(self, db: Path) -> None:
        """States without a threshold (e.g. BLOCKED) never trigger the breaker."""
        assert SectionState.BLOCKED not in _CIRCUIT_BREAKER_LIMITS


# ------------------------------------------------------------------
# Query helpers
# ------------------------------------------------------------------

class TestQueryHelpers:
    """Tests for get_sections_in_state and get_actionable_sections."""

    def test_get_sections_in_state_empty(self, db: Path) -> None:
        assert get_sections_in_state(db, SectionState.PROPOSING) == []

    def test_get_sections_in_state_returns_matching(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.PROPOSING)
        set_section_state(db, "02", SectionState.PROPOSING)
        set_section_state(db, "03", SectionState.ASSESSING)
        result = get_sections_in_state(db, SectionState.PROPOSING)
        assert result == ["01", "02"]

    def test_get_actionable_excludes_terminal_and_blocked(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.PROPOSING)
        set_section_state(db, "02", SectionState.COMPLETE)
        set_section_state(db, "03", SectionState.FAILED)
        set_section_state(db, "04", SectionState.BLOCKED)
        set_section_state(db, "05", SectionState.ESCALATED)
        set_section_state(db, "06", SectionState.IMPLEMENTING)

        result = get_actionable_sections(db)
        section_nums = [sn for sn, _ in result]
        assert "01" in section_nums
        assert "06" in section_nums
        assert "02" not in section_nums
        assert "03" not in section_nums
        assert "04" not in section_nums
        assert "05" not in section_nums

    def test_get_actionable_returns_state_tuples(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.RISK_EVAL)
        result = get_actionable_sections(db)
        assert result == [("01", SectionState.RISK_EVAL)]


# ------------------------------------------------------------------
# Full lifecycle
# ------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end walk through the happy path."""

    def test_happy_path_pending_to_complete(self, db: Path) -> None:
        sec = "01"

        # PENDING -> PROPOSING
        assert advance_section(db, sec, SectionEvent.bootstrap_complete) == SectionState.PROPOSING
        # PROPOSING -> ASSESSING
        assert advance_section(db, sec, SectionEvent.proposal_complete) == SectionState.ASSESSING
        # ASSESSING -> RISK_EVAL
        assert advance_section(db, sec, SectionEvent.alignment_pass) == SectionState.RISK_EVAL
        # RISK_EVAL -> IMPLEMENTING
        assert advance_section(db, sec, SectionEvent.risk_accepted) == SectionState.IMPLEMENTING
        # IMPLEMENTING -> VERIFYING
        assert advance_section(db, sec, SectionEvent.implementation_complete) == SectionState.VERIFYING
        # VERIFYING -> COMPLETE
        assert advance_section(db, sec, SectionEvent.verification_pass) == SectionState.COMPLETE

        assert get_section_state(db, sec) == SectionState.COMPLETE

    def test_retry_then_succeed(self, db: Path) -> None:
        """PROPOSING -> ASSESSING -> PROPOSING (fail) -> ASSESSING -> RISK_EVAL."""
        sec = "02"
        set_section_state(db, sec, SectionState.PENDING)

        advance_section(db, sec, SectionEvent.bootstrap_complete)  # -> PROPOSING
        advance_section(db, sec, SectionEvent.proposal_complete)  # -> ASSESSING
        advance_section(db, sec, SectionEvent.alignment_fail)  # -> PROPOSING (retry)
        advance_section(db, sec, SectionEvent.proposal_complete)  # -> ASSESSING
        result = advance_section(db, sec, SectionEvent.alignment_pass)
        assert result == SectionState.RISK_EVAL

    def test_blocked_then_unblocked(self, db: Path) -> None:
        """RISK_EVAL -> BLOCKED (deferred) -> PROPOSING (info arrives)."""
        sec = "03"
        set_section_state(db, sec, SectionState.RISK_EVAL)

        advance_section(db, sec, SectionEvent.risk_deferred)
        assert get_section_state(db, sec) == SectionState.BLOCKED

        advance_section(db, sec, SectionEvent.info_available)
        assert get_section_state(db, sec) == SectionState.PROPOSING


# ------------------------------------------------------------------
# Transition table structural checks
# ------------------------------------------------------------------

class TestTransitionTableStructure:
    """Verify structural invariants of the transition table."""

    def test_all_transitions_target_valid_states(self) -> None:
        for (_, _), t in TRANSITIONS.items():
            assert isinstance(t.target_state, SectionState)

    def test_no_transition_targets_pending(self) -> None:
        """PENDING is only an initial state, never a transition target."""
        for (_, _), t in TRANSITIONS.items():
            assert t.target_state != SectionState.PENDING

    def test_transition_table_keys_are_valid_enums(self) -> None:
        for (state, event) in TRANSITIONS:
            assert isinstance(state, SectionState)
            assert isinstance(event, SectionEvent)

    def test_circuit_breaker_limits_reference_valid_states(self) -> None:
        for state in _CIRCUIT_BREAKER_LIMITS:
            assert isinstance(state, SectionState)


# ------------------------------------------------------------------
# Consecutive entry counter
# ------------------------------------------------------------------

class TestEntryCounter:
    """Tests for _count_entries_into_state."""

    def test_zero_when_no_history(self, db: Path) -> None:
        assert _count_entries_into_state(db, "01", SectionState.PROPOSING) == 0

    def test_counts_all_entries_into_state(self, db: Path) -> None:
        record_transition(
            db, "01", SectionState.ASSESSING, SectionState.PROPOSING,
            SectionEvent.alignment_fail, attempt_number=1,
        )
        record_transition(
            db, "01", SectionState.PROPOSING, SectionState.ASSESSING,
            SectionEvent.proposal_complete, attempt_number=1,
        )
        record_transition(
            db, "01", SectionState.ASSESSING, SectionState.PROPOSING,
            SectionEvent.alignment_fail, attempt_number=2,
        )
        # Two entries into PROPOSING (regardless of intermediate ASSESSING)
        assert _count_entries_into_state(db, "01", SectionState.PROPOSING) == 2

    def test_does_not_count_entries_for_other_sections(self, db: Path) -> None:
        record_transition(
            db, "01", SectionState.ASSESSING, SectionState.PROPOSING,
            SectionEvent.alignment_fail, attempt_number=1,
        )
        record_transition(
            db, "02", SectionState.ASSESSING, SectionState.PROPOSING,
            SectionEvent.alignment_fail, attempt_number=1,
        )
        assert _count_entries_into_state(db, "01", SectionState.PROPOSING) == 1
