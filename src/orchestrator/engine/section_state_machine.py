"""Section state machine: transition table and DB-backed state management.

Each section is a state machine with its current state recorded in run.db.
Transitions are determined by events returned from single-shot handlers.
There are no loops -- self-transitions (e.g. PROPOSING -> PROPOSING on
alignment failure) replace while-true retry patterns.

A circuit breaker prevents unbounded self-transitions: if a section
exceeds the retry threshold for its current state, it escalates instead
of retrying.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from flow.service.task_db_client import task_db


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------

class SectionState(str, Enum):
    """States a section can occupy in the execution pipeline."""

    PENDING = "pending"
    EXPLORING = "exploring"
    PROPOSING = "proposing"
    ASSESSING = "assessing"
    RISK_EVAL = "risk_eval"
    READY = "ready"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


# Terminal states -- sections in these states cannot advance.
_TERMINAL_STATES = frozenset({
    SectionState.COMPLETE,
    SectionState.FAILED,
})

# Non-actionable states -- sections here need external input to advance.
_NON_ACTIONABLE_STATES = _TERMINAL_STATES | frozenset({
    SectionState.BLOCKED,
    SectionState.ESCALATED,
})


class SectionEvent(str, Enum):
    """Events that drive state transitions."""

    bootstrap_complete = "bootstrap_complete"
    proposal_complete = "proposal_complete"
    alignment_pass = "alignment_pass"
    alignment_fail = "alignment_fail"
    readiness_pass = "readiness_pass"
    readiness_blocked = "readiness_blocked"
    risk_accepted = "risk_accepted"
    risk_deferred = "risk_deferred"
    risk_reopened = "risk_reopened"
    implementation_complete = "implementation_complete"
    verification_pass = "verification_pass"
    verification_fail = "verification_fail"
    info_available = "info_available"
    timeout = "timeout"
    error = "error"

    def __str__(self) -> str:
        return self.value


# ------------------------------------------------------------------
# Transition definition
# ------------------------------------------------------------------

@dataclass(frozen=True)
class Transition:
    """A single entry in the transition table.

    Attributes:
        target_state: The state the section moves to.
        handler_name: Optional name of the function to call during
            the transition (looked up by the dispatcher).
        side_effects: Descriptive labels for side effects that the
            handler should produce (e.g. ``["write_proposal"]``).
    """

    target_state: SectionState
    handler_name: str | None = None
    side_effects: list[str] = field(default_factory=list)


# ------------------------------------------------------------------
# Transition table
# ------------------------------------------------------------------

TRANSITIONS: dict[tuple[SectionState, SectionEvent], Transition] = {
    # --- bootstrap / exploration ---
    (SectionState.PENDING, SectionEvent.bootstrap_complete): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_bootstrap_complete",
        side_effects=["write_exploration_artifacts"],
    ),

    # --- proposal ---
    (SectionState.PROPOSING, SectionEvent.proposal_complete): Transition(
        target_state=SectionState.ASSESSING,
        handler_name="handle_proposal_complete",
        side_effects=["write_proposal", "persist_proposal_state"],
    ),

    # --- assessment ---
    (SectionState.ASSESSING, SectionEvent.alignment_pass): Transition(
        target_state=SectionState.RISK_EVAL,
        handler_name="handle_alignment_pass",
        side_effects=["write_readiness_artifact"],
    ),
    (SectionState.ASSESSING, SectionEvent.alignment_fail): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_alignment_fail",
        side_effects=["attach_problems_context"],
    ),

    # --- risk evaluation ---
    (SectionState.RISK_EVAL, SectionEvent.risk_accepted): Transition(
        target_state=SectionState.IMPLEMENTING,
        handler_name="handle_risk_accepted",
        side_effects=["write_roal_artifacts", "write_accepted_frontier"],
    ),
    (SectionState.RISK_EVAL, SectionEvent.risk_deferred): Transition(
        target_state=SectionState.BLOCKED,
        handler_name="handle_risk_deferred",
        side_effects=["write_deferred_blocker", "request_coordination"],
    ),
    (SectionState.RISK_EVAL, SectionEvent.risk_reopened): Transition(
        target_state=SectionState.BLOCKED,
        handler_name="handle_risk_reopened",
        side_effects=["write_reopen_blocker", "request_reproposal"],
    ),

    # --- implementation ---
    (SectionState.IMPLEMENTING, SectionEvent.implementation_complete): Transition(
        target_state=SectionState.VERIFYING,
        handler_name="handle_implementation_complete",
        side_effects=["write_traceability", "submit_verification_chain"],
    ),

    # --- verification ---
    (SectionState.VERIFYING, SectionEvent.verification_pass): Transition(
        target_state=SectionState.COMPLETE,
        handler_name="handle_verification_pass",
        side_effects=["write_completion_signal"],
    ),
    (SectionState.VERIFYING, SectionEvent.verification_fail): Transition(
        target_state=SectionState.IMPLEMENTING,
        handler_name="handle_verification_fail",
        side_effects=["attach_verification_problems"],
    ),

    # --- blocked ---
    (SectionState.BLOCKED, SectionEvent.info_available): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_info_available",
        side_effects=["clear_blocker", "reenter_with_context"],
    ),
}

# Wildcard transitions: error and timeout apply to any non-terminal state.
_WILDCARD_EVENTS: dict[SectionEvent, Transition] = {
    SectionEvent.error: Transition(
        target_state=SectionState.FAILED,
        handler_name="handle_error",
        side_effects=["log_error"],
    ),
    SectionEvent.timeout: Transition(
        target_state=SectionState.ESCALATED,
        handler_name="handle_timeout",
        side_effects=["emit_needs_parent"],
    ),
}


def _lookup_transition(
    state: SectionState, event: SectionEvent,
) -> Transition:
    """Resolve transition from the table, falling back to wildcards.

    Raises ``InvalidTransitionError`` when no transition exists.
    """
    key = (state, event)
    if key in TRANSITIONS:
        return TRANSITIONS[key]
    if event in _WILDCARD_EVENTS and state not in _TERMINAL_STATES:
        return _WILDCARD_EVENTS[event]
    raise InvalidTransitionError(
        f"No transition from {state.value!r} on event {event.value!r}"
    )


# ------------------------------------------------------------------
# Circuit breaker thresholds
# ------------------------------------------------------------------

_CIRCUIT_BREAKER_LIMITS: dict[SectionState, int] = {
    SectionState.PROPOSING: 5,
    SectionState.IMPLEMENTING: 3,
}


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when an event has no valid transition from the current state."""


