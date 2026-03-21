#!/usr/bin/env python3
"""Task dispatcher — polls the task queue and launches agents.

Single-threaded poll loop that:
1. Claims the next runnable task from the task store
2. Resolves the task type to an agent file + model via task_router
3. Dispatches the agent (with exponential retry on transient failures)
4. Marks the task complete or failed
5. Sends a mailbox notification to the submitter

Includes outage detection: if multiple consecutive tasks fail with similar
transient errors, the dispatcher pauses with escalating backoff before
resuming.

This is infrastructure, not an agent. It runs as a long-lived process.

Usage:
    python -m flow.engine.task_dispatcher <planspace>
    python -m flow.engine.task_dispatcher <planspace> --poll-interval 5
    python -m flow.engine.task_dispatcher <planspace> --once  # single pass, no loop
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dispatch.repository.metadata import DispatchMetaResult, Metadata
from dispatch.types import DispatchStatus
from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import (
    claim_runnable_task as _db_claim_runnable_task,
    complete_task_with_result as _db_complete_task_with_result,
    fail_task_with_result as _db_fail_task_with_result,
    reset_stuck_running_tasks as _db_reset_stuck,
    task_db as _task_db,
)
from flow.service.notifier import (
    notify_task_result,
    record_task_routing,
)
from flow.exceptions import FlowCorruptionError
from flow.repository.flow_context_store import (
    write_dispatch_prompt,
)
from flow.engine.result_projector import TaskResultProjector
from flow.types.context import TaskStatus
from flow.types.result_envelope import TaskResultEnvelope
from taskrouter import ensure_discovered, registry as _task_registry

from signals.types import TRUNCATE_TOKEN

if TYPE_CHECKING:
    from containers import AgentDispatcher, ArtifactIOService, FreshnessService, ModelPolicyService, PromptGuard, TaskRouterService
    from flow.engine.reconciler import Reconciler
    from flow.repository.flow_context_store import FlowContextStore
    from flow.service.notifier import Notifier

DISPATCHER_NAME = "task-dispatcher"
_DEFAULT_POLL_INTERVAL_SECONDS = 3.0
_dispatcher_sleep = time.sleep
logger = logging.getLogger(__name__)

# -- Retry constants --------------------------------------------------------
_RETRY_DELAYS_SECONDS = (30, 60, 120, 240)
_MAX_ATTEMPTS = len(_RETRY_DELAYS_SECONDS) + 1  # 5 total (1 initial + 4 retries)

# -- Outage detection constants ---------------------------------------------
_OUTAGE_CONSECUTIVE_THRESHOLD = 3
_OUTAGE_PAUSE_SCHEDULE_SECONDS = (60, 120, 300, 600)
_OUTAGE_MAX_PAUSE_SECONDS = 1800  # 30 minutes
_OUTAGE_ERROR_PATTERNS = re.compile(
    r"500|rate.?limit|connection|unavailable|overloaded",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskHandle:
    """Identity bundle for a claimed task — threads through dispatcher helpers."""

    db_path: str
    task_id: str
    task_type: str
    submitted_by: str


class _FinalizationVerdict:
    """Result of _finalize_task indicating whether the task should be retried."""
    COMPLETE = "complete"
    FAILED_PERMANENT = "failed_permanent"
    FAILED_TRANSIENT = "failed_transient"

    def __init__(self, verdict: str, error: str = "") -> None:
        self.verdict = verdict
        self.error = error

    @property
    def should_retry(self) -> bool:
        return self.verdict == self.FAILED_TRANSIENT


@dataclass
class _OutageState:
    """Tracks consecutive transient failures for outage detection."""
    consecutive_failures: int = 0
    pause_index: int = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.pause_index = 0

    @property
    def is_outage(self) -> bool:
        return self.consecutive_failures >= _OUTAGE_CONSECUTIVE_THRESHOLD

    @property
    def pause_seconds(self) -> int:
        if self.pause_index < len(_OUTAGE_PAUSE_SCHEDULE_SECONDS):
            return _OUTAGE_PAUSE_SCHEDULE_SECONDS[self.pause_index]
        return _OUTAGE_MAX_PAUSE_SECONDS

    def advance_pause(self) -> None:
        if self.pause_index < len(_OUTAGE_PAUSE_SCHEDULE_SECONDS):
            self.pause_index += 1


class TaskDispatcher:
    def __init__(
        self,
        prompt_guard: PromptGuard,
        freshness: FreshnessService,
        dispatcher: AgentDispatcher,
        policies: ModelPolicyService,
        notifier: Notifier,
        reconciler: Reconciler,
        flow_context_store: FlowContextStore,
        artifact_io: ArtifactIOService,
        task_router: TaskRouterService,
    ) -> None:
        self._prompt_guard = prompt_guard
        self._freshness = freshness
        self._dispatcher = dispatcher
        self._policies = policies
        self._notifier = notifier
        self._reconciler = reconciler
        self._flow_context_store = flow_context_store
        self._artifact_io = artifact_io
        self._task_router = task_router
        self._retry_counts: dict[str, int] = {}
        self._outage = _OutageState()
        self._sleep = _dispatcher_sleep  # seam for testing

    def _read_dispatch_meta(self, meta_path: Path) -> DispatchMetaResult:
        """Read the dispatch metadata sidecar with fail-closed semantics."""
        result = Metadata(artifact_io=self._artifact_io).read_dispatch_metadata(meta_path)
        if result.is_corrupt:
            log(
                f"WARNING: Malformed dispatch meta at {meta_path} "
                f"— renaming to .malformed.json"
            )
        return result

    def _fail_task(self, h: TaskHandle, err, *,
                   planspace=None, output_path=None, codespace=None):
        """Mark a task as failed, notify submitter, and optionally reconcile."""
        result_envelope_path = None
        if planspace is not None:
            result_envelope_path = self._write_result_envelope(
                planspace,
                TaskResultEnvelope(
                    task_id=int(h.task_id),
                    task_type=h.task_type,
                    status=TaskStatus.FAILED.value,
                    output_path=output_path,
                    error=str(err),
                ),
            )
        _db_fail_task_with_result(
            h.db_path,
            h.task_id,
            error=err,
            output_path=output_path,
            result_envelope_path=result_envelope_path,
        )
        notify_task_result(h.db_path, h.submitted_by, h.task_id, h.task_type, TaskStatus.FAILED, err)
        if planspace is not None:
            self._reconciler.reconcile_task_completion(
                Path(h.db_path), planspace, int(h.task_id),
                TaskStatus.FAILED, output_path,
                error=err, codespace=codespace,
            )

    def _write_result_envelope(
        self,
        planspace: Path,
        envelope: TaskResultEnvelope,
    ) -> str:
        envelope_path = PathRegistry(planspace).task_result_envelope(envelope.task_id)
        self._artifact_io.write_json(envelope_path, envelope)
        return str(envelope_path)

    def _build_result_envelope(
        self,
        h: TaskHandle,
        planspace: Path,
        output_path: Path | None,
        *,
        status: str,
        error: str | None = None,
    ) -> TaskResultEnvelope:
        task_dict = {
            "id": h.task_id,
            "task_type": h.task_type,
            "status": status,
            "error": error,
            "output_path": str(output_path) if output_path is not None else None,
        }
        try:
            envelope = TaskResultProjector(self._artifact_io).project(
                task_dict,
                str(output_path) if output_path is not None else None,
                planspace,
            )
        except Exception:  # noqa: BLE001
            envelope = None
        if envelope is not None:
            return envelope
        return TaskResultEnvelope(
            task_id=int(h.task_id),
            task_type=h.task_type,
            status=status,
            output_path=str(output_path) if output_path is not None else None,
            unresolved_problems=[],
            new_value_axes=[],
            partial_solutions=[],
            scope_expansions=[],
            error=error,
        )

    def _resolve_prompt(self, task, planspace, h: TaskHandle):
        """Validate and resolve the task payload to a prompt path.

        Returns the prompt ``Path`` on success, ``None`` on validation failure
        (the task is already failed in that case).
        """
        payload_path = task.get("payload")
        if not payload_path:
            err = "no payload_path — queued tasks require payload-backed runtime context"
            log(f"ERROR: task {h.task_id}: {err}")
            self._fail_task(h, err, planspace=planspace)
            return None

        prompt_path = Path(payload_path)
        if not prompt_path.is_absolute():
            prompt_path = planspace / prompt_path
        if not prompt_path.exists():
            err = f"payload declared but not found: {prompt_path}"
            log(f"ERROR: {err} — failing task {h.task_id}")
            self._fail_task(h, err, planspace=planspace)
            return None

        violations = self._prompt_guard.validate_dynamic(
            prompt_path.read_text(encoding="utf-8"),
        )
        if violations:
            err = f"payload prompt blocked — template violations: {violations}"
            log(f"ERROR: task {h.task_id}: {err}")
            self._fail_task(h, err, planspace=planspace)
            return None

        return prompt_path

    def _claim_task(self, db_path: str, task_id: str) -> bool:
        with _task_db(db_path) as conn:
            cur = conn.execute(
                "UPDATE tasks SET status='running', claimed_by=?, "
                "claimed_at=datetime('now'), updated_at=datetime('now') "
                "WHERE id=? AND status='pending'",
                (DISPATCHER_NAME, int(task_id)),
            )
            conn.commit()
            return cur.rowcount > 0

    def _run_qa_gate(self, planspace, h: TaskHandle,
                     agent_file, prompt_path, task):
        """Run the QA dispatch interceptor. Returns False if rejected."""
        from qa.service.qa_gate import QaGate
        qa_gate = QaGate(
            artifact_io=self._artifact_io,
            task_router=self._task_router,
            policies=self._policies,
            dispatcher=self._dispatcher,
            prompt_guard=self._prompt_guard,
        )
        intercept = qa_gate.evaluate(
            planspace, agent_file, prompt_path,
            task=task,
        )
        if intercept is None:
            return True

        log(f"QA intercept: evaluating task {h.task_id} ({h.task_type})")
        self._notifier.record_qa_intercept(
            planspace, h.task_id,
            None if intercept.intercepted else intercept.verdict,
            db_path=h.db_path, reason_code=intercept.output_path,
        )

        if not intercept.intercepted:
            err = f"QA interceptor rejected: see {intercept.verdict}"
            log(f"QA REJECT: task {h.task_id}: {err}")
            self._fail_task(h, err)
            return False

        if intercept.output_path:
            log(f"QA DEGRADED: task {h.task_id} (reason: {intercept.output_path}) — failing open")
        else:
            log(f"QA PASS: task {h.task_id}")
        return True

    def _wrap_flow_context(self, task, planspace, h: TaskHandle, prompt_path):
        """Wrap prompt with flow context if present. Returns updated prompt path."""
        flow_context_relpath = task.get("flow_context")
        if not flow_context_relpath:
            return prompt_path

        continuation_relpath = task.get("continuation")
        trigger_gate_id = task.get("trigger_gate")
        try:
            flow_ctx = self._flow_context_store.build_flow_context(
                planspace,
                flow_context_path=flow_context_relpath,
                continuation_path=continuation_relpath,
                trigger_gate_id=trigger_gate_id,
            )
        except FlowCorruptionError as exc:
            err = f"flow context corrupt: {exc}"
            log(f"ERROR: task {h.task_id}: {err}")
            self._fail_task(h, err)
            return None

        if flow_ctx is not None:
            prompt_path = write_dispatch_prompt(
                planspace, int(h.task_id), prompt_path,
                flow_context_path=flow_context_relpath,
            )
            log(f"  flow context wrapped -> {prompt_path.name}")

        return prompt_path

    def _check_freshness(self, planspace, h: TaskHandle,
                         section_number, freshness_token, codespace):
        """Verify the freshness token still matches current state.

        Returns True if fresh (or no token to check), False if stale
        (the task is already failed in that case).
        """
        if not freshness_token or not section_number:
            return True
        current_token = self._freshness.compute(planspace, section_number)
        if current_token == freshness_token:
            return True
        err = (
            f"stale alignment — section-{section_number} inputs changed "
            f"(submitted={freshness_token[:TRUNCATE_TOKEN]}, "
            f"current={current_token[:TRUNCATE_TOKEN]})"
        )
        log(f"Task {h.task_id} stale: {err}")
        self._fail_task(h, err, planspace=planspace, codespace=codespace)
        return False

    def _finalize_task(self, h: TaskHandle, planspace,
                       output, output_path, codespace) -> _FinalizationVerdict:
        """Evaluate dispatch result and mark task complete or failed.

        Returns a ``_FinalizationVerdict`` so the caller can decide whether
        to retry (transient failure) or accept the outcome (complete /
        permanent failure).
        """
        meta_path = output_path.with_suffix(".meta.json")
        meta_result = self._read_dispatch_meta(meta_path)

        if meta_result.is_corrupt:
            err = (
                "dispatch meta sidecar corrupt — "
                f"renamed to {meta_path.with_suffix('.malformed.json').name}"
            )
            log(f"ERROR: task {h.task_id}: {err}")
            self._fail_task(h, err,
                       planspace=planspace, output_path=str(output_path),
                       codespace=codespace)
            return _FinalizationVerdict(_FinalizationVerdict.FAILED_PERMANENT, err)

        timed_out = False
        agent_failed = False
        rc = None
        if meta_result.data is not None:
            timed_out = meta_result.data.get("timed_out", False)
            rc = meta_result.data.get("returncode")
            if rc is not None and rc != 0:
                agent_failed = True

        if not timed_out and output.status is DispatchStatus.TIMEOUT:
            timed_out = True

        if timed_out:
            err = "Agent timeout (600s)"
            log(f"Task {h.task_id} timed out")
            self._fail_task(h, err, planspace=planspace, codespace=codespace)
            return _FinalizationVerdict(_FinalizationVerdict.FAILED_PERMANENT, err)
        elif agent_failed:
            err = f"Agent exited with return code {rc}"
            # Return transient verdict — caller decides whether to retry or
            # permanently fail based on retry budget.
            return _FinalizationVerdict(_FinalizationVerdict.FAILED_TRANSIENT, err)
        else:
            result_envelope = self._build_result_envelope(
                h,
                planspace,
                output_path,
                status=TaskStatus.COMPLETE.value,
            )
            result_envelope_path = self._write_result_envelope(
                planspace,
                result_envelope,
            )
            _db_complete_task_with_result(
                h.db_path,
                h.task_id,
                output_path=str(output_path),
                result_envelope_path=result_envelope_path,
                planspace=planspace,
                result_envelope=result_envelope,
            )
            notify_task_result(h.db_path, h.submitted_by, h.task_id, h.task_type, TaskStatus.COMPLETE,
                               str(output_path))
            log(f"Task {h.task_id} complete -> {output_path}")
            self._reconciler.reconcile_task_completion(
                Path(h.db_path), planspace, int(h.task_id),
                TaskStatus.COMPLETE, str(output_path), codespace=codespace,
            )
            return _FinalizationVerdict(_FinalizationVerdict.COMPLETE)

    def dispatch_task(
        self,
        db_path: str,
        planspace: Path,
        task: dict[str, str],
        codespace: Path | None = None,
        model_policy: dict[str, str] | None = None,
        already_claimed: bool = False,
    ) -> None:
        """Claim, dispatch, and complete/fail a single task.

        When *already_claimed* is True the task was atomically claimed by
        ``claim_runnable_task`` and the explicit claim step is skipped.
        """
        h = TaskHandle(
            db_path=db_path,
            task_id=task["id"],
            task_type=task["type"],
            submitted_by=task.get("by", "unknown"),
        )
        registry = PathRegistry(planspace)

        # Resolve agent file and model.
        try:
            agent_file, model = _task_registry.resolve(h.task_type, model_policy)
        except ValueError as e:
            log(f"ERROR: Cannot resolve task {h.task_id}: {e}")
            if not already_claimed and not self._claim_task(db_path, h.task_id):
                log(f"WARNING: Could not claim task {h.task_id}")
                return
            self._fail_task(h, str(e))
            return

        if not already_claimed and not self._claim_task(db_path, h.task_id):
            log(f"WARNING: Could not claim task {h.task_id}")
            return

        record_task_routing(planspace, h.task_id, agent_file, model, db_path=db_path)
        log(f"Dispatching task {h.task_id}: {h.task_type} -> {agent_file} ({model})")

        artifacts_dir = registry.artifacts

        # Validate and resolve prompt
        prompt_path = self._resolve_prompt(task, planspace, h)
        if prompt_path is None:
            return

        # QA gate
        if not self._run_qa_gate(planspace, h, agent_file, prompt_path, task):
            return

        # Flow context wrapping
        prompt_path = self._wrap_flow_context(task, planspace, h, prompt_path)
        if prompt_path is None:
            return

        output_path = artifacts_dir / f"task-{h.task_id}-output.md"

        section_number = _parse_section_number(task.get("scope"))

        # Gap 1: root-reframe signal check — if a root reframe is
        # active, pause section-scoped tasks until coordination
        # resolves the reframe.  O(1) file-existence check.
        if section_number is not None:
            reframe_path = registry.root_reframe_signal()
            if reframe_path.exists():
                err = (
                    "root-reframe signal active — section tasks paused "
                    "until coordination resolves the scope expansion"
                )
                log(f"Task {h.task_id} paused: {err}")
                self._fail_task(h, err, planspace=planspace, codespace=codespace)
                return

        if not self._check_freshness(planspace, h, section_number,
                                task.get("freshness"), codespace):
            return

        # Dispatch the agent with retry on transient failures.
        attempt = self._retry_counts.get(h.task_id, 0) + 1
        while True:
            output = self._dispatcher.dispatch(
                model, prompt_path, output_path,
                planspace, None,
                section_number=section_number,
                codespace=codespace,
                agent_file=agent_file,
            )

            verdict = self._finalize_task(h, planspace, output, output_path, codespace)

            if not verdict.should_retry:
                # Completed or permanently failed — clean up retry tracking.
                self._retry_counts.pop(h.task_id, None)
                if verdict.verdict == _FinalizationVerdict.COMPLETE:
                    self._outage.record_success()
                return

            # Transient failure — check retry budget.
            if attempt >= _MAX_ATTEMPTS:
                log(
                    f"Task {h.task_id} failed (attempt {attempt}/{_MAX_ATTEMPTS}), "
                    f"retries exhausted: {verdict.error}"
                )
                self._fail_task(
                    h, verdict.error,
                    planspace=planspace, output_path=str(output_path),
                    codespace=codespace,
                )
                self._retry_counts.pop(h.task_id, None)
                self._outage.record_failure()
                return

            delay = _RETRY_DELAYS_SECONDS[attempt - 1]
            log(
                f"Task {h.task_id} failed (attempt {attempt}/{_MAX_ATTEMPTS}), "
                f"retrying in {delay}s: {verdict.error}"
            )
            self._retry_counts[h.task_id] = attempt
            self._outage.record_failure()
            self._sleep(delay)
            attempt += 1

    def main(self) -> None:
        parser = argparse.ArgumentParser(description="Task queue dispatcher")
        parser.add_argument("planspace", type=Path, help="Planspace directory")
        parser.add_argument(
            "--poll-interval", type=float, default=_DEFAULT_POLL_INTERVAL_SECONDS,
            help=f"Seconds between polls (default: {_DEFAULT_POLL_INTERVAL_SECONDS})",
        )
        parser.add_argument(
            "--once", action="store_true",
            help="Process one task and exit (no loop)",
        )
        parser.add_argument(
            "--codespace", type=Path, default=None,
            help="Project directory to pass via --project to agents",
        )
        args = parser.parse_args()

        planspace = args.planspace.resolve()
        db_path = str(PathRegistry(planspace).run_db())

        if not Path(db_path).exists():
            log(f"ERROR: Database not found at {db_path}")
            sys.exit(1)

        ensure_discovered()

        reset_count = _db_reset_stuck(db_path)
        if reset_count:
            log(f"Reset {reset_count} stuck running tasks to pending on startup")

        log(f"Starting dispatcher (planspace={planspace}, poll={args.poll_interval}s)")

        while True:
            try:
                # Outage pause: if too many consecutive transient failures,
                # back off before trying the next task.
                if self._outage.is_outage:
                    pause = self._outage.pause_seconds
                    log(
                        f"OUTAGE DETECTED: {self._outage.consecutive_failures} "
                        f"consecutive failures. Pausing dispatcher for {pause}s"
                    )
                    self._outage.advance_pause()
                    self._sleep(pause)

                # PAT-0005: refresh policy per dispatch cycle (not startup-only)
                model_policy = self._policies.load(planspace)

                task = _db_claim_runnable_task(db_path, DISPATCHER_NAME)

                if task:
                    self.dispatch_task(
                        db_path, planspace, task,
                        codespace=args.codespace,
                        model_policy=model_policy,
                        already_claimed=True,
                    )
                elif not args.once:
                    # No runnable tasks — wait before polling again.
                    self._sleep(args.poll_interval)

                if args.once:
                    break

            except KeyboardInterrupt:
                log("Shutting down (interrupted)")
                break
            except Exception as e:  # noqa: BLE001 — top-level daemon loop, must not crash
                log(f"ERROR in dispatch loop: {e}")
                if args.once:
                    sys.exit(1)
                self._sleep(args.poll_interval)


def _parse_section_number(scope: str | None) -> str | None:
    """Extract section number from a scope string like 'section-3'."""
    if not scope:
        return None
    m = re.match(r'^section-(\d+)$', scope)
    return m.group(1) if m else None


def log(msg: str) -> None:
    """Print a timestamped dispatcher log message."""
    print(f"[{DISPATCHER_NAME}] {msg}", flush=True)


def _read_dispatch_meta(meta_path: Path) -> DispatchMetaResult:
    """Read the dispatch metadata sidecar with fail-closed semantics."""
    from containers import Services

    result = Metadata(artifact_io=Services.artifact_io()).read_dispatch_metadata(meta_path)
    if result.is_corrupt:
        log(
            f"WARNING: Malformed dispatch meta at {meta_path} "
            f"— renaming to .malformed.json"
        )
    return result


def dispatch_task(
    db_path: str,
    planspace: Path,
    task: dict[str, str],
    codespace: Path | None = None,
    model_policy: dict[str, str] | None = None,
    already_claimed: bool = False,
) -> None:
    """Module-level entry point for tests and CLI callers."""
    return _get_dispatcher().dispatch_task(
        db_path,
        planspace,
        task,
        codespace=codespace,
        model_policy=model_policy,
        already_claimed=already_claimed,
    )


def _get_dispatcher() -> TaskDispatcher:
    from containers import Services
    from flow.engine.flow_submitter import FlowSubmitter
    from flow.engine.reconciler import Reconciler
    from flow.repository.flow_context_store import FlowContextStore
    from flow.repository.gate_repository import GateRepository
    from flow.service.notifier import Notifier
    from implementation.service.traceability_writer import TraceabilityWriter
    artifact_io = Services.artifact_io()
    flow_context_store = FlowContextStore(artifact_io)
    flow_submitter = FlowSubmitter(
        freshness=Services.freshness(),
        flow_context_store=flow_context_store,
    )
    gate_repository = GateRepository(artifact_io)
    reconciler = Reconciler(
        artifact_io=artifact_io,
        research=Services.research(),
        prompt_guard=Services.prompt_guard(),
        flow_submitter=flow_submitter,
        gate_repository=gate_repository,
        traceability_writer=TraceabilityWriter(
            artifact_io=artifact_io,
            hasher=Services.hasher(),
            logger=Services.logger(),
            section_alignment=Services.section_alignment(),
        ),
    )
    notifier = Notifier(logger=Services.logger())
    return TaskDispatcher(
        prompt_guard=Services.prompt_guard(),
        freshness=Services.freshness(),
        dispatcher=Services.dispatcher(),
        policies=Services.policies(),
        notifier=notifier,
        reconciler=reconciler,
        flow_context_store=flow_context_store,
        artifact_io=artifact_io,
        task_router=Services.task_router(),
    )


def main() -> None:
    _get_dispatcher().main()


if __name__ == "__main__":
    main()
