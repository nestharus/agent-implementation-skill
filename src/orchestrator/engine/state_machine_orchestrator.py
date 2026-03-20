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
from flow.service.starvation_detector import detect_starvation
from flow.types.context import FlowEnvelope, new_flow_id
from flow.types.schema import TaskSpec
from orchestrator.repository.decisions import Decision, Decisions

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService, PipelineControlService
    from flow.engine.flow_submitter import FlowSubmitter

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 2.0

# Map from SectionState to the task type submitted for that state.
# States that do not dispatch an agent (READINESS is script-only,
# COMPLETE/FAILED/ESCALATED are terminal) are omitted.
_STATE_TASK_MAP: dict[SectionState, str] = {
    SectionState.PENDING: "section.excerpt",
    SectionState.EXCERPT_EXTRACTION: "section.excerpt",
    SectionState.PROBLEM_FRAME: "section.problem_frame",
    SectionState.INTENT_TRIAGE: "section.intent_triage",
    SectionState.PHILOSOPHY_BOOTSTRAP: "section.philosophy",
    SectionState.INTENT_PACK: "section.intent_pack",
    SectionState.PROPOSING: "section.propose",
    SectionState.ASSESSING: "section.assess",
    SectionState.RISK_EVAL: "section.risk_eval",
    SectionState.MICROSTRATEGY: "section.microstrategy",
    SectionState.IMPLEMENTING: "section.implement",
    SectionState.IMPL_ASSESSING: "section.impl_assess",
    SectionState.VERIFYING: "section.verify",
    SectionState.POST_COMPLETION: "section.post_complete",
    SectionState.DECOMPOSING: "section.decompose_children",
    SectionState.REASSEMBLING: "section.reassemble",
}

# Terminal and in-flight states -- used by all_sections_terminal.
_TERMINAL_STATES = frozenset({
    SectionState.COMPLETE,
    SectionState.FAILED,
})

# Non-actionable includes BLOCKED and ESCALATED (from the state machine module)
# but we define our own terminal check that includes ESCALATED.
_FULLY_TERMINAL = _TERMINAL_STATES | frozenset({SectionState.ESCALATED})
_SCOPE_EXPANSION_CONTEXT_KEY = "absorbed_scope_expansions"


# ---------------------------------------------------------------------------
# Query helpers that operate on the existing schema
# ---------------------------------------------------------------------------


def all_sections_terminal(db_path: str | Path) -> bool:
    """Return True when every section is in a terminal or escalated state.

    Returns False when section_states has 0 rows (bootstrap has not yet
    populated sections), so the orchestrator keeps polling.
    """
    from flow.service.task_db_client import task_db

    terminal_values = tuple(s.value for s in _FULLY_TERMINAL)
    placeholders = ",".join("?" for _ in terminal_values)
    with task_db(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM section_states",
        ).fetchone()
        if not total or total[0] == 0:
            return False
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
# Codemap delta merge helper (Piece 5E)
# ---------------------------------------------------------------------------


