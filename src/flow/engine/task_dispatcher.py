#!/usr/bin/env python3
"""Task dispatcher — polls the task queue and launches agents.

Single-threaded poll loop that:
1. Finds the next runnable task via db.sh next-task
2. Resolves the task type to an agent file + model via task_router
3. Claims the task
4. Dispatches the agent
5. Marks the task complete or failed
6. Sends a mailbox notification to the submitter

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
from dataclasses import dataclass
from pathlib import Path

from dispatch.repository.metadata import DispatchMetaResult, read_dispatch_metadata
from dispatch.types import DispatchStatus
from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import (
    claim_task as _db_claim_task,
    complete_task as _db_complete_task,
    fail_task as _db_fail_task,
    next_task as _db_next_task,
)
from flow.service.notifier import (
    notify_task_result,
    record_qa_intercept,
    record_task_routing,
)
from flow.exceptions import FlowCorruptionError
from flow.service.flow_facade import (
    build_flow_context,
    reconcile_task_completion,
    write_dispatch_prompt,
)
from flow.types.context import TaskStatus
from taskrouter import ensure_discovered, registry as _task_registry

from containers import Services
from signals.types import TRUNCATE_TOKEN

DISPATCHER_NAME = "task-dispatcher"
_DEFAULT_POLL_INTERVAL_SECONDS = 3.0
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class TaskHandle:
    """Identity bundle for a claimed task — threads through dispatcher helpers."""

    db_path: str
    task_id: str
    task_type: str
    submitted_by: str


def _read_dispatch_meta(meta_path: Path) -> DispatchMetaResult:
    """Read the dispatch metadata sidecar with fail-closed semantics."""
    result = read_dispatch_metadata(meta_path)
    if result.is_corrupt:
        log(
            f"WARNING: Malformed dispatch meta at {meta_path} "
            f"— renaming to .malformed.json"
        )
    return result


def _fail_task(h: TaskHandle, err, *,
               planspace=None, output_path=None, codespace=None):
    """Mark a task as failed, notify submitter, and optionally reconcile."""
    _db_fail_task(h.db_path, h.task_id, error=err)
    notify_task_result(h.db_path, h.submitted_by, h.task_id, h.task_type, TaskStatus.FAILED, err)
    if planspace is not None:
        reconcile_task_completion(
            Path(h.db_path), planspace, int(h.task_id),
            TaskStatus.FAILED, output_path,
            error=err, codespace=codespace,
        )


def _resolve_prompt(task, planspace, h: TaskHandle):
    """Validate and resolve the task payload to a prompt path.

    Returns the prompt ``Path`` on success, ``None`` on validation failure
    (the task is already failed in that case).
    """
    payload_path = task.get("payload")
    if not payload_path:
        err = "no payload_path — queued tasks require payload-backed runtime context"
        log(f"ERROR: task {h.task_id}: {err}")
        _fail_task(h, err)
        return None

    prompt_path = Path(payload_path)
    if not prompt_path.is_absolute():
        prompt_path = planspace / prompt_path
    if not prompt_path.exists():
        err = f"payload declared but not found: {prompt_path}"
        log(f"ERROR: {err} — failing task {h.task_id}")
        _fail_task(h, err)
        return None

    violations = Services.prompt_guard().validate_dynamic(
        prompt_path.read_text(encoding="utf-8"),
    )
    if violations:
        err = f"payload prompt blocked — template violations: {violations}"
        log(f"ERROR: task {h.task_id}: {err}")
        _fail_task(h, err)
        return None

    return prompt_path


def _run_qa_gate(planspace, h: TaskHandle,
                 agent_file, prompt_path, task):
    """Run the QA dispatch interceptor. Returns False if rejected."""
    from qa.service.qa_gate import evaluate_qa_gate
    intercept = evaluate_qa_gate(
        planspace, agent_file, prompt_path,
        task=task,
    )
    if intercept is None:
        return True

    log(f"QA intercept: evaluating task {h.task_id} ({h.task_type})")
    record_qa_intercept(
        planspace, h.task_id,
        None if intercept.intercepted else intercept.verdict,
        db_path=h.db_path, reason_code=intercept.output_path,
    )

    if not intercept.intercepted:
        err = f"QA interceptor rejected: see {intercept.verdict}"
        log(f"QA REJECT: task {h.task_id}: {err}")
        _fail_task(h, err)
        return False

    if intercept.output_path:
        log(f"QA DEGRADED: task {h.task_id} (reason: {intercept.output_path}) — failing open")
    else:
        log(f"QA PASS: task {h.task_id}")
    return True


def _wrap_flow_context(task, planspace, h: TaskHandle, prompt_path):
    """Wrap prompt with flow context if present. Returns updated prompt path."""
    flow_context_relpath = task.get("flow_context")
    if not flow_context_relpath:
        return prompt_path

    continuation_relpath = task.get("continuation")
    trigger_gate_id = task.get("trigger_gate")
    try:
        flow_ctx = build_flow_context(
            planspace,
            flow_context_path=flow_context_relpath,
            continuation_path=continuation_relpath,
            trigger_gate_id=trigger_gate_id,
        )
    except FlowCorruptionError as exc:
        err = f"flow context corrupt: {exc}"
        log(f"ERROR: task {h.task_id}: {err}")
        _fail_task(h, err)
        return None

    if flow_ctx is not None:
        prompt_path = write_dispatch_prompt(
            planspace, int(h.task_id), prompt_path,
            flow_context_path=flow_context_relpath,
        )
        log(f"  flow context wrapped -> {prompt_path.name}")

    return prompt_path


def _parse_section_number(scope: str | None) -> str | None:
    """Extract section number from a scope string like 'section-3'."""
    if not scope:
        return None
    m = re.match(r'^section-(\d+)$', scope)
    return m.group(1) if m else None


def _check_freshness(planspace, h: TaskHandle,
                     section_number, freshness_token, codespace):
    """Verify the freshness token still matches current state.

    Returns True if fresh (or no token to check), False if stale
    (the task is already failed in that case).
    """
    if not freshness_token or not section_number:
        return True
    current_token = Services.freshness().compute(planspace, section_number)
    if current_token == freshness_token:
        return True
    err = (
        f"stale alignment — section-{section_number} inputs changed "
        f"(submitted={freshness_token[:TRUNCATE_TOKEN]}, "
        f"current={current_token[:TRUNCATE_TOKEN]})"
    )
    log(f"Task {h.task_id} stale: {err}")
    _fail_task(h, err, planspace=planspace, codespace=codespace)
    return False


def _finalize_task(h: TaskHandle, planspace,
                   output, output_path, codespace):
    """Evaluate dispatch result and mark task complete or failed."""
    meta_path = output_path.with_suffix(".meta.json")
    meta_result = _read_dispatch_meta(meta_path)

    if meta_result.is_corrupt:
        err = (
            "dispatch meta sidecar corrupt — "
            f"renamed to {meta_path.with_suffix('.malformed.json').name}"
        )
        log(f"ERROR: task {h.task_id}: {err}")
        _fail_task(h, err,
                   planspace=planspace, output_path=str(output_path),
                   codespace=codespace)
        return

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
        _fail_task(h, err, planspace=planspace, codespace=codespace)
    elif agent_failed:
        err = f"Agent exited with return code {rc}"
        log(f"Task {h.task_id} failed: {err}")
        _fail_task(h, err,
                   planspace=planspace, output_path=str(output_path),
                   codespace=codespace)
    else:
        _db_complete_task(h.db_path, h.task_id, output_path=str(output_path))
        notify_task_result(h.db_path, h.submitted_by, h.task_id, h.task_type, TaskStatus.COMPLETE,
                           str(output_path))
        log(f"Task {h.task_id} complete -> {output_path}")
        reconcile_task_completion(
            Path(h.db_path), planspace, int(h.task_id),
            TaskStatus.COMPLETE, str(output_path), codespace=codespace,
        )


def dispatch_task(
    db_path: str,
    planspace: Path,
    task: dict[str, str],
    codespace: Path | None = None,
    model_policy: dict[str, str] | None = None,
) -> None:
    """Claim, dispatch, and complete/fail a single task."""
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
        _db_claim_task(db_path, DISPATCHER_NAME, h.task_id)
        _fail_task(h, str(e))
        return

    # Claim the task.
    try:
        _db_claim_task(db_path, DISPATCHER_NAME, h.task_id)
    except RuntimeError as e:
        log(f"WARNING: Could not claim task {h.task_id}: {e}")
        return

    record_task_routing(planspace, h.task_id, agent_file, model, db_path=db_path)
    log(f"Dispatching task {h.task_id}: {h.task_type} -> {agent_file} ({model})")

    artifacts_dir = registry.artifacts
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Validate and resolve prompt
    prompt_path = _resolve_prompt(task, planspace, h)
    if prompt_path is None:
        return

    # QA gate
    if not _run_qa_gate(planspace, h, agent_file, prompt_path, task):
        return

    # Flow context wrapping
    prompt_path = _wrap_flow_context(task, planspace, h, prompt_path)
    if prompt_path is None:
        return

    output_path = artifacts_dir / f"task-{h.task_id}-output.md"

    section_number = _parse_section_number(task.get("scope"))

    if not _check_freshness(planspace, h, section_number,
                            task.get("freshness"), codespace):
        return

    # Dispatch the agent
    output = Services.dispatcher().dispatch(
        model, prompt_path, output_path,
        planspace, None,
        section_number=section_number,
        codespace=codespace,
        agent_file=agent_file,
    )

    _finalize_task(h, planspace, output, output_path, codespace)


def log(msg: str) -> None:
    """Print a timestamped dispatcher log message."""
    print(f"[{DISPATCHER_NAME}] {msg}", flush=True)


def main() -> None:
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
    log(f"Starting dispatcher (planspace={planspace}, poll={args.poll_interval}s)")

    while True:
        try:
            # PAT-0005: refresh policy per dispatch cycle (not startup-only)
            model_policy = Services.policies().load(planspace)

            task = _db_next_task(db_path)

            if task:
                dispatch_task(
                    db_path, planspace, task,
                    codespace=args.codespace,
                    model_policy=model_policy,
                )
            elif not args.once:
                # No runnable tasks — wait before polling again.
                time.sleep(args.poll_interval)

            if args.once:
                break

        except KeyboardInterrupt:
            log("Shutting down (interrupted)")
            break
        except Exception as e:  # noqa: BLE001 — top-level daemon loop, must not crash
            log(f"ERROR in dispatch loop: {e}")
            if args.once:
                sys.exit(1)
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
