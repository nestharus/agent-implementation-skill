"""State-machine-driven section orchestrator.

Replaces the poll-and-wait loop in ``PipelineOrchestrator._run_loop``
with a state machine that drives each section independently through
its lifecycle.

Each section has a current state recorded in run.db.  The orchestrator
polls for actionable sections (not blocked, not terminal) and submits
the appropriate task for each one's current state.  Blocked sections
are checked every pass for unblock conditions.

This module does NOT execute tasks itself -- it submits them to the
task queue for the ``TaskDispatcher`` to process.  Task completions
advance the state machine via the reconciler.

Uses the ``SectionState`` enum and DB helpers from
``orchestrator.engine.section_state_machine``, which owns the
transition table, circuit breaker, and schema.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
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
)
from flow.types.context import FlowEnvelope, new_flow_id
from flow.types.schema import TaskSpec

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService, PipelineControlService
    from flow.engine.flow_submitter import FlowSubmitter

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 2.0

# Map from SectionState to the task type submitted for that state.
_STATE_TASK_MAP: dict[SectionState, str] = {
    SectionState.PENDING: "section.propose",
    SectionState.READY: "section.implement",
}

# Terminal and in-flight states -- used by all_sections_terminal.
_TERMINAL_STATES = frozenset({
    SectionState.COMPLETE,
    SectionState.FAILED,
})

# Non-actionable includes BLOCKED and ESCALATED (from the state machine module)
# but we define our own terminal check that includes ESCALATED.
_FULLY_TERMINAL = _TERMINAL_STATES | frozenset({SectionState.ESCALATED})


# ---------------------------------------------------------------------------
# Query helpers that operate on the existing schema
# ---------------------------------------------------------------------------


def all_sections_terminal(db_path: str | Path) -> bool:
    """Return True when every section is in a terminal or escalated state."""
    from flow.service.task_db_client import task_db

    terminal_values = tuple(s.value for s in _FULLY_TERMINAL)
    placeholders = ",".join("?" for _ in terminal_values)
    with task_db(db_path) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM section_states "
            f"WHERE state NOT IN ({placeholders})",
            terminal_values,
        ).fetchone()
    return (row[0] if row else 0) == 0


def get_all_section_states(db_path: str | Path) -> list[dict]:
    """Load current state for all sections as dicts."""
    from flow.service.task_db_client import task_db
    import sqlite3

    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM section_states ORDER BY section_number"
        )
        return [dict(r) for r in cur.fetchall()]


def get_blocked_sections(db_path: str | Path) -> list[dict]:
    """Return all sections currently BLOCKED as dicts."""
    from flow.service.task_db_client import task_db
    import sqlite3

    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM section_states WHERE state=? ORDER BY section_number",
            (SectionState.BLOCKED.value,),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class StateMachineOrchestrator:
    """Drives per-section state machines by submitting tasks to the queue.

    The orchestrator does not execute tasks.  It:
    1. Reads each section's current state from run.db.
    2. Submits the appropriate task for sections in actionable states.
    3. Checks blocked sections for unblock conditions.
    4. Sleeps and repeats until all sections are terminal.

    Task completions advance the state machine via the reconciler's
    completion handlers (see ``advance_on_task_completion``).
    """

    def __init__(
        self,
        logger_service: LogService,
        artifact_io: ArtifactIOService,
        flow_submitter: FlowSubmitter,
        pipeline_control: PipelineControlService,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._logger = logger_service
        self._artifact_io = artifact_io
        self._flow_submitter = flow_submitter
        self._pipeline_control = pipeline_control
        self._poll_interval = poll_interval
        self._sleep = time.sleep  # seam for testing

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize_sections(
        self,
        db_path: str | Path,
        section_numbers: list[str],
    ) -> None:
        """Create section_states rows for all sections.

        Idempotent: existing rows are not overwritten, so resume works.
        Sections that already have a state (from a prior run) keep their
        current state.  The schema (section_states, section_transitions)
        is created by ``init_db`` in ``task_db_client`` -- this method
        only populates rows.
        """
        for num in section_numbers:
            current = get_section_state(db_path, num)
            if current == SectionState.PENDING:
                # Check if the row actually exists or was a default
                exists = self._section_row_exists(db_path, num)
                if not exists:
                    set_section_state(db_path, num, SectionState.PENDING)
                    self._logger.log(
                        f"[STATE] Section {num}: initialized -> {SectionState.PENDING.value}"
                    )
                else:
                    self._logger.log(
                        f"[STATE] Section {num}: resuming in state "
                        f"{SectionState.PENDING.value}"
                    )
            else:
                self._logger.log(
                    f"[STATE] Section {num}: resuming in state {current.value}"
                )

    @staticmethod
    def _section_row_exists(db_path: str | Path, section_number: str) -> bool:
        """Check whether a row exists in section_states for this section."""
        from flow.service.task_db_client import task_db

        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM section_states WHERE section_number=?",
                (section_number,),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(
        self,
        db_path: str | Path,
        planspace: Path,
        section_payload_paths: dict[str, str],
    ) -> None:
        """Main orchestration loop.

        Polls until all sections reach a terminal state.

        *section_payload_paths* maps section number -> payload path
        (the section spec file) for task submission.
        """
        self._logger.log("[STATE] Starting state machine orchestration loop")

        while not all_sections_terminal(db_path):
            # Abort check
            if self._pipeline_control.handle_pending_messages(planspace):
                self._logger.log("[STATE] Aborted by parent")
                return

            # 1. Submit tasks for actionable sections
            actionable = get_actionable_sections(db_path)
            for sec_num, state in actionable:
                payload = section_payload_paths.get(sec_num, "")
                self._submit_for_state(db_path, planspace, sec_num, state, payload)

            # 2. Check blocked sections for unblock conditions
            blocked = get_blocked_sections(db_path)
            for row in blocked:
                sec_num = row["section_number"]
                self._check_unblock(db_path, planspace, sec_num, row)

            self._sleep(self._poll_interval)

        self._logger.log("[STATE] All sections terminal -- orchestration complete")

    # ------------------------------------------------------------------
    # Task submission per state
    # ------------------------------------------------------------------

    def _submit_for_state(
        self,
        db_path: str | Path,
        planspace: Path,
        section_number: str,
        state: SectionState,
        payload_path: str,
    ) -> None:
        """Submit the appropriate task for a section's current state."""
        task_type = _STATE_TASK_MAP.get(state)
        if task_type is None:
            return

        self._submit_section_task(
            db_path, planspace, section_number, task_type, payload_path,
        )

        # Advance state: PENDING -> PROPOSING or READY -> IMPLEMENTING
        if state == SectionState.PENDING:
            event = SectionEvent.bootstrap_complete
        elif state == SectionState.READY:
            event = SectionEvent.risk_accepted
        else:
            return

        try:
            new_state = advance_section(db_path, section_number, event)
            self._logger.log(
                f"[STATE] Section {section_number}: {state.value} -> "
                f"{new_state.value} (submitted {task_type})"
            )
        except InvalidTransitionError:
            # If the transition table doesn't have this entry, just
            # set the state directly (graceful fallback).
            target = (
                SectionState.PROPOSING if state == SectionState.PENDING
                else SectionState.IMPLEMENTING
            )
            set_section_state(db_path, section_number, target)
            self._logger.log(
                f"[STATE] Section {section_number}: {state.value} -> "
                f"{target.value} (submitted {task_type}, direct set)"
            )

    def _submit_section_task(
        self,
        db_path: str | Path,
        planspace: Path,
        section_number: str,
        task_type: str,
        payload_path: str,
    ) -> None:
        """Submit a single section task into the task queue."""
        env = FlowEnvelope(
            db_path=Path(str(db_path)),
            submitted_by="state_machine_orchestrator",
            flow_id=new_flow_id(),
            planspace=planspace,
        )
        concern_scope = f"section-{section_number}"
        self._flow_submitter.submit_chain(
            env,
            [
                TaskSpec(
                    task_type=task_type,
                    concern_scope=concern_scope,
                    payload_path=payload_path,
                    priority="normal",
                ),
            ],
        )

    # ------------------------------------------------------------------
    # Blocked section checks
    # ------------------------------------------------------------------

    def _check_unblock(
        self,
        db_path: str | Path,
        planspace: Path,
        section_number: str,
        row: dict,
    ) -> None:
        """Check if a blocked section's blocking condition has resolved.

        Polls for the existence of artifacts that would satisfy the
        blocker.  This is the "poll and check" pattern from the design
        doc -- no dependency graph, just artifact existence checks.
        """
        paths = PathRegistry(planspace)
        context = _parse_context(row.get("context_json"))
        blocker_type = context.get("blocker_type", "")
        blocked_reason = row.get("blocked_reason", "") or ""

        unblocked = False

        if blocker_type == "blocking_research_questions" or "research" in blocked_reason:
            if paths.research_dossier(section_number).exists():
                unblocked = True

        elif blocker_type == "coordination_needed" or "coordination" in blocked_reason:
            coord_decision = (
                paths.coordination_dir()
                / f"section-{section_number}-decision.json"
            )
            if coord_decision.exists():
                unblocked = True

        elif blocker_type == "verification_failure" or "verification" in blocked_reason:
            verification_status = paths.verification_status(section_number)
            data = self._artifact_io.read_json(verification_status)
            if isinstance(data, dict) and data.get("status") == "pass":
                unblocked = True

        elif blocker_type == "readiness_failed" or "readiness" in blocked_reason:
            data = self._artifact_io.read_json(
                paths.execution_ready(section_number),
            )
            if isinstance(data, dict) and data.get("ready", False):
                unblocked = True

        if unblocked:
            try:
                new_state = advance_section(
                    db_path, section_number, SectionEvent.info_available,
                    context={"unblocked_from": blocker_type or blocked_reason},
                )
                self._logger.log(
                    f"[STATE] Section {section_number}: blocked -> "
                    f"{new_state.value} (blocker resolved: "
                    f"{blocker_type or blocked_reason})"
                )
            except InvalidTransitionError:
                set_section_state(db_path, section_number, SectionState.PROPOSING)
                self._logger.log(
                    f"[STATE] Section {section_number}: blocked -> proposing "
                    f"(blocker resolved: {blocker_type or blocked_reason}, "
                    f"direct set)"
                )