def _merge_child_deltas(
    planspace: Path,
    parent_num: str,
    child_nums: list[str],
) -> None:
    """Merge child codemap deltas into the parent's fragment.

    For each child, reads the delta artifact at
    ``PathRegistry.codemap_delta(child_num)``.  New lines from the delta
    are appended to the parent's fragment at
    ``PathRegistry.section_codemap(parent_num)``.  Consumed deltas are
    deleted so they are not re-merged on the next poll cycle.

    This is additive-only: existing parent fragment content is never
    removed.  All operations are wrapped in try/except so failures
    never block the orchestration loop.
    """
    paths = PathRegistry(planspace)
    merged_any = False

    for child_num in child_nums:
        try:
            delta_path = paths.codemap_delta(child_num)
            if not delta_path.is_file():
                continue

            delta_text = delta_path.read_text(encoding="utf-8")
            delta_data = json.loads(delta_text)
            child_lines = delta_data.get("lines", [])
            if not child_lines:
                # Empty delta -- consume and skip.
                delta_path.unlink(missing_ok=True)
                continue

            # Read existing parent fragment.
            parent_fragment_path = paths.section_codemap(parent_num)
            parent_fragment_path.parent.mkdir(parents=True, exist_ok=True)

            existing_lines: list[str] = []
            if parent_fragment_path.is_file():
                try:
                    existing_lines = parent_fragment_path.read_text(
                        encoding="utf-8",
                    ).splitlines()
                except OSError:
                    existing_lines = []

            existing_set = set(existing_lines)
            new_lines = [
                line for line in child_lines
                if line not in existing_set
            ]

            if new_lines:
                merged = existing_lines + new_lines
                parent_fragment_path.write_text(
                    "\n".join(merged) + "\n",
                    encoding="utf-8",
                )
                merged_any = True
                logger.info(
                    "codemap delta merge: %d new lines from child %s "
                    "into parent %s",
                    len(new_lines),
                    child_num,
                    parent_num,
                )

            # Consume the delta so it is not re-merged.
            delta_path.unlink(missing_ok=True)

        except Exception:
            logger.debug(
                "Failed to merge codemap delta from child %s into "
                "parent %s — continuing",
                child_num,
                parent_num,
                exc_info=True,
            )

    if merged_any:
        logger.info(
            "codemap delta propagation: updated parent %s fragment "
            "from child deltas",
            parent_num,
        )


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
    ) -> None:
        """Main orchestration loop.

        Polls until all sections reach a terminal state.  Payload paths
        are derived from ``PathRegistry.section_spec()`` using each
        section's number -- no pre-built dict required.

        On a fresh run, ``section_states`` is empty until the bootstrap
        chain (discover_substrate) populates it.  The loop waits.
        On resume, rows already exist so the loop proceeds immediately.
        """
        self._logger.log("[STATE] Starting state machine orchestration loop")
        paths = PathRegistry(planspace)

        while not all_sections_terminal(db_path):
            # Abort check
            if self._pipeline_control.handle_pending_messages(planspace):
                self._logger.log("[STATE] Aborted by parent")
                return

            # If no section rows yet, bootstrap is still running.
            # Check for bootstrap failure so we don't wait forever.
            all_states = get_all_section_states(db_path)
            if not all_states:
                if self._bootstrap_has_failed(db_path):
                    self._logger.log(
                        "[STATE] ERROR: Bootstrap failed and no sections "
                        "were populated -- aborting orchestration"
                    )
                    return
                self._logger.log(
                    "[STATE] Waiting for bootstrap to populate section states..."
                )
                self._sleep(self._poll_interval)
                continue

            # 1. Submit tasks for actionable sections
            actionable = get_actionable_sections(db_path)
            for sec_num, state in actionable:
                self._submit_for_state(db_path, planspace, sec_num, state, paths)

            # 2. Check blocked sections for unblock conditions
            blocked = get_blocked_sections(db_path)
            for row in blocked:
                sec_num = row["section_number"]
                self._check_unblock(db_path, planspace, sec_num, row)

            # 3. Check AWAITING_CHILDREN sections for child completion
            self._check_awaiting_children(db_path, planspace)

            # 4. Orphan cleanup: fail children whose parent terminated
            self._check_orphaned_children(db_path)

            # 5. Starvation detection: escalate sections stuck too long
            blocked_nums = [row["section_number"] for row in blocked]
            if blocked_nums:
                starved = detect_starvation(
                    self._artifact_io, planspace, blocked_nums,
                )
                for sec_num in starved:
                    try:
                        new_state = advance_section(
                            db_path, sec_num, SectionEvent.timeout,
                            context={
                                "blocked_reason": "starvation_detected",
                                "detail": (
                                    f"Section {sec_num} exceeded starvation "
                                    f"threshold while blocked"
                                ),
                            },
                        )
                        self._logger.log(
                            f"[STATE] Section {sec_num}: blocked -> "
                            f"{new_state.value} (starvation detected)"
                        )
                    except InvalidTransitionError:
                        set_section_state(
                            db_path, sec_num, SectionState.ESCALATED,
                            blocked_reason="starvation_detected",
                        )
                        self._logger.log(
                            f"[STATE] Section {sec_num}: blocked -> "
                            f"escalated (starvation detected, direct set)"
                        )

            self._sleep(self._poll_interval)

        self._logger.log("[STATE] All sections terminal -- orchestration complete")

    # ------------------------------------------------------------------
    # Task submission per state
    # ------------------------------------------------------------------

    @staticmethod
    def _bootstrap_has_failed(db_path: str | Path) -> bool:
        """Check if any bootstrap stage has status='failed' in the log."""
        from flow.service.task_db_client import task_db

        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM bootstrap_execution_log "
                "WHERE status='failed' LIMIT 1",
            ).fetchone()
        return row is not None

    def _submit_for_state(
        self,
        db_path: str | Path,
        planspace: Path,
        section_number: str,
        state: SectionState,
        paths: PathRegistry,
    ) -> None:
        """Submit the appropriate task for a section's current state.

        Each state maps to exactly one task type.  The handler for that
        task is single-shot: it dispatches one agent call, reads the
        output, and produces an event that the reconciler feeds back
        into ``advance_section``.

        States without a task mapping (READINESS, terminal states) are
        skipped -- READINESS runs script logic inline via the reconciler.
        """
        task_type = _STATE_TASK_MAP.get(state)
        if task_type is None:
            # READINESS is script-only; terminal/blocked states skip.
            return

        from flow.service.task_db_client import has_active_task
        concern_scope = f"section-{section_number}"
        if has_active_task(db_path, concern_scope, task_type):
            return  # already submitted, skip

        payload_path = str(paths.section_spec(section_number))
        self._submit_section_task(
            db_path, planspace, section_number, task_type, payload_path,
        )
        # Record submission time for starvation detection
        from flow.service.starvation_detector import record_chain_submission
        record_chain_submission(self._artifact_io, planspace, section_number)
        self._logger.log(
            f"[STATE] Section {section_number}: submitted {task_type} "
            f"(state={state.value})"
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

    # ------------------------------------------------------------------
    # AWAITING_CHILDREN polling
    # ------------------------------------------------------------------

    def _check_awaiting_children(
        self,
        db_path: str | Path,
        planspace: Path | None = None,
    ) -> None:
        """Poll parent sections in AWAITING_CHILDREN for child completion.

        A parent in AWAITING_CHILDREN advances when its children reach
        terminal states or SCOPE_EXPANSION.  The check:
        - All children terminal (COMPLETE/FAILED/ESCALATED) -> children_complete
        - Some children terminal + any child in SCOPE_EXPANSION -> scope_expansion
          (parent absorbs the upward signal)
        - Mix of terminal and in-progress children with at least one
          SCOPE_EXPANSION -> scope_expansion takes priority
        - Some children terminal, rest still running, no SCOPE_EXPANSION
          -> wait (no event yet)
        - All children terminal but some FAILED/ESCALATED -> children_partial

        Additionally, when *planspace* is provided, child codemap deltas
        are merged into the parent's fragment (Piece 5E).

        This query returns nothing when no parent-child relationships
        exist, making the check purely additive.
        """
        from flow.service.task_db_client import task_db

        awaiting = get_sections_in_state(db_path, SectionState.AWAITING_CHILDREN)
        if not awaiting:
            return

        for parent_num in awaiting:
            with task_db(db_path) as conn:
                children = conn.execute(
                    "SELECT section_number, state FROM section_states "
                    "WHERE parent_section = ? ORDER BY section_number",
                    (parent_num,),
                ).fetchall()

            if not children:
                # No children registered yet -- decompose task may still
                # be running.  Wait.
                continue

            child_nums = [row[0] for row in children]
            child_states = [SectionState(row[1]) for row in children]

            # Piece 5E: merge child codemap deltas into parent fragment.
            if planspace is not None:
                _merge_child_deltas(planspace, parent_num, child_nums)

            # SCOPE_EXPANSION is non-actionable for the child, so the parent
            # must consume it immediately rather than waiting for all children
            # to settle.
            scope_children = [
                child_num
                for child_num, child_state in zip(child_nums, child_states, strict=False)
                if child_state == SectionState.SCOPE_EXPANSION
            ]
            if scope_children:
                if planspace is None:
                    continue
                self._consume_scope_expansion_children(
                    db_path, planspace, parent_num, scope_children,
                )
                continue

            all_terminal = all(s in _FULLY_TERMINAL for s in child_states)

            if not all_terminal:
                # Some children still running -- wait.
                continue

            # All children are in terminal states. Check if any failed.
            all_complete = all(
                s == SectionState.COMPLETE for s in child_states
            )
            event = (
                SectionEvent.children_complete if all_complete
                else SectionEvent.children_partial
            )
            try:
                new_state = advance_section(
                    db_path, parent_num, event,
                )
                self._logger.log(
                    f"[STATE] Section {parent_num}: awaiting_children -> "
                    f"{new_state.value} ({event.value})"
                )
            except InvalidTransitionError:
                pass

    def _consume_scope_expansion_children(
        self,
        db_path: str | Path,
        planspace: Path,
        parent_num: str,
        child_nums: list[str],
    ) -> None:
        """Absorb child scope-expansion signals and re-open children.

        The signal artifact is authoritative. Missing or malformed signals
        fail closed: the child remains in SCOPE_EXPANSION and the parent stays
        in AWAITING_CHILDREN.
        """
        paths = PathRegistry(planspace)
        parent_context = self._load_section_context(db_path, parent_num)
        absorbed_count = 0

        for child_num in child_nums:
            signal_path = paths.scope_expansion_signal(child_num)
            signal = self._load_scope_expansion_signal(
                signal_path, child_num=child_num, parent_num=parent_num,
            )
            if signal is None:
                self._logger.log(
                    f"[STATE] Section {parent_num}: waiting on valid scope "
                    f"expansion signal from child {child_num}"
                )
                continue

            revised_scope_grant = _build_revised_scope_grant(signal)
            self._update_child_scope_grant(
                db_path, child_num, revised_scope_grant,
            )

            try:
                new_state = advance_section(
                    db_path,
                    child_num,
                    SectionEvent.info_available,
                    context={
                        "scope_expansion_absorbed": True,
                        "scope_expansion_signal": signal,
                        "revised_scope_grant": revised_scope_grant,
                        "unblocked_from": SectionState.SCOPE_EXPANSION.value,
                    },
                )
                self._logger.log(
                    f"[STATE] Section {child_num}: scope_expansion -> "
                    f"{new_state.value} (parent {parent_num} absorbed signal)"
                )
                absorbed_count += 1
                parent_context = _append_scope_expansion_context(
                    parent_context, signal,
                )
                self._record_scope_expansion_decision(
                    planspace, parent_num, child_num, signal, signal_path,
                )
            except InvalidTransitionError:
                self._logger.log(
                    f"[STATE] Section {child_num}: scope expansion signal "
                    f"was valid but could not be resumed"
                )

        if absorbed_count:
            set_section_state(
                db_path,
                parent_num,
                SectionState.AWAITING_CHILDREN,
                context=parent_context,
            )
            self._logger.log(
                f"[STATE] Section {parent_num}: absorbed "
                f"{absorbed_count} scope expansion signal(s); "
                f"remaining in awaiting_children"
            )

    def _load_section_context(
        self,
        db_path: str | Path,
        section_number: str,
    ) -> dict:
        """Load the current structured context for a section row."""
        from flow.service.task_db_client import task_db

        with task_db(db_path) as conn:
            row = conn.execute(
                "SELECT context_json FROM section_states WHERE section_number = ?",
                (section_number,),
            ).fetchone()
        return _parse_context(row[0] if row else None)

    def _load_scope_expansion_signal(
        self,
        signal_path: Path,
        *,
        child_num: str,
        parent_num: str,
    ) -> dict | None:
        """Load and validate a scope-expansion signal artifact."""
        raw = self._artifact_io.read_json(signal_path)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            self._artifact_io.rename_malformed(signal_path)
            return None

        child_section = str(raw.get("child_section", "")).strip()
        parent_section = str(raw.get("parent_section", "")).strip()
        problem_statement = str(raw.get("problem_statement", "")).strip()
        why = str(raw.get("why", "")).strip()
        suggested_reframe = str(raw.get("suggested_reframe", "")).strip()
        attempted_raw = raw.get("attempted", [])

        if not isinstance(attempted_raw, list):
            self._artifact_io.rename_malformed(signal_path)
            return None

        attempted = [
            str(item).strip()
            for item in attempted_raw
            if str(item).strip()
        ]

        valid = (
            child_section == child_num
            and parent_section == parent_num
            and bool(problem_statement)
            and bool(why)
            and bool(suggested_reframe)
        )
        if not valid:
            self._artifact_io.rename_malformed(signal_path)
            return None

        return {
            "child_section": child_section,
            "parent_section": parent_section,
            "problem_statement": problem_statement,
            "why": why,
            "attempted": attempted,
            "suggested_reframe": suggested_reframe,
        }

    def _record_scope_expansion_decision(
        self,
        planspace: Path,
        parent_num: str,
        child_num: str,
        signal: dict,
        signal_path: Path,
    ) -> None:
        """Append a durable parent receipt for an absorbed child signal."""
        decisions = Decisions(artifact_io=self._artifact_io)
        decisions_dir = PathRegistry(planspace).decisions_dir()
        existing = decisions.load_decisions(decisions_dir, section=parent_num)
        next_num = len(existing) + 1
        decision = Decision(
            id=f"d-{parent_num}-{next_num:03d}",
            scope="section",
            section=parent_num,
            problem_id=None,
            parent_problem_id=None,
            concern_scope="scope-expansion-absorption",
            proposal_summary=(
                f"Absorbed scope expansion from child {child_num}: "
                f"{signal['problem_statement']}"
            ),
            alignment_to_parent=signal["suggested_reframe"],
            status="decided",
            why_unsolved=signal["why"],
            evidence=[str(signal_path), *signal["attempted"]],
            next_action=(
                f"Revise child {child_num} scope grant and resume it in "
                f"{SectionState.PROPOSING.value}"
            ),
        )
        decisions.record_decision(decisions_dir, decision)

    def _update_child_scope_grant(
        self,
        db_path: str | Path,
        child_num: str,
        scope_grant: str,
    ) -> None:
        """Persist the revised child scope grant without changing state."""
        current_state = get_section_state(db_path, child_num)
        set_section_state(
            db_path,
            child_num,
            current_state,
            scope_grant=scope_grant,
        )

    # ------------------------------------------------------------------
    # Orphan cleanup
    # ------------------------------------------------------------------

    def _check_orphaned_children(self, db_path: str | Path) -> None:
        """Fail child sections whose parent has reached a terminal state.

        A child is orphaned when its parent is in COMPLETE, FAILED, or
        ESCALATED but the child itself is still running.  This is a
        structural check -- it replaces starvation-based heuristics for
        child sections.  Root sections (parent_section IS NULL) are
        unaffected and continue using the starvation detector.

        This query returns nothing when no parent-child relationships
        exist (the current state), making the check purely additive.
        """
        from flow.service.task_db_client import task_db

        terminal_values = tuple(s.value for s in _FULLY_TERMINAL)
        terminal_placeholders = ",".join("?" for _ in terminal_values)

        with task_db(db_path) as conn:
            rows = conn.execute(
                f"SELECT c.section_number FROM section_states c "
                f"JOIN section_states p ON c.parent_section = p.section_number "
                f"WHERE c.parent_section IS NOT NULL "
                f"AND p.state IN ({terminal_placeholders}) "
                f"AND c.state NOT IN ({terminal_placeholders})",
                terminal_values + terminal_values,
            ).fetchall()

        for (sec_num,) in rows:
            set_section_state(
                db_path, sec_num, SectionState.FAILED,
                error="parent_terminated",
            )
            self._logger.log(
                f"[STATE] Section {sec_num}: -> failed "
                f"(orphan cleanup: parent terminated)"
            )


# ---------------------------------------------------------------------------
# Task completion -> state advance (called by reconciler)
# ---------------------------------------------------------------------------

# Map (task_type, success) -> SectionEvent for section-scoped tasks.
# Each entry maps a completed task to the event that drives the next
# state transition.  Task types that need context-dependent routing
# (e.g. readiness, assessment) are handled in advance_on_task_completion.
_TASK_EVENT_MAP: dict[tuple[str, bool], SectionEvent] = {
    # --- excerpt / problem-frame ---
    ("section.excerpt", True): SectionEvent.excerpt_complete,
    ("section.excerpt", False): SectionEvent.error,

    # --- intent pipeline ---
    ("section.intent_triage", True): SectionEvent.triage_complete,
    ("section.intent_triage", False): SectionEvent.error,
    ("section.intent_pack", True): SectionEvent.intent_pack_complete,
    ("section.intent_pack", False): SectionEvent.error,

    # --- proposal ---
    ("section.propose", True): SectionEvent.proposal_complete,
    ("section.propose", False): SectionEvent.error,

    # --- microstrategy ---
    ("section.microstrategy", True): SectionEvent.microstrategy_complete,
    ("section.microstrategy", False): SectionEvent.error,

    # --- implementation ---
    ("section.implement", True): SectionEvent.implementation_complete,
    ("section.implement", False): SectionEvent.error,

    # --- verification ---
    ("section.verify", True): SectionEvent.verification_pass,
    ("section.verify", False): SectionEvent.verification_fail,

    # --- post-completion ---
    ("section.post_complete", True): SectionEvent.post_completion_done,
    ("section.post_complete", False): SectionEvent.error,

    # --- fractal descent / reassembly ---
    ("section.decompose_children", True): SectionEvent.excerpt_complete,
    ("section.decompose_children", False): SectionEvent.error,
    ("section.reassemble", True): SectionEvent.reassembly_complete,
    ("section.reassemble", False): SectionEvent.error,
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

    Several task types require context-dependent event selection:
    - ``section.problem_frame``: valid/invalid based on context
    - ``section.philosophy``: ready/blocked based on context
    - ``section.assess``: alignment pass/fail based on context
    - ``section.risk_eval``: accepted/deferred/reopened based on context
    - ``section.impl_assess``: impl alignment pass/fail based on context
    - ``section.readiness_check``: readiness pass/descent/blocked (legacy compat)
    """
    ctx = context or {}

    # --- context-dependent event routing ---
    if task_type == "section.problem_frame":
        if not success:
            event = SectionEvent.error
        elif ctx.get("valid", False):
            event = SectionEvent.problem_frame_valid
        else:
            event = SectionEvent.problem_frame_invalid

    elif task_type == "section.philosophy":
        if not success:
            event = SectionEvent.error
        elif ctx.get("ready", False):
            event = SectionEvent.philosophy_ready
        else:
            event = SectionEvent.philosophy_blocked

    elif task_type == "section.assess":
        if not success:
            event = SectionEvent.error
        elif ctx.get("aligned", False):
            event = SectionEvent.alignment_pass
        elif ctx.get("vertical_misalignment", False):
            event = SectionEvent.vertical_misalignment
        else:
            event = SectionEvent.alignment_fail

    elif task_type == "section.risk_eval":
        if not success:
            event = SectionEvent.error
        else:
            outcome = ctx.get("outcome", "accepted")
            if outcome == "deferred":
                event = SectionEvent.risk_deferred
            elif outcome == "reopened":
                event = SectionEvent.risk_reopened
            else:
                event = SectionEvent.risk_accepted

    elif task_type == "section.impl_assess":
        if not success:
            event = SectionEvent.error
        elif ctx.get("aligned", False):
            event = SectionEvent.impl_alignment_pass
        else:
            event = SectionEvent.impl_alignment_fail

    elif task_type == "section.readiness_check":
        # Legacy compatibility for readiness check tasks
        if not success:
            event = SectionEvent.error
        elif ctx.get("descent_required", False):
            event = SectionEvent.descent_required
        elif ctx.get("ready", False):
            event = SectionEvent.readiness_pass
        else:
            event = SectionEvent.readiness_blocked

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


def _append_scope_expansion_context(context: dict, signal: dict) -> dict:
    """Append a unique absorbed signal into parent transient context."""
    existing = context.get(_SCOPE_EXPANSION_CONTEXT_KEY, [])
    normalized_existing = existing if isinstance(existing, list) else []
    if signal not in normalized_existing:
        normalized_existing = [*normalized_existing, signal]
    merged = dict(context)
    merged[_SCOPE_EXPANSION_CONTEXT_KEY] = normalized_existing
    return merged


def _build_revised_scope_grant(signal: dict) -> str:
    """Build the canonical child scope grant from the absorbed signal."""
    attempted_lines = "\n".join(
        f"- {item}" for item in signal.get("attempted", [])
    )
    attempted_block = (
        f"\nAttempted locally:\n{attempted_lines}"
        if attempted_lines
        else ""
    )
    return (
        "Parent-approved revised scope grant\n"
        f"Problem to absorb: {signal['problem_statement']}\n"
        f"Why local scope failed: {signal['why']}\n"
        f"Approved reframe: {signal['suggested_reframe']}"
        f"{attempted_block}\n"
    )
