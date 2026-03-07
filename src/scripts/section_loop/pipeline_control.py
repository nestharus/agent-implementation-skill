import subprocess
import sys
from pathlib import Path
from typing import Any

from lib.path_registry import PathRegistry
from lib.section_input_hasher import (
    coordination_recheck_hash,
    section_inputs_hash,
)

from .communication import (
    AGENT_NAME,
    DB_SH,
    log,
    mailbox_cleanup,
    mailbox_drain,
    mailbox_recv,
    mailbox_send,
)


def check_pipeline_state(planspace: Path) -> str:
    """Query the latest pipeline-state lifecycle event. Returns 'running' if none."""
    result = subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "query", str(planspace / "run.db"),  # noqa: S607
         "lifecycle", "--tag", "pipeline-state", "--limit", "1"],
        capture_output=True, text=True,
    )
    # query returns id|ts|kind|tag|body|agent — body is the state value
    line = result.stdout.strip()
    if line:
        parts = line.split("|")
        if len(parts) >= 5 and parts[4]:
            return parts[4]
    return "running"


def _invalidate_excerpts(planspace: Path) -> None:
    """Delete all section excerpt files, forcing setup to rerun."""
    paths = PathRegistry(planspace)
    sections_dir = paths.sections_dir()
    if sections_dir.exists():
        for f in sections_dir.glob("section-*-proposal-excerpt.md"):
            f.unlink(missing_ok=True)
        for f in sections_dir.glob("section-*-alignment-excerpt.md"):
            f.unlink(missing_ok=True)

_section_inputs_hash = section_inputs_hash


def _set_alignment_changed_flag(planspace: Path) -> None:
    """Write flag file so the main loop knows to requeue sections."""
    paths = PathRegistry(planspace)
    flag = paths.alignment_changed_flag()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1", encoding="utf-8")
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
         "lifecycle", "alignment-changed", "pending",
         "--agent", AGENT_NAME],
        capture_output=True, text=True,
    )


def alignment_changed_pending(planspace: Path) -> bool:
    """Check if alignment_changed flag is set (non-clearing)."""
    return PathRegistry(planspace).alignment_changed_flag().exists()


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    """Check if alignment_changed flag is set. Clears it if so."""
    flag = PathRegistry(planspace).alignment_changed_flag()
    if flag.exists():
        flag.unlink(missing_ok=True)
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
             "lifecycle", "alignment-changed", "cleared",
             "--agent", AGENT_NAME],
            capture_output=True, text=True,
        )
        return True
    return False


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
        log("Alignment changed — requeuing sections "
            f"with changed inputs: {requeued}")
    else:
        log("Alignment changed but no section inputs "
            "differ — skipping requeue")
    return requeued


def wait_if_paused(planspace: Path, parent: str) -> None:
    """Block if pipeline is paused. Polls until state returns to running.

    Buffers non-abort messages in memory while paused and replays them
    after resume (avoids the re-send-to-self infinite loop).
    """
    state = check_pipeline_state(planspace)
    if state != "paused":
        return
    log("Pipeline paused — waiting for resume")
    mailbox_send(planspace, parent, "status:paused")
    buffered: list[str] = []
    while check_pipeline_state(planspace) == "paused":
        msg = mailbox_recv(planspace, timeout=5)
        if msg == "TIMEOUT":
            continue
        if msg.startswith("abort"):
            log("Received abort while paused — shutting down")
            mailbox_send(planspace, parent, "fail:aborted")
            mailbox_cleanup(planspace)
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            log("Alignment changed while paused — invalidating excerpts")
            _invalidate_excerpts(planspace)
            _set_alignment_changed_flag(planspace)
            continue
        buffered.append(msg)
    # Replay buffered messages after resume
    for msg in buffered:
        mailbox_send(planspace, AGENT_NAME, msg)
    log("Pipeline resumed")
    mailbox_send(planspace, parent, "status:resumed")


def pause_for_parent(planspace: Path, parent: str, signal: str) -> str:
    """Send a pause signal to parent and block until we get a response."""
    mailbox_send(planspace, parent, signal)
    while True:
        msg = mailbox_recv(planspace, timeout=0)
        if msg.startswith("abort"):
            log("Received abort — shutting down")
            mailbox_send(planspace, parent, "fail:aborted")
            mailbox_cleanup(planspace)
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            log("Alignment changed during pause — invalidating excerpts")
            _invalidate_excerpts(planspace)
            _set_alignment_changed_flag(planspace)
            continue
        return msg


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
    msgs = mailbox_drain(planspace)
    alignment_changed = False
    for msg in msgs:
        if msg.startswith("abort"):
            if current_section:
                mailbox_send(planspace, parent,
                             f"fail:{current_section}:aborted")
            else:
                mailbox_send(planspace, parent, "fail:aborted")
            log("Received abort — shutting down")
            mailbox_cleanup(planspace)
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            log("Alignment changed — invalidating excerpts and setting flag")
            _invalidate_excerpts(planspace)
            _set_alignment_changed_flag(planspace)
            alignment_changed = True
        else:
            # Replay non-control messages back to our mailbox
            mailbox_send(planspace, AGENT_NAME, msg)
    if alignment_changed:
        return "alignment_changed"
    return None


def check_for_messages(planspace: Path) -> list[str]:
    """Non-blocking check for any pending messages."""
    return mailbox_drain(planspace)


def handle_pending_messages(planspace: Path, queue: list[str],
                            completed: set[str]) -> bool:
    """Process any pending messages. Returns True if should abort."""
    for msg in check_for_messages(planspace):
        if msg.startswith("abort"):
            return True
        if msg.startswith("alignment_changed"):
            log("Alignment changed — invalidating excerpts and setting flag")
            _invalidate_excerpts(planspace)
            _set_alignment_changed_flag(planspace)
            # Targeted requeue handled by _check_and_clear_alignment_changed
            # in main loop (uses _section_inputs_hash for selective requeue)
    return False