# ---------------------------------------------------------------------------
# Task completion -> state advance (called by reconciler)
# ---------------------------------------------------------------------------

# Map (task_type, success) -> SectionEvent for section-scoped tasks.
_TASK_EVENT_MAP: dict[tuple[str, bool], SectionEvent] = {
    ("section.propose", True): SectionEvent.proposal_complete,
    ("section.propose", False): SectionEvent.error,
    ("section.implement", True): SectionEvent.implementation_complete,
    ("section.implement", False): SectionEvent.error,
    ("section.verify", True): SectionEvent.verification_pass,
    ("section.verify", False): SectionEvent.verification_fail,
}


def advance_on_task_completion(
    db_path: str | Path,
    section_number: str,
    task_type: str,
    success: bool,
    context: dict | None = None,
) -> str | None:
    """Advance a section's state based on task completion.

    Called by the reconciler when a section-scoped task completes.
    Returns the new state value (string), or None if no transition occurred.

    Delegates to ``advance_section`` which handles the circuit breaker.

    The readiness check is special: the outcome depends on the readiness
    artifact content.  ``alignment_pass`` maps ASSESSING -> RISK_EVAL,
    ``alignment_fail`` maps ASSESSING -> PROPOSING.
    """
    ctx = context or {}

    # Determine the event
    if task_type == "section.readiness_check":
        if not success:
            event = SectionEvent.error
        elif ctx.get("ready", False):
            # Ready = alignment passed
            event = SectionEvent.alignment_pass
        else:
            # Not ready = alignment failed (problems found)
            event = SectionEvent.alignment_fail
    else:
        event = _TASK_EVENT_MAP.get((task_type, success))
        if event is None:
            return None

    try:
        new_state = advance_section(
            db_path, section_number, event, context=ctx,
        )
        return new_state.value
    except InvalidTransitionError:
        logger.debug(
            "No transition for section %s: task_type=%s, event=%s",
            section_number, task_type, event.value,
        )
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_context(raw: str | None) -> dict:
    """Safely parse a context_json string."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
