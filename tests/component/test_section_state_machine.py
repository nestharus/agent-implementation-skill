"""Component tests for the section state machine.

Verifies:
- State transitions follow the expanded transition table
- Invalid transitions raise InvalidTransitionError
- Observational re-entry guards gate PROPOSING and IMPLEMENTING retries
- DB operations (get/set/record) work correctly
- Wildcard transitions (error, timeout) from any non-terminal state
- Query helpers (get_sections_in_state, get_actionable_sections)
- Full lifecycle through all new states (excerpt -> complete)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from flow.service.task_db_client import init_db
from orchestrator.engine.section_state_machine import (
    TRANSITIONS,
    InvalidTransitionError,
    SectionEvent,
    SectionState,
    Transition,
    advance_section,
    get_actionable_sections,
    get_section_state,
    get_sections_in_state,
    record_transition,
    set_section_state,
)
from orchestrator.path_registry import PathRegistry


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Create a fresh DB with the full schema (including section tables)."""
    db_path = tmp_path / "run.db"
    init_db(db_path)
    return db_path


def _paths_for(db_path: Path) -> PathRegistry:
    paths = PathRegistry(db_path.parent)
    paths.ensure_artifacts_tree()
    return paths


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, indent=2) + "\n")


def _seed_proposing_inputs(
    db_path: Path,
    section_number: str,
    *,
    label: str,
    include_impl_feedback: bool = True,
) -> None:
    paths = _paths_for(db_path)
    intent_dir = paths.intent_section_dir(section_number)
    _write_text(paths.problem_frame(section_number), f"problem frame {label}\n")
    _write_text(intent_dir / "problem.md", f"problem {label}\n")
    _write_text(intent_dir / "problem-alignment.md", f"alignment {label}\n")
    _write_json(intent_dir / "surface-registry.json", {"surfaces": [label]})
    _write_json(
        paths.research_derived_surfaces(section_number),
        {"surfaces": [{"title": label}]},
    )
    if include_impl_feedback:
        _write_json(
            paths.impl_feedback_surfaces(section_number),
            {"surfaces": [{"title": f"impl-{label}"}]},
        )


