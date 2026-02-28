import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    sections_dir = planspace / "artifacts" / "sections"
    if sections_dir.exists():
        for f in sections_dir.glob("section-*-proposal-excerpt.md"):
            f.unlink(missing_ok=True)
        for f in sections_dir.glob("section-*-alignment-excerpt.md"):
            f.unlink(missing_ok=True)


def _section_inputs_hash(
    sec_num: str, planspace: Path, codespace: Path,
    sections_by_num: dict[str, Any],
) -> str:
    """Compute a hash of a section's alignment-relevant inputs.

    Includes: proposal excerpt, alignment excerpt, related files list,
    consequence notes targeting this section, and tool registry digest.
    Used for targeted requeue (only requeue sections whose inputs
    actually changed) and incremental Phase 2 alignment checks.
    """
    hasher = hashlib.sha256()
    artifacts = planspace / "artifacts"

    # Excerpt files
    for suffix in ("proposal-excerpt.md", "alignment-excerpt.md"):
        p = artifacts / "sections" / f"section-{sec_num}-{suffix}"
        if p.exists():
            hasher.update(p.read_bytes())

    # Related files list (sorted for stability)
    section = sections_by_num.get(sec_num)
    if section and section.related_files:
        hasher.update(
            "\n".join(sorted(section.related_files)).encode("utf-8"))

    # Consequence notes targeting this section
    notes_dir = artifacts / "notes"
    if notes_dir.exists():
        for note in sorted(notes_dir.glob(f"from-*-to-{sec_num}.md")):
            hasher.update(note.read_bytes())

    # Tool registry digest (if exists)
    tools_path = artifacts / "tool-registry.json"
    if tools_path.exists():
        hasher.update(tools_path.read_bytes())

    # Section spec file
    spec_path = artifacts / "sections" / f"section-{sec_num}.md"
    if spec_path.exists():
        hasher.update(spec_path.read_bytes())

    # Decisions file
    decisions_path = artifacts / "decisions" / f"section-{sec_num}.md"
    if decisions_path.exists():
        hasher.update(decisions_path.read_bytes())

    # Integration proposal
    integration_path = (artifacts / "proposals"
                        / f"section-{sec_num}-integration-proposal.md")
    if integration_path.exists():
        hasher.update(integration_path.read_bytes())

    # Microstrategy: canonical artifact (actual content)
    microstrategy_path = (artifacts / "proposals"
                          / f"section-{sec_num}-microstrategy.md")
    if microstrategy_path.exists():
        hasher.update(microstrategy_path.read_bytes())

    # Microstrategy prompt/output logs (secondary)
    for ms_path in sorted(artifacts.glob(f"microstrategy-{sec_num}*.md")):
        hasher.update(ms_path.read_bytes())

    # TODO extraction
    todos_path = artifacts / "todos" / f"section-{sec_num}-todos.md"
    if todos_path.exists():
        hasher.update(todos_path.read_bytes())

    # Codemap digest (influences proposal generation)
    codemap_path = artifacts / "codemap.md"
    if codemap_path.exists():
        hasher.update(codemap_path.read_bytes())

    # Codemap corrections — authoritative fixes that override codemap.md
    corrections_path = artifacts / "signals" / "codemap-corrections.json"
    if corrections_path.exists():
        hasher.update(corrections_path.read_bytes())

    # Mode files — greenfield/brownfield affects prompt context
    for mode_file in (
        artifacts / "project-mode.txt",
        artifacts / "signals" / "project-mode.json",
        artifacts / "sections" / f"section-{sec_num}-mode.txt",
    ):
        if mode_file.exists():
            hasher.update(mode_file.read_bytes())

    # Problem frame — alignment-relevant summary artifact (R33/P11)
    problem_frame_path = (
        artifacts / "sections" / f"section-{sec_num}-problem-frame.md"
    )
    if problem_frame_path.exists():
        hasher.update(problem_frame_path.read_bytes())

    # Intent layer artifacts — trigger re-proposal when definitions evolve
    intent_global_philosophy = artifacts / "intent" / "global" / "philosophy.md"
    if intent_global_philosophy.exists():
        hasher.update(intent_global_philosophy.read_bytes())
    # V7/R67: Hash source manifest so changes to upstream philosophy
    # sources trigger re-proposal even when distilled artifact is cached
    intent_global_manifest = (
        artifacts / "intent" / "global" / "philosophy-source-manifest.json")
    if intent_global_manifest.exists():
        hasher.update(intent_global_manifest.read_bytes())
    # V2/R69: Hash source map — philosophy mutations must update
    # provenance, and provenance changes are alignment-relevant
    intent_global_source_map = (
        artifacts / "intent" / "global" / "philosophy-source-map.json")
    if intent_global_source_map.exists():
        hasher.update(intent_global_source_map.read_bytes())
    intent_sec_dir = artifacts / "intent" / "sections" / f"section-{sec_num}"
    for intent_file in (
        intent_sec_dir / "problem.md",
        intent_sec_dir / "problem-alignment.md",
        intent_sec_dir / "philosophy-excerpt.md",
    ):
        if intent_file.exists():
            hasher.update(intent_file.read_bytes())

    # Input refs — contract deltas and other registered inputs
    inputs_dir = artifacts / "inputs" / f"section-{sec_num}"
    if inputs_dir.exists():
        for ref_path in sorted(inputs_dir.glob("*.ref")):
            hasher.update(ref_path.read_bytes())
            # Also hash the referenced file itself (not just the pointer)
            try:
                referenced = Path(ref_path.read_text(encoding="utf-8").strip())
                if referenced.exists():
                    hasher.update(referenced.read_bytes())
            except (OSError, ValueError) as exc:
                # V3/R57: Use stable error marker so convergence cannot
                # be falsely reported when refs are unreadable.
                hasher.update(
                    f"REF_READ_ERROR:{ref_path}".encode("utf-8"))
                print(
                    f"[HASH][WARN] Failed to read ref "
                    f"{ref_path}: {exc}",
                )

    return hasher.hexdigest()


def _set_alignment_changed_flag(planspace: Path) -> None:
    """Write flag file so the main loop knows to requeue sections."""
    flag = planspace / "artifacts" / "alignment-changed-pending"
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
    return (planspace / "artifacts" / "alignment-changed-pending").exists()


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    """Check if alignment_changed flag is set. Clears it if so."""
    flag = planspace / "artifacts" / "alignment-changed-pending"
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
    hash_dir = planspace / "artifacts" / "section-inputs-hashes"
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
