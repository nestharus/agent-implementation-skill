"""Task-request ingestion — reads agent-emitted task-request files and dispatches.

Agents write task-request JSON files to paths like:
    artifacts/signals/task-requests-proposal-NN.json
    artifacts/signals/task-requests-impl-NN.json
    artifacts/signals/task-requests-micro-NN.json
    artifacts/signals/task-requests-reexplore-NN.json
    artifacts/signals/task-requests-coord-NN.json

This module closes the loop by reading those files and dispatching the
requested tasks through the standard dispatch pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .agent_templates import validate_dynamic_content
from .communication import log
from .dispatch import dispatch_agent, read_model_policy

# task_router lives alongside section_loop (in src/scripts/)
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from task_router import resolve_task  # noqa: E402


def ingest_task_requests(signal_path: Path) -> list[dict]:
    """Read and parse a task-request signal file.

    Supports both a single JSON object and JSONL (one object per line).
    Fail-closed: on JSONDecodeError, renames to .malformed.json + logs
    warning and returns empty list.  Entries missing ``task_type`` are
    skipped with a warning.  The signal file is deleted after a
    successful read to prevent re-processing.
    """
    if not signal_path.exists():
        return []

    raw = signal_path.read_text(encoding="utf-8").strip()
    if not raw:
        # Empty file — nothing to ingest, clean up
        signal_path.unlink(missing_ok=True)
        return []

    entries: list[dict] = []

    # Try single JSON object / array first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            entries = [parsed]
        elif isinstance(parsed, list):
            entries = [e for e in parsed if isinstance(e, dict)]
        else:
            log(f"  task_ingestion: WARNING — unexpected JSON type in "
                f"{signal_path}, treating as empty")
            signal_path.unlink(missing_ok=True)
            return []
    except json.JSONDecodeError:
        # Try JSONL (one JSON object per line)
        try:
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    entries.append(obj)
        except json.JSONDecodeError as exc:
            log(f"  task_ingestion: WARNING — malformed JSON in "
                f"{signal_path} ({exc}), renaming to .malformed.json")
            try:
                signal_path.rename(
                    signal_path.with_suffix(".malformed.json"))
            except OSError:
                pass
            return []

    # Validate: each entry must have task_type
    valid: list[dict] = []
    for entry in entries:
        if "task_type" not in entry:
            log(f"  task_ingestion: WARNING — skipping entry without "
                f"task_type: {entry!r}")
            continue
        valid.append(entry)

    # Delete signal file after successful read
    signal_path.unlink(missing_ok=True)
    return valid


def dispatch_ingested_tasks(
    planspace: Path,
    tasks: list[dict],
    section_number: str,
    parent_queue: str,
    codespace: Path | None = None,
) -> list[str]:
    """Dispatch ingested task requests through the standard pipeline.

    For each task dict, resolves agent_file + model via task_router,
    writes a task prompt file, and dispatches through dispatch_agent().
    Returns list of output strings.
    """
    if not tasks:
        return []

    artifacts = planspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    model_policy = read_model_policy(planspace)
    outputs: list[str] = []

    for task in tasks:
        task_type = task["task_type"]

        try:
            agent_file, model = resolve_task(task_type, model_policy)
        except ValueError as exc:
            log(f"  task_ingestion: WARNING — unknown task type "
                f"{task_type!r}, skipping ({exc})")
            continue

        # Write a task prompt from the payload or task metadata
        prompt_path = (
            artifacts / f"task-{task_type}-{section_number}-prompt.md"
        )
        payload_path = task.get("payload_path")
        if payload_path and Path(payload_path).exists():
            # Use the agent-provided payload as the prompt
            prompt_path = Path(payload_path)
        else:
            # Generate a minimal prompt from task metadata
            scope = task.get("concern_scope", section_number)
            priority = task.get("priority", "normal")
            content = (
                f"# Task: {task_type}\n\n"
                f"## Scope\n{scope}\n\n"
                f"## Priority\n{priority}\n\n"
                f"## Context\n"
                f"Dispatched from section {section_number} task ingestion.\n"
            )
            violations = validate_dynamic_content(content)
            if violations:
                log(f"  task_ingestion: WARNING — raw prompt has "
                    f"template violations: {violations}")
            prompt_path.write_text(content, encoding="utf-8")

        output_path = (
            artifacts / f"task-{task_type}-{section_number}-output.md"
        )

        result = dispatch_agent(
            model, prompt_path, output_path,
            planspace, parent_queue,
            section_number=section_number,
            codespace=codespace,
            agent_file=agent_file,
        )
        outputs.append(result)

    return outputs


def ingest_and_dispatch(
    planspace: Path,
    signal_path: Path,
    section_number: str,
    parent_queue: str,
    codespace: Path | None = None,
) -> list[str]:
    """Convenience wrapper: ingest task requests then dispatch them.

    Reads task-request JSON from signal_path, dispatches each through
    the standard pipeline. Returns list of output strings.
    """
    tasks = ingest_task_requests(signal_path)
    if not tasks:
        return []
    return dispatch_ingested_tasks(
        planspace, tasks, section_number, parent_queue, codespace,
    )