def _seed_implementing_inputs(
    db_path: Path,
    section_number: str,
    *,
    label: str,
    include_optional: bool = True,
) -> None:
    paths = _paths_for(db_path)
    _write_json(paths.execution_ready(section_number), {"ready": True, "label": label})
    _write_json(
        paths.risk_accepted_steps(section_number),
        {"accepted_steps": [label], "posture": "accepted"},
    )
    _write_text(paths.microstrategy(section_number), f"microstrategy {label}\n")
    if include_optional:
        _write_json(
            paths.post_impl_assessment(section_number),
            {"status": "needs_followup", "label": label},
        )
        _write_json(
            paths.verification_status(section_number),
            {"status": "fail", "label": label},
        )
        _write_json(
            paths.testing_rca_findings(section_number),
            {"findings": [label]},
        )


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
            SectionState.PENDING, SectionState.EXCERPT_EXTRACTION,
            SectionEvent.excerpt_complete,
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
        assert row[1] == "excerpt_extraction"
        assert row[2] == "excerpt_complete"
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
            # --- excerpt / problem-frame ---
            (SectionState.PENDING, SectionEvent.excerpt_complete, SectionState.EXCERPT_EXTRACTION),
            (SectionState.EXCERPT_EXTRACTION, SectionEvent.excerpt_complete, SectionState.PROBLEM_FRAME),
            (SectionState.PROBLEM_FRAME, SectionEvent.problem_frame_valid, SectionState.INTENT_TRIAGE),
            (SectionState.PROBLEM_FRAME, SectionEvent.problem_frame_invalid, SectionState.BLOCKED),
            # --- intent ---
            (SectionState.INTENT_TRIAGE, SectionEvent.triage_complete, SectionState.PHILOSOPHY_BOOTSTRAP),
            (SectionState.PHILOSOPHY_BOOTSTRAP, SectionEvent.philosophy_ready, SectionState.INTENT_PACK),
            (SectionState.PHILOSOPHY_BOOTSTRAP, SectionEvent.philosophy_blocked, SectionState.BLOCKED),
            (SectionState.INTENT_PACK, SectionEvent.intent_pack_complete, SectionState.PROPOSING),
            # --- proposal ---
            (SectionState.PROPOSING, SectionEvent.proposal_complete, SectionState.ASSESSING),
            # --- assessment ---
            (SectionState.ASSESSING, SectionEvent.alignment_pass, SectionState.READINESS),
            (SectionState.ASSESSING, SectionEvent.alignment_fail, SectionState.PROPOSING),
            (SectionState.ASSESSING, SectionEvent.vertical_misalignment, SectionState.PROPOSING),
            # --- readiness ---
            (SectionState.READINESS, SectionEvent.readiness_pass, SectionState.RISK_EVAL),
            (SectionState.READINESS, SectionEvent.readiness_blocked, SectionState.BLOCKED),
            # --- risk ---
            (SectionState.RISK_EVAL, SectionEvent.risk_accepted, SectionState.MICROSTRATEGY),
            (SectionState.RISK_EVAL, SectionEvent.risk_deferred, SectionState.BLOCKED),
            (SectionState.RISK_EVAL, SectionEvent.risk_reopened, SectionState.BLOCKED),
            # --- microstrategy ---
            (SectionState.MICROSTRATEGY, SectionEvent.microstrategy_complete, SectionState.IMPLEMENTING),
            # --- implementation ---
            (SectionState.IMPLEMENTING, SectionEvent.implementation_complete, SectionState.IMPL_ASSESSING),
            # --- implementation assessment ---
            (SectionState.IMPL_ASSESSING, SectionEvent.impl_alignment_pass, SectionState.VERIFYING),
            (SectionState.IMPL_ASSESSING, SectionEvent.impl_alignment_fail, SectionState.IMPLEMENTING),
            # --- verification ---
            (SectionState.VERIFYING, SectionEvent.verification_pass, SectionState.POST_COMPLETION),
            (SectionState.VERIFYING, SectionEvent.verification_fail, SectionState.IMPLEMENTING),
            # --- post-completion ---
            (SectionState.POST_COMPLETION, SectionEvent.post_completion_done, SectionState.COMPLETE),
            # --- blocked ---
            (SectionState.BLOCKED, SectionEvent.info_available, SectionState.PROPOSING),
            # --- fractal descent / reassembly ---
            (SectionState.READINESS, SectionEvent.descent_required, SectionState.DECOMPOSING),
            (SectionState.DECOMPOSING, SectionEvent.excerpt_complete, SectionState.AWAITING_CHILDREN),
            (SectionState.AWAITING_CHILDREN, SectionEvent.children_complete, SectionState.REASSEMBLING),
            (SectionState.AWAITING_CHILDREN, SectionEvent.children_partial, SectionState.REASSEMBLING),
            (SectionState.REASSEMBLING, SectionEvent.reassembly_complete, SectionState.POST_COMPLETION),
            (SectionState.SCOPE_EXPANSION, SectionEvent.info_available, SectionState.PROPOSING),
        ],
        ids=[
            "pending->excerpt_extraction",
            "excerpt->problem_frame",
            "problem_frame->intent_triage(valid)",
            "problem_frame->blocked(invalid)",
            "triage->philosophy",
            "philosophy->intent_pack(ready)",
            "philosophy->blocked",
            "intent_pack->proposing",
            "proposing->assessing",
            "assessing->readiness(pass)",
            "assessing->proposing(fail)",
            "assessing->proposing(vertical-misalignment)",
            "readiness->risk_eval(pass)",
            "readiness->blocked",
            "risk_eval->microstrategy",
            "risk_eval->blocked(deferred)",
            "risk_eval->blocked(reopened)",
            "microstrategy->implementing",
            "implementing->impl_assessing",
            "impl_assessing->verifying(pass)",
            "impl_assessing->implementing(fail)",
            "verifying->post_completion(pass)",
            "verifying->implementing(fail)",
            "post_completion->complete",
            "blocked->proposing",
            "readiness->decomposing",
            "decomposing->awaiting_children",
            "awaiting_children->reassembling(complete)",
            "awaiting_children->reassembling(partial)",
            "reassembling->post_completion",
            "scope_expansion->proposing",
        ],
    )
    def test_transition(
        self, db: Path, initial: SectionState,
        event: SectionEvent, expected: SectionState,
    ) -> None:
        set_section_state(db, "01", initial)
        result = advance_section(db, "01", event)
        assert result == expected

    def test_impl_feedback_detected_transitions_implementing_to_blocked(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.IMPLEMENTING)
        result = advance_section(
            db,
            "01",
            SectionEvent.impl_feedback_detected,
            context={
                "implementation_feedback_detected": True,
                "blocker_type": "implementation_feedback",
                "blocked_reason": "implementation_feedback",
            },
        )

        assert result == SectionState.BLOCKED

    def test_info_available_transitions_blocked_to_proposing_after_impl_feedback(
        self,
        db: Path,
    ) -> None:
        set_section_state(
            db,
            "01",
            SectionState.BLOCKED,
            blocked_reason="implementation_feedback",
            context={"blocker_type": "implementation_feedback"},
        )

        result = advance_section(
            db,
            "01",
            SectionEvent.info_available,
            context={"unblocked_from": "implementation_feedback"},
        )

        assert result == SectionState.PROPOSING
        assert get_section_state(db, "01") == SectionState.PROPOSING


