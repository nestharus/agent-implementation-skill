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
    python3 scripts/task_dispatcher.py <planspace>
    python3 scripts/task_dispatcher.py <planspace> --poll-interval 5
    python3 scripts/task_dispatcher.py <planspace> --once  # single pass, no loop
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from lib.dispatch.dispatch_metadata import DISPATCH_META_CORRUPT, read_dispatch_metadata
from lib.core.path_registry import PathRegistry
from lib.tasks.task_db_client import db_cmd
from lib.tasks.task_notifier import (
    notify_task_result,
    record_qa_intercept,
    record_task_routing,
)
from lib.tasks.task_parser import parse_task_output

# Resolve paths relative to this script's location.
SCRIPTS_DIR = Path(__file__).resolve().parent
WORKFLOW_HOME = Path(os.environ.get("WORKFLOW_HOME", SCRIPTS_DIR.parent))

# Import task_router and task_flow from the same directory.
sys.path.insert(0, str(SCRIPTS_DIR))
from task_flow import (  # noqa: E402
    FlowCorruptionError,
    build_flow_context,
    compute_section_freshness,
    reconcile_task_completion,
    write_dispatch_prompt,
)
from task_router import resolve_task  # noqa: E402

from section_loop.agent_templates import (  # noqa: E402
    validate_dynamic_content,
)
from section_loop.dispatch import dispatch_agent, read_model_policy  # noqa: E402

DISPATCHER_NAME = "task-dispatcher"

# Sentinel returned by _read_dispatch_meta when the sidecar file exists
# but contains malformed JSON.  Distinct from None (file absent) and
# dict (valid parse).
_DISPATCH_META_CORRUPT = DISPATCH_META_CORRUPT


def _db_cmd(db_path: str, command: str, *args: str) -> str:
    """Compatibility shim for tests that patch dispatcher-local DB calls."""
    return db_cmd(db_path, command, *args)


def _notify(
    db_path: str,
    target: str,
    task_id: str,
    task_type: str,
    status: str,
    detail: str,
) -> None:
    """Compatibility shim for tests that patch dispatcher-local notifications."""
    notify_task_result(db_path, target, task_id, task_type, status, detail)


def _record_task_routing(
    db_path: str,
    planspace: Path,
    task_id: str,
    task_type: str,
    agent_file: str,
    model: str,
) -> None:
    """Compatibility shim for dispatcher-local routing persistence."""
    record_task_routing(
        planspace,
        task_id,
        task_type,
        agent_file,
        model,
        db_path=db_path,
    )


def _record_qa_intercept(
    db_path: str,
    planspace: Path,
    task_id: str,
    task_type: str,
    rejection_reason: str | None,
    *,
    reason_code: str | None = None,
) -> None:
    """Compatibility shim for dispatcher-local QA logging."""
    record_qa_intercept(
        planspace,
        task_id,
        task_type,
        rejection_reason,
        db_path=db_path,
        reason_code=reason_code,
    )


def parse_next_task(output: str) -> dict[str, str] | None:
    """Parse the pipe-separated output of next-task into a dict.

    Returns None if no runnable tasks.
    Output format: id=N | type=T | by=B | prio=P [| problem=X] [| ...]
    """
    return parse_task_output(output)


def _read_dispatch_meta(meta_path: Path) -> dict | None | object:
    """Read the dispatch metadata sidecar with fail-closed semantics.

    Returns:
    - ``None`` when the file does not exist (allows timeout-prefix
      fallback to work).
    - A ``dict`` when the file exists and contains valid JSON.
    - ``_DISPATCH_META_CORRUPT`` when the file exists but is malformed
      or unreadable.  The corrupt file is renamed to
      ``.malformed.json`` for forensic preservation and a warning is
      logged (same pattern as ``_read_flow_json`` in task_flow.py).
    """
    data = read_dispatch_metadata(meta_path)
    if data is DISPATCH_META_CORRUPT:
        log(
            f"WARNING: Malformed dispatch meta at {meta_path} "
            f"— renaming to .malformed.json"
        )
        return _DISPATCH_META_CORRUPT
    return data


