"""Section state machine: transition table and DB-backed state management.

Each section is a state machine with its current state recorded in run.db.
Transitions are determined by events returned from single-shot handlers.
There are no loops -- re-entry transitions (e.g. ASSESSING -> PROPOSING on
alignment failure) replace while-true retry patterns.

Re-entry into PROPOSING and IMPLEMENTING is guarded observationally.
If the proposal-shaping or execution-shaping inputs have not changed
since the last entry into that state, the section escalates instead of
re-entering with no new information.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from flow.service.task_db_client import task_db
from orchestrator.path_registry import PathRegistry
from signals.repository.artifact_io import read_json, write_json


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------

class SectionState(str, Enum):
    """States a section can occupy in the execution pipeline.

    Each state corresponds to at most one agent dispatch.  Handlers are
    single-shot: dispatch one agent, read output, return an event.
    The state machine handles retry via self-transitions.
    """

    PENDING = "pending"
    EXCERPT_EXTRACTION = "excerpt_extraction"
    PROBLEM_FRAME = "problem_frame"
    INTENT_TRIAGE = "intent_triage"
    PHILOSOPHY_BOOTSTRAP = "philosophy_bootstrap"
    INTENT_PACK = "intent_pack"
    PROPOSING = "proposing"
    ASSESSING = "assessing"
    READINESS = "readiness"
    RISK_EVAL = "risk_eval"
    MICROSTRATEGY = "microstrategy"
    IMPLEMENTING = "implementing"
    IMPL_ASSESSING = "impl_assessing"
    VERIFYING = "verifying"
    POST_COMPLETION = "post_completion"
    DECOMPOSING = "decomposing"
    AWAITING_CHILDREN = "awaiting_children"
    REASSEMBLING = "reassembling"
    SCOPE_EXPANSION = "scope_expansion"
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
    SectionState.AWAITING_CHILDREN,
    SectionState.SCOPE_EXPANSION,
})


class SectionEvent(str, Enum):
    """Events that drive state transitions.

    Each event is produced by a single-shot handler after one agent
    dispatch.  The transition table maps ``(state, event)`` to the
    next state.
    """

    # --- excerpt / problem-frame ---
    excerpt_complete = "excerpt_complete"
    problem_frame_valid = "problem_frame_valid"
    problem_frame_invalid = "problem_frame_invalid"

    # --- intent ---
    triage_complete = "triage_complete"
    philosophy_ready = "philosophy_ready"
    philosophy_blocked = "philosophy_blocked"
    intent_pack_complete = "intent_pack_complete"

    # --- proposal ---
    proposal_complete = "proposal_complete"

    # --- assessment (proposal alignment) ---
    alignment_pass = "alignment_pass"
    alignment_fail = "alignment_fail"

    # --- readiness ---
    readiness_pass = "readiness_pass"
    readiness_blocked = "readiness_blocked"

    # --- risk ---
    risk_accepted = "risk_accepted"
    risk_deferred = "risk_deferred"
    risk_reopened = "risk_reopened"

    # --- microstrategy ---
    microstrategy_complete = "microstrategy_complete"

    # --- implementation ---
    implementation_complete = "implementation_complete"
    impl_feedback_detected = "impl_feedback_detected"

    # --- implementation assessment ---
    impl_alignment_pass = "impl_alignment_pass"
    impl_alignment_fail = "impl_alignment_fail"

    # --- verification ---
    verification_pass = "verification_pass"
    verification_fail = "verification_fail"

    # --- post-completion ---
    post_completion_done = "post_completion_done"

    # --- fractal descent / reassembly ---
    descent_required = "descent_required"
    children_complete = "children_complete"
    children_partial = "children_partial"
    scope_expansion = "scope_expansion"
    reassembly_complete = "reassembly_complete"
    vertical_misalignment = "vertical_misalignment"

    # --- generic ---
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
    # --- excerpt extraction (setup-excerpter) ---
    (SectionState.PENDING, SectionEvent.excerpt_complete): Transition(
        target_state=SectionState.EXCERPT_EXTRACTION,
        handler_name="handle_start",
        side_effects=["submit_excerpt_extraction"],
    ),
    (SectionState.EXCERPT_EXTRACTION, SectionEvent.excerpt_complete): Transition(
        target_state=SectionState.PROBLEM_FRAME,
        handler_name="handle_excerpt_complete",
        side_effects=["write_excerpts"],
    ),

    # --- problem frame validation ---
    (SectionState.PROBLEM_FRAME, SectionEvent.problem_frame_valid): Transition(
        target_state=SectionState.INTENT_TRIAGE,
        handler_name="handle_problem_frame_valid",
        side_effects=["validate_problem_frame"],
    ),
    (SectionState.PROBLEM_FRAME, SectionEvent.problem_frame_invalid): Transition(
        target_state=SectionState.BLOCKED,
        handler_name="handle_problem_frame_invalid",
        side_effects=["emit_frame_blocker"],
    ),

    # --- intent triage (intent-triager) ---
    (SectionState.INTENT_TRIAGE, SectionEvent.triage_complete): Transition(
        target_state=SectionState.PHILOSOPHY_BOOTSTRAP,
        handler_name="handle_triage_complete",
        side_effects=["write_triage_result"],
    ),

    # --- philosophy bootstrap (self-contained chain) ---
    (SectionState.PHILOSOPHY_BOOTSTRAP, SectionEvent.philosophy_ready): Transition(
        target_state=SectionState.INTENT_PACK,
        handler_name="handle_philosophy_ready",
        side_effects=["write_philosophy_artifacts"],
    ),
    (SectionState.PHILOSOPHY_BOOTSTRAP, SectionEvent.philosophy_blocked): Transition(
        target_state=SectionState.BLOCKED,
        handler_name="handle_philosophy_blocked",
        side_effects=["emit_philosophy_blocker"],
    ),

    # --- intent pack generation (intent-pack-generator) ---
    (SectionState.INTENT_PACK, SectionEvent.intent_pack_complete): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_intent_pack_complete",
        side_effects=["write_intent_pack", "write_governance_packet"],
    ),

    # --- proposal (integration-proposer — SINGLE SHOT) ---
    (SectionState.PROPOSING, SectionEvent.proposal_complete): Transition(
        target_state=SectionState.ASSESSING,
        handler_name="handle_proposal_complete",
        side_effects=["write_proposal", "persist_proposal_state"],
    ),

    # --- assessment (alignment-judge — SINGLE SHOT) ---
    (SectionState.ASSESSING, SectionEvent.alignment_pass): Transition(
        target_state=SectionState.READINESS,
        handler_name="handle_alignment_pass",
        side_effects=["write_alignment_result"],
    ),
    (SectionState.ASSESSING, SectionEvent.alignment_fail): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_alignment_fail",
        side_effects=["attach_problems_context"],
    ),
    (SectionState.ASSESSING, SectionEvent.vertical_misalignment): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_vertical_misalignment",
        side_effects=["attach_scope_grant_context"],
    ),

    # --- readiness (script logic, no agent dispatch) ---
    (SectionState.READINESS, SectionEvent.readiness_pass): Transition(
        target_state=SectionState.RISK_EVAL,
        handler_name="handle_readiness_pass",
        side_effects=["write_readiness_artifact"],
    ),
    (SectionState.READINESS, SectionEvent.readiness_blocked): Transition(
        target_state=SectionState.BLOCKED,
        handler_name="handle_readiness_blocked",
        side_effects=["emit_readiness_blocker"],
    ),

    # --- risk evaluation (risk-assessor + execution-optimizer) ---
    (SectionState.RISK_EVAL, SectionEvent.risk_accepted): Transition(
        target_state=SectionState.MICROSTRATEGY,
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

    # --- microstrategy (microstrategy-decider) ---
    (SectionState.MICROSTRATEGY, SectionEvent.microstrategy_complete): Transition(
        target_state=SectionState.IMPLEMENTING,
        handler_name="handle_microstrategy_complete",
        side_effects=["write_microstrategy_artifacts"],
    ),

    # --- implementation (implementation-strategist — SINGLE SHOT) ---
    (SectionState.IMPLEMENTING, SectionEvent.implementation_complete): Transition(
        target_state=SectionState.IMPL_ASSESSING,
        handler_name="handle_implementation_complete",
        side_effects=["write_implementation_output"],
    ),
    (SectionState.IMPLEMENTING, SectionEvent.impl_feedback_detected): Transition(
        target_state=SectionState.BLOCKED,
        handler_name="handle_impl_feedback_detected",
        side_effects=["write_impl_feedback_blocker", "cancel_verify_descendants"],
    ),

    # --- implementation assessment (alignment-judge — SINGLE SHOT) ---
    (SectionState.IMPL_ASSESSING, SectionEvent.impl_alignment_pass): Transition(
        target_state=SectionState.VERIFYING,
        handler_name="handle_impl_alignment_pass",
        side_effects=["write_traceability", "submit_verification_chain"],
    ),
    (SectionState.IMPL_ASSESSING, SectionEvent.impl_alignment_fail): Transition(
        target_state=SectionState.IMPLEMENTING,
        handler_name="handle_impl_alignment_fail",
        side_effects=["attach_impl_problems_context"],
    ),

    # --- verification (async task chain) ---
    (SectionState.VERIFYING, SectionEvent.verification_pass): Transition(
        target_state=SectionState.POST_COMPLETION,
        handler_name="handle_verification_pass",
        side_effects=["write_verification_result"],
    ),
    (SectionState.VERIFYING, SectionEvent.verification_fail): Transition(
        target_state=SectionState.IMPLEMENTING,
        handler_name="handle_verification_fail",
        side_effects=["attach_verification_problems"],
    ),

    # --- post-completion (impact-analyzer) ---
    (SectionState.POST_COMPLETION, SectionEvent.post_completion_done): Transition(
        target_state=SectionState.COMPLETE,
        handler_name="handle_post_completion_done",
        side_effects=["write_completion_signal"],
    ),

    # --- blocked ---
    (SectionState.BLOCKED, SectionEvent.info_available): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_info_available",
        side_effects=["clear_blocker", "reenter_with_context"],
    ),

    # --- fractal descent / reassembly ---
    (SectionState.READINESS, SectionEvent.descent_required): Transition(
        target_state=SectionState.DECOMPOSING,
        handler_name="handle_descent_required",
        side_effects=["decompose_into_children"],
    ),
    (SectionState.DECOMPOSING, SectionEvent.excerpt_complete): Transition(
        target_state=SectionState.AWAITING_CHILDREN,
        handler_name="handle_children_spawned",
        side_effects=["record_child_sections"],
    ),
    (SectionState.AWAITING_CHILDREN, SectionEvent.children_complete): Transition(
        target_state=SectionState.REASSEMBLING,
        handler_name="handle_children_complete",
        side_effects=["collect_child_results"],
    ),
    (SectionState.AWAITING_CHILDREN, SectionEvent.children_partial): Transition(
        target_state=SectionState.REASSEMBLING,
        handler_name="handle_children_partial",
        side_effects=["collect_child_results", "note_partial_children"],
    ),
    (SectionState.REASSEMBLING, SectionEvent.reassembly_complete): Transition(
        target_state=SectionState.POST_COMPLETION,
        handler_name="handle_reassembly_complete",
        side_effects=["write_reassembly_result"],
    ),
    (SectionState.SCOPE_EXPANSION, SectionEvent.info_available): Transition(
        target_state=SectionState.PROPOSING,
        handler_name="handle_scope_absorbed",
        side_effects=["absorb_parent_rescope"],
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
        side_effects=["emit_need_decision"],
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


_REENTRY_GUARDED_STATES = frozenset({
    SectionState.PROPOSING,
    SectionState.IMPLEMENTING,
})


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when an event has no valid transition from the current state."""


