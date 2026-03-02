"""Task-request ingestion — reads agent-emitted task-request files and submits.

Agents write task-request JSON files to paths like:
    artifacts/signals/task-requests-proposal-NN.json
    artifacts/signals/task-requests-impl-NN.json
    artifacts/signals/task-requests-micro-NN.json
    artifacts/signals/task-requests-reexplore-NN.json
    artifacts/signals/task-requests-coord-NN.json

This module closes the loop by reading those files and submitting the
requested tasks into the queue with flow metadata.  The task_dispatcher.py
poll loop handles actual dispatch.

Supports both legacy (v1) single-task JSON and v2 flow declarations.
Legacy requests are submitted as single-step chains; v2 declarations
are fully processed (chains and fanouts).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .agent_templates import validate_dynamic_content
from .communication import log
from .dispatch import dispatch_agent, read_model_policy

# task_router and flow_schema live alongside section_loop (in src/scripts/)
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from flow_schema import (  # noqa: E402
    ChainAction,
    FanoutAction,
    FlowDeclaration,
    parse_flow_signal,
    validate_flow_declaration,
)
from task_flow import submit_chain, submit_fanout  # noqa: E402
from task_router import resolve_task  # noqa: E402


def _parse_signal_file(signal_path: Path) -> FlowDeclaration | None:
    """Parse a task-request signal file into a FlowDeclaration.

    Returns None if the file is missing, empty, or malformed.
    On parse errors, renames to .malformed.json for diagnosis.
    On success, deletes the signal file to prevent re-processing.
    """
    if not signal_path.exists():
        return None

    raw = signal_path.read_text(encoding="utf-8").strip()
    if not raw:
        signal_path.unlink(missing_ok=True)
        return None

    try:
        decl = parse_flow_signal(signal_path)
    except ValueError as exc:
        log(f"  task_ingestion: WARNING — malformed signal in "
            f"{signal_path} ({exc}), renaming to .malformed.json")
        try:
            signal_path.rename(
                signal_path.with_suffix(".malformed.json"))
        except OSError:
            pass
        return None

    # Validate v2 declarations
    if decl.version >= 2:
        errors = validate_flow_declaration(decl)
        if errors:
            log(f"  task_ingestion: WARNING — v2 flow declaration in "
                f"{signal_path} has validation errors: {errors}")
            try:
                signal_path.rename(
                    signal_path.with_suffix(".malformed.json"))
            except OSError:
                pass
            return None

    signal_path.unlink(missing_ok=True)
    return decl


def ingest_task_requests(signal_path: Path) -> list[dict]:
    """Read and parse a task-request signal file.

    Supports both a single JSON object and JSONL (one object per line),
    as well as v2 flow envelopes.  Parsing is delegated to
    ``flow_schema.parse_flow_signal`` which normalizes all formats
    into a ``FlowDeclaration``.

    For legacy (v1) declarations the extracted task dicts are returned
    for dispatch.  For v2 declarations, validation is performed and a
    warning is logged — dispatch is not yet supported (Task 6).

    Fail-closed: on parse errors, renames to .malformed.json + logs
    warning and returns empty list.  Entries missing ``task_type`` are
    skipped with a warning.  The signal file is deleted after a
    successful read to prevent re-processing.

    .. deprecated::
        Use :func:`ingest_and_submit` instead, which submits tasks into
        the queue with flow metadata rather than returning raw dicts.
    """
    decl = _parse_signal_file(signal_path)
    if decl is None:
        return []

    # Legacy v1 only — v2 declarations are handled by ingest_and_submit
    if decl.version >= 2:
        log(f"  task_ingestion: WARNING — v2 flow actions should use "
            f"ingest_and_submit, skipping")
        return []

    # --- Legacy (v1): extract task dicts from chain steps ---
    entries = _extract_legacy_tasks(decl)

    # Validate: each entry must have task_type
    valid: list[dict] = []
    for entry in entries:
        if "task_type" not in entry:
            log(f"  task_ingestion: WARNING — skipping entry without "
                f"task_type: {entry!r}")
            continue
        valid.append(entry)

    return valid


def _extract_legacy_tasks(decl: FlowDeclaration) -> list[dict]:
    """Extract flat task dicts from a legacy (v1) FlowDeclaration.

    Legacy declarations are normalized into a single ChainAction whose
    steps map 1:1 to the original task dicts.
    """
    from flow_schema import ChainAction  # noqa: E402 — deferred to avoid circular

    tasks: list[dict] = []
    for action in decl.actions:
        if isinstance(action, ChainAction):
            for step in action.steps:
                task: dict = {"task_type": step.task_type}
                if step.concern_scope:
                    task["concern_scope"] = step.concern_scope
                if step.payload_path:
                    task["payload_path"] = step.payload_path
                if step.priority and step.priority != "normal":
                    task["priority"] = step.priority
                if step.problem_id:
                    task["problem_id"] = step.problem_id
                tasks.append(task)
    return tasks


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
        if payload_path:
            # Resolve relative paths against planspace (V5/R77)
            resolved = Path(payload_path)
            if not resolved.is_absolute():
                resolved = planspace / resolved
            if resolved.exists():
                # Validate agent-provided payload prompt (V3/R77)
                payload_content = resolved.read_text(encoding="utf-8")
                violations = validate_dynamic_content(payload_content)
                if violations:
                    log(f"  task_ingestion: ERROR — payload prompt "
                        f"{resolved} blocked — template violations: "
                        f"{violations}")
                    continue
                prompt_path = resolved
            else:
                # Payload declared but missing — fail closed (V5/R77)
                log(f"  task_ingestion: ERROR — payload declared but "
                    f"not found: {resolved} — skipping task")
                continue
        else:
            # No payload supplied — generate a minimal prompt
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
                log(f"  task_ingestion: ERROR — generated prompt "
                    f"blocked — template violations: {violations}")
                continue
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
    """Legacy wrapper: ingest task requests then dispatch them directly.

    .. deprecated::
        Use :func:`ingest_and_submit` instead.  This function dispatches
        tasks immediately rather than submitting them to the queue with
        flow metadata.

    Reads task-request JSON from signal_path, dispatches each through
    the standard pipeline. Returns list of output strings.
    """
    tasks = ingest_task_requests(signal_path)
    if not tasks:
        return []
    return dispatch_ingested_tasks(
        planspace, tasks, section_number, parent_queue, codespace,
    )


def ingest_and_submit(
    planspace: Path,
    db_path: Path,
    submitted_by: str,
    signal_path: Path,
    *,
    flow_id: str | None = None,
    chain_id: str | None = None,
    declared_by_task_id: int | None = None,
    origin_refs: list[str] | None = None,
) -> list[int]:
    """Submit agent-emitted task requests into the queue with flow metadata.

    Reads task-request JSON files, parses them via ``parse_flow_signal``,
    and submits them through ``submit_chain``/``submit_fanout`` from
    task_flow.py.  The task_dispatcher.py poll loop handles actual dispatch.

    For legacy v1 tasks: each is submitted as a single-step chain.
    For v2 declarations: chain/fanout actions are fully processed.

    Flow metadata (flow_id, chain_id, origin_refs) is propagated from
    the calling context so submitted tasks carry provenance.

    Returns list of submitted task IDs.
    """
    decl = _parse_signal_file(signal_path)
    if decl is None:
        return []

    all_task_ids: list[int] = []
    refs = origin_refs or []

    for action in decl.actions:
        if isinstance(action, ChainAction):
            if not action.steps:
                continue
            task_ids = submit_chain(
                db_path,
                submitted_by,
                action.steps,
                flow_id=flow_id,
                chain_id=chain_id,
                declared_by_task_id=declared_by_task_id,
                origin_refs=refs,
                planspace=planspace,
            )
            all_task_ids.extend(task_ids)
        elif isinstance(action, FanoutAction):
            if not action.branches:
                continue
            # Fanout requires a flow_id — allocate one if not provided
            fanout_flow_id = flow_id
            if not fanout_flow_id:
                from task_flow import _new_flow_id  # noqa: E402
                fanout_flow_id = _new_flow_id()
            submit_fanout(
                db_path,
                submitted_by,
                action.branches,
                flow_id=fanout_flow_id,
                declared_by_task_id=declared_by_task_id,
                origin_refs=refs,
                gate=action.gate,
                planspace=planspace,
            )
            # Fanout returns gate_id not task_ids; the individual
            # branch task_ids are not directly returned here but are
            # in the DB for the dispatcher to find.
        else:
            log(f"  task_ingestion: WARNING — unknown action type "
                f"{type(action).__name__}, skipping")

    if all_task_ids:
        log(f"  task_ingestion: submitted {len(all_task_ids)} tasks "
            f"to queue (submitted_by={submitted_by})")

    return all_task_ids
