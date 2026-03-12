from pathlib import Path
from typing import Any

from staleness.service.change_tracker import (
    check_pending as alignment_changed_pending_flag,
    invalidate_excerpts as invalidate_all_excerpts,
    make_alignment_checker,
    set_flag as set_alignment_changed_flag,
)
from signals.service.message_poller import (
    check_for_messages as drain_messages,
    handle_pending_messages as handle_messages,
    poll_control_messages as poll_messages,
)
from orchestrator.path_registry import PathRegistry
from orchestrator.service.pipeline_state import (
    check_pipeline_state as query_pipeline_state,
    pause_for_parent as wait_for_parent,
    wait_if_paused as block_if_paused,
)
from staleness.service.input_hasher import (
    coordination_recheck_hash,
    section_inputs_hash,
)

from containers import Services
from signals.service.communication import (
    AGENT_NAME,
    DB_SH,
)

_section_inputs_hash = section_inputs_hash


def check_pipeline_state(planspace: Path) -> str:
    return query_pipeline_state(planspace, db_sh=DB_SH)


def _invalidate_excerpts(planspace: Path) -> None:
    invalidate_all_excerpts(planspace)


def _set_alignment_changed_flag(planspace: Path) -> None:
    """Write flag file so the main loop knows to requeue sections."""
    set_alignment_changed_flag(
        planspace,
        db_sh=DB_SH,
        agent_name=AGENT_NAME,
    )


def alignment_changed_pending(planspace: Path) -> bool:
    """Check if alignment_changed flag is set (non-clearing)."""
    return alignment_changed_pending_flag(planspace)


_check_and_clear_alignment_changed = make_alignment_checker(DB_SH, AGENT_NAME)


def requeue_changed_sections(
    completed: set[str], queue: list[str],
    sections_by_num: dict[str, Any], planspace: Path,
    codespace: Path, *, current_section: str | None = None,
) -> list[str]:
    """Targeted requeue: only requeue completed sections whose inputs changed.

    Compares current input hashes against persisted baselines in
    ``artifacts/section-inputs-hashes/``. Returns the list of section
    numbers that were actually requeued. Always re-adds *current_section*
    to the front of the queue (it was interrupted mid-flight).
    """
    paths = PathRegistry(planspace)
    hash_dir = paths.section_inputs_hashes_dir()
    hash_dir.mkdir(parents=True, exist_ok=True)
    requeued: list[str] = []
    for done_num in list(completed):
        cur = _section_inputs_hash(
            done_num, planspace, codespace, sections_by_num)
        prev_file = hash_dir / f"{done_num}.hash"
        prev = (prev_file.read_text(encoding="utf-8").strip()
                if prev_file.exists() else "")
        if cur != prev:
            completed.discard(done_num)
            if done_num not in queue:
                queue.append(done_num)
            requeued.append(done_num)
            prev_file.write_text(cur, encoding="utf-8")
    if current_section and current_section not in queue:
        queue.insert(0, current_section)
    if requeued:
        Services.logger().log("Alignment changed — requeuing sections "
            f"with changed inputs: {requeued}")
    else:
        Services.logger().log("Alignment changed but no section inputs "
            "differ — skipping requeue")
    return requeued


def wait_if_paused(planspace: Path, parent: str) -> None:
    """Block if pipeline is paused. Polls until state returns to running.

    Buffers non-abort messages in memory while paused and replays them
    after resume (avoids the re-send-to-self infinite loop).
    """
    block_if_paused(
        planspace,
        parent,
        db_sh=DB_SH,
        agent_name=AGENT_NAME,
        logger=Services.logger().log,
    )


def pause_for_parent(planspace: Path, parent: str, signal: str) -> str:
    """Send a pause signal to parent and block until we get a response."""
    return wait_for_parent(
        planspace,
        parent,
        signal,
        db_sh=DB_SH,
        agent_name=AGENT_NAME,
        logger=Services.logger().log,
    )


def poll_control_messages(
    planspace: Path, parent: str,
    current_section: str | None = None,
) -> str | None:
    """Non-blocking poll for abort / alignment_changed control messages.

    Drains the section-loop mailbox and processes control messages:
    - abort: sends fail:aborted (with section if known), cleans up, exits.
    - alignment_changed: invalidates excerpts, sets flag, returns
      "alignment_changed" so the caller can restart.

    Returns "alignment_changed" if the flag was set, None otherwise.
    Non-control messages are re-queued to our own mailbox (replay).
    """
    return poll_messages(
        planspace,
        parent,
        current_section,
        db_sh=DB_SH,
        agent_name=AGENT_NAME,
        logger=Services.logger().log,
    )


def check_for_messages(planspace: Path) -> list[str]:
    """Non-blocking check for any pending messages."""
    return drain_messages(
        planspace,
        db_sh=DB_SH,
        agent_name=AGENT_NAME,
        logger=Services.logger().log,
    )


def handle_pending_messages(planspace: Path, queue: list[str],
                            completed: set[str]) -> bool:
    """Process any pending messages. Returns True if should abort."""
    return handle_messages(
        planspace,
        queue,
        completed,
        db_sh=DB_SH,
        agent_name=AGENT_NAME,
        logger=Services.logger().log,
    )