class CircuitBreakerTripped(Exception):
    """Legacy compatibility alias for former breaker-based callers.

    Re-entry is now governed by observational progress stamps instead of
    numeric thresholds.
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
    parent_section: str | None = None,
    depth: int | None = None,
    scope_grant: str | None = None,
    spawned_by_state: str | None = None,
) -> None:
    """Write (upsert) the current state for a section.

    The optional *parent_section*, *depth*, *scope_grant*, and
    *spawned_by_state* parameters support the fractal layer model.
    When provided on an INSERT they populate the corresponding columns;
    when omitted they default to NULL / 0 via the schema defaults.
    On conflict (UPDATE) these columns are only overwritten when an
    explicit non-None value was passed, preserving previously stored
    values for existing rows.
    """
    context_json = json.dumps(context) if context else None

    # Build the SET clause dynamically so fractal columns are only
    # overwritten when the caller explicitly provides them.
    set_parts = [
        "state = excluded.state",
        "updated_at = excluded.updated_at",
        "error = excluded.error",
        "blocked_reason = excluded.blocked_reason",
        "context_json = excluded.context_json",
    ]
    if parent_section is not None:
        set_parts.append("parent_section = excluded.parent_section")
    if depth is not None:
        set_parts.append("depth = excluded.depth")
    if scope_grant is not None:
        set_parts.append("scope_grant = excluded.scope_grant")
    if spawned_by_state is not None:
        set_parts.append("spawned_by_state = excluded.spawned_by_state")

    set_clause = ", ".join(set_parts)

    with task_db(db_path) as conn:
        conn.execute(
            f"""INSERT INTO section_states
                   (section_number, state, updated_at, error, blocked_reason,
                    context_json, parent_section, depth, scope_grant,
                    spawned_by_state)
               VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(section_number) DO UPDATE SET
                   {set_clause}""",
            (
                section_number, state.value, error, blocked_reason,
                context_json, parent_section, depth if depth is not None else 0,
                scope_grant, spawned_by_state,
            ),
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


def _get_scope_grant(
    db_path: str | Path,
    section_number: str,
) -> str | None:
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT scope_grant FROM section_states WHERE section_number = ?",
            (section_number,),
        ).fetchone()
    value = row[0] if row else None
    return value if isinstance(value, str) and value.strip() else None


def _next_attempt_number(
    db_path: str | Path,
    section_number: str,
    state: SectionState,
) -> int:
    """Return the next history attempt number for entries into ``state``."""
    with task_db(db_path) as conn:
        row = conn.execute(
            """SELECT COALESCE(MAX(attempt_number), 0)
               FROM section_transitions
               WHERE section_number = ? AND to_state = ?""",
            (section_number, state.value),
        ).fetchone()
    return (row[0] if row else 0) + 1


def _has_parent_section(db_path: str | Path, section_number: str) -> bool:
    """Return True when the section has a non-null parent_section."""
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT parent_section FROM section_states WHERE section_number = ?",
            (section_number,),
        ).fetchone()
    return bool(row and row[0])


def _resolve_planspace(
    db_path: str | Path,
    planspace: str | Path | None = None,
) -> Path:
    if planspace is not None:
        return Path(planspace)
    return Path(db_path).parent


def _reentry_stamp_paths(
    paths: PathRegistry,
    section_number: str,
    target_state: SectionState,
) -> list[Path]:
    intent_dir = paths.intent_section_dir(section_number)
    if target_state is SectionState.PROPOSING:
        return [
            paths.problem_frame(section_number),
            intent_dir / "problem.md",
            intent_dir / "problem-alignment.md",
            intent_dir / "surface-registry.json",
            paths.research_derived_surfaces(section_number),
            paths.impl_feedback_surfaces(section_number),
        ]
    if target_state is SectionState.IMPLEMENTING:
        return [
            paths.execution_ready(section_number),
            paths.risk_accepted_steps(section_number),
            paths.microstrategy(section_number),
            paths.post_impl_assessment(section_number),
            paths.verification_status(section_number),
            paths.testing_rca_findings(section_number),
        ]
    raise ValueError(f"Unsupported re-entry stamp target state: {target_state.value}")


def _compute_reentry_stamp(
    db_path: str | Path,
    section_number: str,
    target_state: SectionState,
    planspace: str | Path,
) -> str:
    """Hash the authoritative re-entry inputs for PROPOSING/IMPLEMENTING."""
    paths = PathRegistry(Path(planspace))
    hasher = hashlib.sha256()

    for path in _reentry_stamp_paths(paths, section_number, target_state):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        hasher.update(str(path.relative_to(paths.planspace)).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(content)
        hasher.update(b"\0")

    if target_state is SectionState.PROPOSING:
        scope_grant = _get_scope_grant(db_path, section_number)
        if scope_grant is not None:
            hasher.update(b"scope_grant\0")
            hasher.update(scope_grant.encode("utf-8"))
            hasher.update(b"\0")

    return hasher.hexdigest()


def _get_last_reentry_stamp(
    db_path: str | Path,
    section_number: str,
    target_state: SectionState,
    planspace: str | Path | None = None,
) -> str | None:
    """Read the last persisted stamp hash for a guarded state."""
    paths = PathRegistry(_resolve_planspace(db_path, planspace))
    data = read_json(paths.reentry_stamp(section_number, target_state.value))
    if not isinstance(data, dict):
        return None
    stamp_hash = data.get("stamp_hash")
    return stamp_hash if isinstance(stamp_hash, str) and stamp_hash else None


def _persist_reentry_stamp(
    db_path: str | Path,
    section_number: str,
    target_state: SectionState,
    stamp_hash: str,
    planspace: str | Path | None = None,
) -> None:
    """Persist the current stamp hash for a guarded state."""
    paths = PathRegistry(_resolve_planspace(db_path, planspace))
    write_json(
        paths.reentry_stamp(section_number, target_state.value),
        {
            "section_number": section_number,
            "state_name": target_state.value,
            "stamp_hash": stamp_hash,
        },
    )


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
    3. Apply re-entry guards for PROPOSING/IMPLEMENTING.
    4. Write the new state and record the transition.
    5. Persist the re-entry stamp when applicable.
    6. Return the new state.

    Raises ``InvalidTransitionError`` if no transition exists.
    """
    current = get_section_state(db_path, section_number)

    transition = _lookup_transition(current, event)
    target = transition.target_state

    # Root sections cannot enter SCOPE_EXPANSION. If a future transition
    # would send a root there, fail closed to ESCALATED instead.
    if (
        target == SectionState.SCOPE_EXPANSION
        and get_section_depth(db_path, section_number) == 0
    ):
        target = SectionState.ESCALATED

    stamp_hash: str | None = None
    if target in _REENTRY_GUARDED_STATES:
        planspace = _resolve_planspace(db_path)
        stamp_hash = _compute_reentry_stamp(
            db_path, section_number, target, planspace,
        )
        last_stamp = _get_last_reentry_stamp(
            db_path, section_number, target, planspace,
        )
        if last_stamp == stamp_hash:
            if (
                target is SectionState.PROPOSING
                and _has_parent_section(db_path, section_number)
            ):
                target = SectionState.SCOPE_EXPANSION
            else:
                target = SectionState.ESCALATED
            stamp_hash = None

    attempt = _next_attempt_number(db_path, section_number, target)

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
    if stamp_hash is not None:
        _persist_reentry_stamp(db_path, section_number, target, stamp_hash)

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


def get_child_sections(
    db_path: str | Path, parent_number: str,
) -> list[tuple[str, SectionState]]:
    """Return all sections whose ``parent_section`` equals *parent_number*.

    Each result is a ``(section_number, state)`` tuple, ordered by
    section number.  Returns an empty list when the parent has no
    children.
    """
    with task_db(db_path) as conn:
        rows = conn.execute(
            "SELECT section_number, state FROM section_states "
            "WHERE parent_section = ? ORDER BY section_number",
            (parent_number,),
        ).fetchall()
    return [(r[0], SectionState(r[1])) for r in rows]


def get_section_depth(db_path: str | Path, section_number: str) -> int:
    """Return the recursion depth of a section (0 for root sections).

    Returns ``0`` if the section does not exist yet (consistent with
    the schema default).
    """
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT depth FROM section_states WHERE section_number = ?",
            (section_number,),
        ).fetchone()
    return row[0] if row is not None else 0