class CircuitBreakerTripped(Exception):
    """Raised (internally) when the circuit breaker fires.

    The section is moved to ESCALATED instead of re-entering the same state.
    """


# ------------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------------

def get_section_state(db_path: str | Path, section_number: str) -> SectionState:
    """Read the current state for a section from the DB.

    Returns ``SectionState.PENDING`` if the section has no row yet.
    """
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT state FROM section_states WHERE section_number = ?",
            (section_number,),
        ).fetchone()
    if row is None:
        return SectionState.PENDING
    return SectionState(row[0])


def set_section_state(
    db_path: str | Path,
    section_number: str,
    state: SectionState,
    *,
    error: str | None = None,
    blocked_reason: str | None = None,
    context: dict | None = None,
) -> None:
    """Write (upsert) the current state for a section."""
    context_json = json.dumps(context) if context else None
    with task_db(db_path) as conn:
        conn.execute(
            """INSERT INTO section_states
                   (section_number, state, updated_at, error, blocked_reason, context_json)
               VALUES (?, ?, datetime('now'), ?, ?, ?)
               ON CONFLICT(section_number) DO UPDATE SET
                   state = excluded.state,
                   updated_at = excluded.updated_at,
                   error = excluded.error,
                   blocked_reason = excluded.blocked_reason,
                   context_json = excluded.context_json""",
            (section_number, state.value, error, blocked_reason, context_json),
        )
        conn.commit()