class TestInvalidTransitions:
    """Events that have no transition from the current state raise."""

    def test_complete_rejects_all_non_wildcard_events(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.COMPLETE)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.excerpt_complete)

    def test_failed_rejects_all_non_wildcard_events(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.FAILED)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.proposal_complete)

    def test_pending_rejects_proposal_complete(self, db: Path) -> None:
        # PENDING only accepts excerpt_complete (plus wildcards)
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

    def test_proposing_rejects_impl_alignment_pass(self, db: Path) -> None:
        """PROPOSING does not accept impl_alignment_pass."""
        set_section_state(db, "01", SectionState.PROPOSING)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.impl_alignment_pass)

    def test_readiness_rejects_proposal_complete(self, db: Path) -> None:
        """READINESS only accepts readiness_pass/readiness_blocked."""
        set_section_state(db, "01", SectionState.READINESS)
        with pytest.raises(InvalidTransitionError):
            advance_section(db, "01", SectionEvent.proposal_complete)


# ------------------------------------------------------------------
# Wildcard transitions (error, timeout)
# ------------------------------------------------------------------

class TestWildcardTransitions:
    """Error and timeout events apply to any non-terminal state."""

    @pytest.mark.parametrize("state", [
        SectionState.PENDING,
        SectionState.EXCERPT_EXTRACTION,
        SectionState.PROBLEM_FRAME,
        SectionState.INTENT_TRIAGE,
        SectionState.PHILOSOPHY_BOOTSTRAP,
        SectionState.INTENT_PACK,
        SectionState.PROPOSING,
        SectionState.ASSESSING,
        SectionState.READINESS,
        SectionState.RISK_EVAL,
        SectionState.MICROSTRATEGY,
        SectionState.IMPLEMENTING,
        SectionState.IMPL_ASSESSING,
        SectionState.VERIFYING,
        SectionState.POST_COMPLETION,
        SectionState.BLOCKED,
        SectionState.ESCALATED,
        SectionState.DECOMPOSING,
        SectionState.AWAITING_CHILDREN,
        SectionState.REASSEMBLING,
        SectionState.SCOPE_EXPANSION,
    ])
    def test_error_transitions_to_failed(self, db: Path, state: SectionState) -> None:
        set_section_state(db, "01", state)
        result = advance_section(db, "01", SectionEvent.error, {"error": "boom"})
        assert result == SectionState.FAILED

    @pytest.mark.parametrize("state", [
        SectionState.PENDING,
        SectionState.EXCERPT_EXTRACTION,
        SectionState.PROBLEM_FRAME,
        SectionState.INTENT_TRIAGE,
        SectionState.PHILOSOPHY_BOOTSTRAP,
        SectionState.INTENT_PACK,
        SectionState.PROPOSING,
        SectionState.ASSESSING,
        SectionState.READINESS,
        SectionState.RISK_EVAL,
        SectionState.MICROSTRATEGY,
        SectionState.IMPLEMENTING,
        SectionState.IMPL_ASSESSING,
        SectionState.VERIFYING,
        SectionState.POST_COMPLETION,
        SectionState.BLOCKED,
        SectionState.ESCALATED,
        SectionState.DECOMPOSING,
        SectionState.AWAITING_CHILDREN,
        SectionState.REASSEMBLING,
        SectionState.SCOPE_EXPANSION,
    ])
    def test_timeout_transitions_to_escalated(self, db: Path, state: SectionState) -> None:
        set_section_state(db, "01", state)
        result = advance_section(db, "01", SectionEvent.timeout)
        assert result == SectionState.ESCALATED


