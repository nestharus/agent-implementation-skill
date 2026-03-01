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
import subprocess
import sys
import time
from pathlib import Path

# Resolve paths relative to this script's location.
SCRIPTS_DIR = Path(__file__).resolve().parent
WORKFLOW_HOME = Path(os.environ.get("WORKFLOW_HOME", SCRIPTS_DIR.parent))
DB_SH = SCRIPTS_DIR / "db.sh"

# Import task_router from the same directory.
sys.path.insert(0, str(SCRIPTS_DIR))
from task_router import resolve_task  # noqa: E402

from section_loop.agent_templates import (  # noqa: E402
    render_template,
    validate_dynamic_content,
)
from section_loop.dispatch import dispatch_agent, read_model_policy  # noqa: E402

DISPATCHER_NAME = "task-dispatcher"


def _db(db_path: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a db.sh subcommand and return the result."""
    cmd = ["bash", str(DB_SH), *args, db_path] if args[0] == "init" else ["bash", str(DB_SH), args[0], db_path, *args[1:]]
    # db.sh expects: db.sh <command> <db> [args...]
    # So: bash db.sh <command> <db_path> [remaining args]
    return subprocess.run(  # noqa: S603, S607
        cmd, capture_output=True, text=True, timeout=30,
    )


def _db_cmd(db_path: str, command: str, *args: str) -> str:
    """Run a db.sh subcommand, return stdout. Raises on failure."""
    cmd = ["bash", str(DB_SH), command, db_path, *args]
    result = subprocess.run(  # noqa: S603, S607
        cmd, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"db.sh {command} failed (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def parse_next_task(output: str) -> dict[str, str] | None:
    """Parse the pipe-separated output of next-task into a dict.

    Returns None if no runnable tasks.
    Output format: id=N | type=T | by=B | prio=P [| problem=X] [| ...]
    """
    output = output.strip()
    if output == "NO_RUNNABLE_TASKS":
        return None

    result = {}
    for part in output.split(" | "):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result if "id" in result else None


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
    _record_task_routing(db_path, task_id, agent_file, model)

    log(f"Dispatching task {task_id}: {task_type} -> {agent_file} ({model})")

    # Build the prompt path. If payload_path is provided, use it.
    # Otherwise, create a minimal dispatch prompt from the task metadata.
    artifacts_dir = planspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if payload_path:
        prompt_path = Path(payload_path)
        if not prompt_path.is_absolute():
            prompt_path = planspace / prompt_path
    else:
        prompt_path = artifacts_dir / f"task-{task_id}-prompt.md"
        _write_task_prompt(prompt_path, task)
        # S5: validate dynamically generated prompt content
        violations = validate_dynamic_content(
            prompt_path.read_text(encoding="utf-8"),
        )
        if violations:
            log(f"WARNING: Dynamic prompt violations for task {task_id}: "
                f"{violations}")

    output_path = artifacts_dir / f"task-{task_id}-output.md"

    # V6: Dispatch through section_loop.dispatch for pause/alignment
    # handling, context sidecars, and per-dispatch monitoring.
    output = dispatch_agent(
        model, prompt_path, output_path,
        planspace, None,  # parent=None outside section-loop context
        section_number=None,
        codespace=codespace,
        agent_file=agent_file,
    )

    if output.startswith("TIMEOUT:"):
        log(f"Task {task_id} timed out")
        _db_cmd(
            db_path, "fail-task", task_id,
            "--error", "Agent timeout (600s)",
        )
        _notify(
            db_path, submitted_by, task_id, task_type, "failed",
            "Agent timeout (600s)",
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


def _write_task_prompt(prompt_path: Path, task: dict[str, str]) -> None:
    """Write a minimal prompt from task metadata when no payload exists.

    Dynamic prompts are wrapped through the agent template system to
    enforce immutable system constraints (S5).
    """
    lines = [f"# Task: {task['type']}\n"]
    if task.get("problem"):
        lines.append(f"Problem: {task['problem']}\n")
    if task.get("scope"):
        lines.append(f"Scope: {task['scope']}\n")
    lines.append(
        "\nComplete this task and write your output. "
        "Refer to task metadata above for context.\n"
    )
    dynamic_body = "\n".join(lines)
    prompt_path.write_text(
        render_template(f"task-dispatch:{task['type']}", dynamic_body),
        encoding="utf-8",
    )


def _record_task_routing(
    db_path: str, task_id: str, agent_file: str, model: str,
) -> None:
    """Update the task row with the resolved agent_file and model."""
    import sqlite3

    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET agent_file=?, model=? WHERE id=?",
        (agent_file, model, int(task_id)),
    )
    conn.commit()
    conn.close()


def _notify(
    db_path: str,
    target: str,
    task_id: str,
    task_type: str,
    status: str,
    detail: str,
) -> None:
    """Send a mailbox notification to the task submitter."""
    body = f"task:{status}:{task_id}:{task_type}:{detail}"
    try:
        _db_cmd(
            db_path, "send", target, "--from", DISPATCHER_NAME, body,
        )
    except RuntimeError:
        # Non-critical — submitter may not have a mailbox.
        pass


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
    db_path = str(planspace / "run.db")

    if not Path(db_path).exists():
        log(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    # V6: Load model policy once at startup for policy-driven dispatch
    model_policy = read_model_policy(planspace)

    log(f"Starting dispatcher (planspace={planspace}, poll={args.poll_interval}s)")

    while True:
        try:
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