def record_transition(
    db_path: str | Path,
    section_number: str,
    from_state: SectionState,
    to_state: SectionState,
    event: SectionEvent,
    *,
    context: dict | None = None,
    attempt_number: int = 1,
) -> None:
    """Append a row to the ``section_transitions`` history table."""
    context_json = json.dumps(context) if context else None
    with task_db(db_path) as conn:
        conn.execute(
            """INSERT INTO section_transitions
                   (section_number, from_state, to_state, event,
                    context_json, attempt_number, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                section_number,
                from_state.value,
                to_state.value,
                event.value,
                context_json,
                attempt_number,
            ),
        )
        conn.commit()


def _count_entries_into_state(
    db_path: str | Path,
    section_number: str,
    state: SectionState,
) -> int:
    """Count total transitions *into* ``state`` for a section.

    Used by the circuit breaker to detect repeated re-entry into the
    same state (e.g. ASSESSING -> PROPOSING five times).  Counts every
    transition row where ``to_state == state``, regardless of what
    intermediate states occurred in between.
    """
    with task_db(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM section_transitions
               WHERE section_number = ? AND to_state = ?""",
            (section_number, state.value),
        ).fetchone()
    return row[0] if row else 0


# ------------------------------------------------------------------
# Core state-advance function
# ------------------------------------------------------------------

def advance_section(
    db_path: str | Path,
    section_number: str,
    event: SectionEvent,
    context: dict | None = None,
) -> SectionState:
    """Advance a section through the state machine.

    1. Read current state from DB.
    2. Look up the transition for ``(current_state, event)``.
    3. Apply the circuit breaker for self-transitions.
    4. Write the new state and record the transition.
    5. Return the new state.

    Raises ``InvalidTransitionError`` if no transition exists.
    """
    current = get_section_state(db_path, section_number)

    transition = _lookup_transition(current, event)
    target = transition.target_state

    # --- circuit breaker for re-entry into bounded states ---
    # Fires when the section has entered the target state too many times
    # (e.g. ASSESSING -> PROPOSING cycles).  The count includes *all*
    # prior entries, not just consecutive ones.
    if target in _CIRCUIT_BREAKER_LIMITS:
        prior_entries = _count_entries_into_state(db_path, section_number, target)
        # prior_entries counts existing transitions; this would be +1
        if prior_entries + 1 > _CIRCUIT_BREAKER_LIMITS[target]:
            target = SectionState.ESCALATED

    attempt = _count_entries_into_state(db_path, section_number, target) + 1

    # Persist
    error_text = context.get("error") if context else None
    blocked_reason = context.get("blocked_reason") if context else None
    set_section_state(
        db_path, section_number, target,
        error=error_text,
        blocked_reason=blocked_reason,
        context=context,
    )
    record_transition(
        db_path, section_number, current, target, event,
        context=context, attempt_number=attempt,
    )

    return target


# ------------------------------------------------------------------
# Query helpers
# ------------------------------------------------------------------

def get_sections_in_state(
    db_path: str | Path, state: SectionState,
) -> list[str]:
    """Return section numbers currently in the given state."""
    with task_db(db_path) as conn:
        rows = conn.execute(
            "SELECT section_number FROM section_states WHERE state = ? "
            "ORDER BY section_number",
            (state.value,),
        ).fetchall()
    return [r[0] for r in rows]


def get_actionable_sections(
    db_path: str | Path,
) -> list[tuple[str, SectionState]]:
    """Return sections that can advance.

    Excludes BLOCKED, COMPLETE, FAILED, and ESCALATED.
    """
    placeholders = ",".join("?" for _ in _NON_ACTIONABLE_STATES)
    values = [s.value for s in _NON_ACTIONABLE_STATES]
    with task_db(db_path) as conn:
        rows = conn.execute(
            f"SELECT section_number, state FROM section_states "
            f"WHERE state NOT IN ({placeholders}) "
            f"ORDER BY section_number",
            values,
        ).fetchall()
    return [(r[0], SectionState(r[1])) for r in rows]