# ------------------------------------------------------------------
# Re-entry guards
# ------------------------------------------------------------------

class TestReentryGuards:
    """Re-entry depends on progress-stamp changes, not historical counts."""

    def test_proposing_reentry_with_changed_stamp_is_allowed(self, db: Path) -> None:
        set_section_state(
            db,
            "01",
            SectionState.ASSESSING,
            scope_grant="Initial scope grant",
        )
        _seed_proposing_inputs(db, "01", label="v1")

        first = advance_section(db, "01", SectionEvent.alignment_fail)
        assert first == SectionState.PROPOSING

        set_section_state(db, "01", SectionState.ASSESSING, scope_grant="Expanded scope grant")
        second = advance_section(db, "01", SectionEvent.alignment_fail)
        assert second == SectionState.PROPOSING

    def test_proposing_reentry_with_unchanged_stamp_escalates_root(self, db: Path) -> None:
        set_section_state(
            db,
            "01",
            SectionState.ASSESSING,
            scope_grant="Stable scope grant",
        )
        _seed_proposing_inputs(db, "01", label="stable")

        assert advance_section(db, "01", SectionEvent.alignment_fail) == SectionState.PROPOSING

        set_section_state(db, "01", SectionState.ASSESSING)
        assert advance_section(db, "01", SectionEvent.alignment_fail) == SectionState.ESCALATED

    def test_child_proposing_reentry_with_unchanged_stamp_routes_to_scope_expansion(
        self,
        db: Path,
    ) -> None:
        set_section_state(
            db,
            "01.1",
            SectionState.ASSESSING,
            parent_section="01",
            scope_grant="Stay within delegated auth changes only.",
        )
        _seed_proposing_inputs(db, "01.1", label="child-stable")

        assert advance_section(db, "01.1", SectionEvent.vertical_misalignment) == SectionState.PROPOSING

        set_section_state(db, "01.1", SectionState.ASSESSING)
        assert advance_section(db, "01.1", SectionEvent.vertical_misalignment) == SectionState.SCOPE_EXPANSION

    def test_implementing_reentry_with_changed_stamp_is_allowed(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.IMPL_ASSESSING)
        _seed_implementing_inputs(db, "01", label="v1")

        assert advance_section(db, "01", SectionEvent.impl_alignment_fail) == SectionState.IMPLEMENTING

        set_section_state(db, "01", SectionState.IMPL_ASSESSING)
        _seed_implementing_inputs(db, "01", label="v2")
        assert advance_section(db, "01", SectionEvent.impl_alignment_fail) == SectionState.IMPLEMENTING

    def test_implementing_reentry_with_unchanged_stamp_escalates(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.IMPL_ASSESSING)
        _seed_implementing_inputs(db, "01", label="stable")

        assert advance_section(db, "01", SectionEvent.impl_alignment_fail) == SectionState.IMPLEMENTING

        set_section_state(db, "01", SectionState.IMPL_ASSESSING)
        assert advance_section(db, "01", SectionEvent.impl_alignment_fail) == SectionState.ESCALATED

    def test_first_entry_into_guarded_state_is_always_allowed(self, db: Path) -> None:
        paths = _paths_for(db)
        set_section_state(db, "01", SectionState.INTENT_PACK)
        assert advance_section(db, "01", SectionEvent.intent_pack_complete) == SectionState.PROPOSING
        assert paths.reentry_stamp("01", SectionState.PROPOSING.value).exists()

        set_section_state(db, "02", SectionState.MICROSTRATEGY)
        assert advance_section(db, "02", SectionEvent.microstrategy_complete) == SectionState.IMPLEMENTING
        assert paths.reentry_stamp("02", SectionState.IMPLEMENTING.value).exists()

    def test_missing_stamp_inputs_hash_to_empty_stamp(self, db: Path) -> None:
        paths = _paths_for(db)
        set_section_state(db, "01", SectionState.ASSESSING)

        assert advance_section(db, "01", SectionEvent.alignment_fail) == SectionState.PROPOSING

        stamp_data = json.loads(
            paths.reentry_stamp("01", SectionState.PROPOSING.value).read_text(encoding="utf-8"),
        )
        assert stamp_data["stamp_hash"] == hashlib.sha256(b"").hexdigest()

    def test_root_scope_expansion_target_redirects_to_escalated(self, db: Path) -> None:
        """Root sections fail closed to ESCALATED instead of entering SCOPE_EXPANSION."""
        key = (SectionState.AWAITING_CHILDREN, SectionEvent.scope_expansion)
        original = TRANSITIONS.get(key)
        TRANSITIONS[key] = Transition(target_state=SectionState.SCOPE_EXPANSION)
        try:
            set_section_state(db, "01", SectionState.AWAITING_CHILDREN, depth=0)
            result = advance_section(db, "01", SectionEvent.scope_expansion)
        finally:
            if original is None:
                del TRANSITIONS[key]
            else:
                TRANSITIONS[key] = original

        assert result == SectionState.ESCALATED

    def test_non_guarded_state_change_advances_normally(self, db: Path) -> None:
        set_section_state(db, "01", SectionState.PROPOSING)
        assert advance_section(db, "01", SectionEvent.proposal_complete) == SectionState.ASSESSING


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

    def test_new_states_are_actionable(self, db: Path) -> None:
        """All new intermediate states are actionable (not blocked/terminal)."""
        for state in (
            SectionState.EXCERPT_EXTRACTION,
            SectionState.PROBLEM_FRAME,
            SectionState.INTENT_TRIAGE,
            SectionState.PHILOSOPHY_BOOTSTRAP,
            SectionState.INTENT_PACK,
            SectionState.READINESS,
            SectionState.MICROSTRATEGY,
            SectionState.IMPL_ASSESSING,
            SectionState.POST_COMPLETION,
            SectionState.DECOMPOSING,
            SectionState.REASSEMBLING,
        ):
            set_section_state(db, "01", state)
            result = get_actionable_sections(db)
            nums = [sn for sn, _ in result]
            assert "01" in nums, f"{state.value} should be actionable"

    def test_awaiting_and_scope_expansion_are_non_actionable(self, db: Path) -> None:
        """AWAITING_CHILDREN and SCOPE_EXPANSION are non-actionable."""
        for state in (
            SectionState.AWAITING_CHILDREN,
            SectionState.SCOPE_EXPANSION,
        ):
            set_section_state(db, "01", state)
            result = get_actionable_sections(db)
            nums = [sn for sn, _ in result]
            assert "01" not in nums, f"{state.value} should be non-actionable"

    def test_scope_expansion_is_not_terminal(self, db: Path) -> None:
        """SCOPE_EXPANSION is non-actionable but NOT terminal -- parent consumes it."""
        from orchestrator.engine.section_state_machine import _TERMINAL_STATES
        assert SectionState.SCOPE_EXPANSION not in _TERMINAL_STATES