def dispatch_task(
    db_path: str,
    planspace: Path,
    task: dict[str, str],
    codespace: Path | None = None,
    model_policy: dict[str, str] | None = None,
) -> None:
    """Claim, dispatch, and complete/fail a single task."""
    task_id = task["id"]
    task_type = task["type"]
    submitted_by = task.get("by", "unknown")
    payload_path = task.get("payload")
    registry = PathRegistry(planspace)

    # Resolve agent file and model.
    try:
        agent_file, model = resolve_task(task_type, model_policy)
    except ValueError as e:
        log(f"ERROR: Cannot resolve task {task_id}: {e}")
        _db_cmd(db_path, "claim-task", DISPATCHER_NAME, task_id)
        _db_cmd(db_path, "fail-task", task_id, "--error", str(e))
        _notify(db_path, submitted_by, task_id, task_type, "failed", str(e))
        return

    # Claim the task.
    try:
        _db_cmd(db_path, "claim-task", DISPATCHER_NAME, task_id)
    except RuntimeError as e:
        log(f"WARNING: Could not claim task {task_id}: {e}")
        return

    # Record agent_file and model on the task row for observability.
    _record_task_routing(
        db_path,
        planspace,
        task_id,
        task_type,
        agent_file,
        model,
    )

    log(f"Dispatching task {task_id}: {task_type} -> {agent_file} ({model})")

    # Build the prompt path. payload_path is required for all queued tasks
    # (R80/P1). Fail closed if absent.
    artifacts_dir = registry.artifacts
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if payload_path:
        prompt_path = Path(payload_path)
        if not prompt_path.is_absolute():
            prompt_path = planspace / prompt_path
        if not prompt_path.exists():
            err = f"payload declared but not found: {prompt_path}"
            log(f"ERROR: {err} — failing task {task_id}")
            _db_cmd(db_path, "fail-task", task_id, "--error", err)
            _notify(db_path, submitted_by, task_id, task_type, "failed", err)
            return
        # V4/R77: validate agent-provided payload prompt
        violations = validate_dynamic_content(
            prompt_path.read_text(encoding="utf-8"),
        )
        if violations:
            err = f"payload prompt blocked — template violations: {violations}"
            log(f"ERROR: task {task_id}: {err}")
            _db_cmd(db_path, "fail-task", task_id, "--error", err)
            _notify(db_path, submitted_by, task_id, task_type, "failed", err)
            return
    else:
        # R80/P1: payload-backed context is mandatory for all queued tasks.
        # Metadata-only dispatch produces under-specified agents.
        err = "no payload_path — queued tasks require payload-backed runtime context"
        log(f"ERROR: task {task_id}: {err}")
        _db_cmd(db_path, "fail-task", task_id, "--error", err)
        _notify(db_path, submitted_by, task_id, task_type, "failed", err)
        return

    # --- QA dispatch interceptor (optional) ---
    # Lazy-import inside conditional to avoid hard dependency.
    try:
        from qa_interceptor import intercept_task, read_qa_parameters
        qa_params = read_qa_parameters(planspace)
    except Exception:
        qa_params = {}

    if qa_params.get("qa_mode"):
        log(f"QA intercept: evaluating task {task_id} ({task_type})")
        try:
            passed, rationale_path, reason_code = intercept_task(task, agent_file, planspace)
        except Exception as exc:
            # Fail-OPEN: QA errors must not block dispatch.
            log(f"QA ERROR for task {task_id}: {exc} — failing open (degraded)")
            passed = True
            rationale_path = None
            reason_code = "dispatch_error"

        _record_qa_intercept(
            db_path,
            planspace,
            task_id,
            task_type,
            None if passed else rationale_path,
            reason_code=reason_code,
        )

        if not passed:
            err = f"QA interceptor rejected: see {rationale_path}"
            log(f"QA REJECT: task {task_id}: {err}")
            _db_cmd(db_path, "fail-task", task_id, "--error", err)
            _notify(db_path, submitted_by, task_id, task_type, "failed", err)
            return
        # PAT-0014: distinguish genuine approval from degraded fallback
        if reason_code:
            log(f"QA DEGRADED: task {task_id} (reason: {reason_code}) — failing open")
        else:
            log(f"QA PASS: task {task_id}")

    # --- Flow context wrapping (Task 5) ---
    # If the task has flow metadata, create a wrapper prompt that
    # prepends flow context paths.  The original prompt is NOT mutated.
    flow_context_relpath = task.get("flow_context")
    continuation_relpath = task.get("continuation")
    trigger_gate_id = task.get("trigger_gate")
    if flow_context_relpath:
        try:
            flow_ctx = build_flow_context(
                planspace, int(task_id),
                flow_context_path=flow_context_relpath,
                continuation_path=continuation_relpath,
                trigger_gate_id=trigger_gate_id,
            )
        except FlowCorruptionError as exc:
            err = f"flow context corrupt: {exc}"
            log(f"ERROR: task {task_id}: {err}")
            _db_cmd(db_path, "fail-task", task_id, "--error", err)
            _notify(db_path, submitted_by, task_id, task_type, "failed", err)
            return

        if flow_ctx is not None:
            prompt_path = write_dispatch_prompt(
                planspace, int(task_id), prompt_path,
                flow_context_path=flow_context_relpath,
                continuation_path=continuation_relpath,
            )
            log(f"  flow context wrapped -> {prompt_path.name}")

    output_path = artifacts_dir / f"task-{task_id}-output.md"

    # P3: Recover section identity from queued task scope
    section_number = None
    scope = task.get("scope")
    if scope:
        m = re.match(r'^section-(\d+)$', scope)
        if m:
            section_number = m.group(1)

    # P4: Freshness gate for section-scoped queued tasks
    freshness_token = task.get("freshness")
    if freshness_token and section_number:
        current_token = compute_section_freshness(planspace, section_number)
        if current_token != freshness_token:
            err = (
                f"stale alignment — section-{section_number} inputs changed "
                f"(submitted={freshness_token[:8]}, "
                f"current={current_token[:8]})"
            )
            log(f"Task {task_id} stale: {err}")
            _db_cmd(db_path, "fail-task", task_id, "--error", err)
            _notify(db_path, submitted_by, task_id, task_type, "failed", err)
            reconcile_task_completion(
                Path(db_path), planspace, int(task_id),
                "failed", None, error=err, codespace=codespace,
            )
            return

    # V6: Dispatch through section_loop.dispatch for pause/alignment
    # handling, context sidecars, and per-dispatch monitoring.
    output = dispatch_agent(
        model, prompt_path, output_path,
        planspace, None,  # parent=None outside section-loop context
        section_number=section_number,
        codespace=codespace,
        agent_file=agent_file,
    )

    # Read dispatch metadata sidecar for return-code visibility
    meta_path = output_path.with_suffix(".meta.json")
    meta_result = _read_dispatch_meta(meta_path)

    if meta_result is _DISPATCH_META_CORRUPT:
        err = (
            "dispatch meta sidecar corrupt — "
            f"renamed to {meta_path.with_suffix('.malformed.json').name}"
        )
        log(f"ERROR: task {task_id}: {err}")
        _db_cmd(db_path, "fail-task", task_id, "--error", err)
        _notify(db_path, submitted_by, task_id, task_type, "failed", err)
        reconcile_task_completion(
            Path(db_path), planspace, int(task_id),
            "failed", str(output_path), error=err, codespace=codespace,
        )
        return

    timed_out = False
    agent_failed = False
    rc = None
    if isinstance(meta_result, dict):
        timed_out = meta_result.get("timed_out", False)
        rc = meta_result.get("returncode")
        if rc is not None and rc != 0:
            agent_failed = True

    # Fallback: detect timeout from output prefix when sidecar is absent
    if not timed_out and output.startswith("TIMEOUT:"):
        timed_out = True

    if timed_out:
        log(f"Task {task_id} timed out")
        _db_cmd(
            db_path, "fail-task", task_id,
            "--error", "Agent timeout (600s)",
        )
        _notify(
            db_path, submitted_by, task_id, task_type, "failed",
            "Agent timeout (600s)",
        )
        reconcile_task_completion(
            Path(db_path), planspace, int(task_id),
            "failed", None, error="Agent timeout (600s)", codespace=codespace,
        )
    elif agent_failed:
        err = f"Agent exited with return code {rc}"
        log(f"Task {task_id} failed: {err}")
        _db_cmd(db_path, "fail-task", task_id, "--error", err)
        _notify(db_path, submitted_by, task_id, task_type, "failed", err)
        reconcile_task_completion(
            Path(db_path), planspace, int(task_id),
            "failed", str(output_path), error=err, codespace=codespace,
        )
    else:
        _db_cmd(
            db_path, "complete-task", task_id,
            "--output", str(output_path),
        )
        _notify(
            db_path, submitted_by, task_id, task_type, "complete",
            str(output_path),
        )
        log(f"Task {task_id} complete -> {output_path}")
        reconcile_task_completion(
            Path(db_path), planspace, int(task_id),
            "complete", str(output_path), codespace=codespace,
        )


def log(msg: str) -> None:
    """Print a timestamped dispatcher log message."""
    print(f"[{DISPATCHER_NAME}] {msg}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Task queue dispatcher")
    parser.add_argument("planspace", type=Path, help="Planspace directory")
    parser.add_argument(
        "--poll-interval", type=float, default=3.0,
        help="Seconds between polls (default: 3)",
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

    log(f"Starting dispatcher (planspace={planspace}, poll={args.poll_interval}s)")

    while True:
        try:
            # PAT-0005: refresh policy per dispatch cycle (not startup-only)
            model_policy = read_model_policy(planspace)

            output = _db_cmd(db_path, "next-task")
            task = parse_next_task(output)

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
        except Exception as e:
            log(f"ERROR in dispatch loop: {e}")
            if args.once:
                sys.exit(1)
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