# ------------------------------------------------------------------
# Full lifecycle
# ------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end walk through the happy path."""

    def test_happy_path_pending_to_complete(self, db: Path) -> None:
        sec = "01"

        # PENDING -> EXCERPT_EXTRACTION
        assert advance_section(db, sec, SectionEvent.excerpt_complete) == SectionState.EXCERPT_EXTRACTION
        # EXCERPT_EXTRACTION -> PROBLEM_FRAME
        assert advance_section(db, sec, SectionEvent.excerpt_complete) == SectionState.PROBLEM_FRAME
        # PROBLEM_FRAME -> INTENT_TRIAGE
        assert advance_section(db, sec, SectionEvent.problem_frame_valid) == SectionState.INTENT_TRIAGE
        # INTENT_TRIAGE -> PHILOSOPHY_BOOTSTRAP
        assert advance_section(db, sec, SectionEvent.triage_complete) == SectionState.PHILOSOPHY_BOOTSTRAP
        # PHILOSOPHY_BOOTSTRAP -> INTENT_PACK
        assert advance_section(db, sec, SectionEvent.philosophy_ready) == SectionState.INTENT_PACK
        # INTENT_PACK -> PROPOSING
        assert advance_section(db, sec, SectionEvent.intent_pack_complete) == SectionState.PROPOSING
        # PROPOSING -> ASSESSING
        assert advance_section(db, sec, SectionEvent.proposal_complete) == SectionState.ASSESSING
        # ASSESSING -> READINESS
        assert advance_section(db, sec, SectionEvent.alignment_pass) == SectionState.READINESS
        # READINESS -> RISK_EVAL
        assert advance_section(db, sec, SectionEvent.readiness_pass) == SectionState.RISK_EVAL
        # RISK_EVAL -> MICROSTRATEGY
        assert advance_section(db, sec, SectionEvent.risk_accepted) == SectionState.MICROSTRATEGY
        # MICROSTRATEGY -> IMPLEMENTING
        assert advance_section(db, sec, SectionEvent.microstrategy_complete) == SectionState.IMPLEMENTING
        # IMPLEMENTING -> IMPL_ASSESSING
        assert advance_section(db, sec, SectionEvent.implementation_complete) == SectionState.IMPL_ASSESSING
        # IMPL_ASSESSING -> VERIFYING
        assert advance_section(db, sec, SectionEvent.impl_alignment_pass) == SectionState.VERIFYING
        # VERIFYING -> POST_COMPLETION
        assert advance_section(db, sec, SectionEvent.verification_pass) == SectionState.POST_COMPLETION
        # POST_COMPLETION -> COMPLETE
        assert advance_section(db, sec, SectionEvent.post_completion_done) == SectionState.COMPLETE

        assert get_section_state(db, sec) == SectionState.COMPLETE

    def test_proposal_retry_then_succeed(self, db: Path) -> None:
        """PROPOSING -> ASSESSING -> PROPOSING (fail) -> ASSESSING -> READINESS."""
        sec = "02"
        set_section_state(db, sec, SectionState.PROPOSING)

        advance_section(db, sec, SectionEvent.proposal_complete)  # -> ASSESSING
        advance_section(db, sec, SectionEvent.alignment_fail)  # -> PROPOSING (retry)
        advance_section(db, sec, SectionEvent.proposal_complete)  # -> ASSESSING
        result = advance_section(db, sec, SectionEvent.alignment_pass)
        assert result == SectionState.READINESS

    def test_impl_retry_then_succeed(self, db: Path) -> None:
        """IMPLEMENTING -> IMPL_ASSESSING -> IMPLEMENTING (fail) -> pass."""
        sec = "03"
        set_section_state(db, sec, SectionState.IMPLEMENTING)

        advance_section(db, sec, SectionEvent.implementation_complete)  # -> IMPL_ASSESSING
        advance_section(db, sec, SectionEvent.impl_alignment_fail)  # -> IMPLEMENTING (retry)
        advance_section(db, sec, SectionEvent.implementation_complete)  # -> IMPL_ASSESSING
        result = advance_section(db, sec, SectionEvent.impl_alignment_pass)
        assert result == SectionState.VERIFYING

    def test_blocked_then_unblocked(self, db: Path) -> None:
        """RISK_EVAL -> BLOCKED (deferred) -> PROPOSING (info arrives)."""
        sec = "04"
        set_section_state(db, sec, SectionState.RISK_EVAL)

        advance_section(db, sec, SectionEvent.risk_deferred)
        assert get_section_state(db, sec) == SectionState.BLOCKED

        advance_section(db, sec, SectionEvent.info_available)
        assert get_section_state(db, sec) == SectionState.PROPOSING

    def test_philosophy_blocked_then_info(self, db: Path) -> None:
        """PHILOSOPHY_BOOTSTRAP -> BLOCKED -> PROPOSING."""
        sec = "05"
        set_section_state(db, sec, SectionState.PHILOSOPHY_BOOTSTRAP)

        advance_section(db, sec, SectionEvent.philosophy_blocked)
        assert get_section_state(db, sec) == SectionState.BLOCKED

        advance_section(db, sec, SectionEvent.info_available)
        assert get_section_state(db, sec) == SectionState.PROPOSING

    def test_readiness_blocked_then_info(self, db: Path) -> None:
        """READINESS -> BLOCKED -> PROPOSING."""
        sec = "06"
        set_section_state(db, sec, SectionState.READINESS)

        advance_section(db, sec, SectionEvent.readiness_blocked)
        assert get_section_state(db, sec) == SectionState.BLOCKED

        advance_section(db, sec, SectionEvent.info_available)
        assert get_section_state(db, sec) == SectionState.PROPOSING

    def test_verification_fail_goes_to_implementing(self, db: Path) -> None:
        """VERIFYING -> IMPLEMENTING on verification_fail (for re-attempt)."""
        sec = "07"
        set_section_state(db, sec, SectionState.VERIFYING)

        result = advance_section(db, sec, SectionEvent.verification_fail)
        assert result == SectionState.IMPLEMENTING

    def test_fractal_descent_happy_path(self, db: Path) -> None:
        """READINESS -> DECOMPOSING -> AWAITING_CHILDREN -> REASSEMBLING -> POST_COMPLETION."""
        sec = "08"
        set_section_state(db, sec, SectionState.READINESS)

        # READINESS -> DECOMPOSING (ROAL says decompose)
        assert advance_section(db, sec, SectionEvent.descent_required) == SectionState.DECOMPOSING
        # DECOMPOSING -> AWAITING_CHILDREN (children spawned)
        assert advance_section(db, sec, SectionEvent.excerpt_complete) == SectionState.AWAITING_CHILDREN
        # AWAITING_CHILDREN -> REASSEMBLING (all children done)
        assert advance_section(db, sec, SectionEvent.children_complete) == SectionState.REASSEMBLING
        # REASSEMBLING -> POST_COMPLETION
        assert advance_section(db, sec, SectionEvent.reassembly_complete) == SectionState.POST_COMPLETION

    def test_fractal_descent_partial_children(self, db: Path) -> None:
        """AWAITING_CHILDREN -> REASSEMBLING on children_partial."""
        sec = "09"
        set_section_state(db, sec, SectionState.AWAITING_CHILDREN)

        assert advance_section(db, sec, SectionEvent.children_partial) == SectionState.REASSEMBLING

    def test_scope_expansion_then_repropose(self, db: Path) -> None:
        """SCOPE_EXPANSION -> PROPOSING when parent absorbs and re-scopes."""
        sec = "10"
        set_section_state(db, sec, SectionState.SCOPE_EXPANSION)

        result = advance_section(db, sec, SectionEvent.info_available)
        assert result == SectionState.PROPOSING


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

    def test_every_non_terminal_state_has_at_least_one_transition(self) -> None:
        """Every non-terminal, non-blocked state appears as a source in TRANSITIONS."""
        terminal = {SectionState.COMPLETE, SectionState.FAILED, SectionState.ESCALATED}
        source_states = {state for (state, _) in TRANSITIONS}
        for state in SectionState:
            if state not in terminal:
                assert state in source_states, (
                    f"{state.value} has no outgoing transitions"
                )

    def test_all_new_states_exist_in_enum(self) -> None:
        """Verify all the states from the design spec exist."""
        expected = {
            "pending", "excerpt_extraction", "problem_frame",
            "intent_triage", "philosophy_bootstrap", "intent_pack",
            "proposing", "assessing", "readiness", "risk_eval",
            "microstrategy", "implementing", "impl_assessing",
            "verifying", "post_completion", "complete",
            "blocked", "escalated", "failed",
            # fractal layer states
            "decomposing", "awaiting_children", "reassembling",
            "scope_expansion",
        }
        actual = {s.value for s in SectionState}
        assert expected == actual
