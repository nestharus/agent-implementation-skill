#!/usr/bin/env python3
"""Section loop orchestrator for the implementation pipeline.

Manages the per-section execution cycle using a strategic, agent-driven flow:

Phase 1 — Initial pass (per-section):
  1. Section setup — extract proposal/alignment excerpts from global docs
  2. Integration proposal loop — GPT proposes how to wire the proposal into
     the codebase, Opus checks alignment, iterate until aligned
  3. Strategic implementation — GPT implements holistically with sub-agents,
     Opus checks alignment, iterate until aligned

Phase 2 — Global coordination:
  4. Re-check alignment across ALL sections (cross-section changes may have
     introduced problems invisible during the per-section pass)
  5. Global coordinator collects outstanding problems, dispatches Opus
     coordination-planner agent to group and strategize, then executes
     the plan with Codex fix agents
  6. Re-run per-section alignment to verify fixes
  7. Repeat steps 5-6 until all sections ALIGNED or max rounds reached

Agents are strategic throughout: GPT dispatches sub-agents (GLM for cheap
exploration, Codex for targeted implementation), reasons about problems,
and proposes integration strategies. Opus checks shape and direction — not
tiny details.

All agent dispatches run as background subprocesses. The script communicates
with its parent (the orchestrator or interactive session) via mailbox
messages. When paused (waiting for user input, research results, etc.),
the script blocks on its own mailbox recv until the parent sends a resume.

Mail protocol (sent TO parent mailbox):
    summary:setup:<section>:<text>
    summary:proposal:<section>:<text>
    summary:proposal-align:<section>:<text>
    summary:impl:<section>:<text>
    summary:impl-align:<section>:<text>
    status:coordination:round-<N>
    done:<section>:<count> files modified
    fail:<section>:<error>
    fail:<section>:aborted
    fail:<section>:coordination_exhausted:<summary>
    fail:aborted                         (global abort, any time)
    complete                             (ONLY when all aligned)
    pause:underspec:<section>:<description>
    pause:need_decision:<section>:<question>
    pause:dependency:<section>:<needed_section>
    pause:loop_detected:<section>:<detail>

Mail protocol (received FROM parent mailbox):
    resume:<payload>          — continue with answer/result
    abort                     — clean shutdown
    alignment_changed         — user input changed alignment, re-evaluate

Pipeline state (lifecycle events in run.db, tag=pipeline-state):
    running   — normal execution (default if no event)
    paused    — finish current agent, then wait
    Checked between each agent dispatch. Monitor or orchestrator writes
    lifecycle events via db.sh log to control the pipeline.

Multi-tier monitoring:
    - Per-section agents (setup, proposal, implementation) get mail
      instructions + a per-agent GLM monitor
    - Agents narrate actions via mailbox (plan:/done:/LOOP_DETECTED:)
    - Per-agent monitors detect loops within a single agent dispatch
    - Task-level monitor detects cycles across sections (alignment stuck,
      rescheduling cycles)
    - Opus alignment checks are dispatched WITHOUT a per-agent monitor
      (alignment prompts have no narration instructions; a monitor would
      false-positive STALLED after 5 minutes of expected silence)
    - Coordinator fix agents are dispatched WITHOUT per-agent monitors
      (fix prompts use strategic GLM sub-agents internally for validation;
      the task-level monitor detects cross-section stuck states)

Usage:
    section-loop.py <planspace> <codespace> --global-proposal <path>
                    --global-alignment <path> [--parent <name>]

Requires:
    - Section files in <planspace>/artifacts/sections/section-*.md
    - Each section file has ## Related Files with ### <filepath> entries
    - Global proposal and alignment documents
    - uv run agents available for dispatching models
    - db.sh available at $WORKFLOW_HOME/scripts/db.sh
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKFLOW_HOME = Path(os.environ.get(
    "WORKFLOW_HOME",
    Path(__file__).resolve().parent.parent,
))
DB_SH = WORKFLOW_HOME / "scripts" / "db.sh"
AGENT_NAME = "section-loop"
# Coordination round limits: hard cap to prevent runaway, but rounds
# continue adaptively while problem count decreases.
MAX_COORDINATION_ROUNDS = 10  # hard safety cap
MIN_COORDINATION_ROUNDS = 2   # always try at least this many


@dataclass
class Section:
    """A single section with its metadata and execution state."""

    number: str  # e.g., "01"
    path: Path
    global_proposal_path: Path = field(default_factory=Path)
    global_alignment_path: Path = field(default_factory=Path)
    related_files: list[str] = field(default_factory=list)
    solve_count: int = 0


def log(msg: str) -> None:
    """Print a timestamped log message to stdout."""
    print(f"[section-loop] {msg}", flush=True)




def _summary_tag(message: str) -> str:
    """Extract a structured tag from a summary-worthy message.

    Maps message prefixes to queryable tags for db.sh log events::

      summary:proposal-align:03:PROBLEMS → proposal-align:03
      status:coordination:round-2        → coordination:round-2
      done:03:5 files modified           → done:03
      fail:03:error                      → fail:03
      complete                           → complete
      pause:underspec:03:detail          → underspec:03
    """
    parts = message.split(":")
    if message.startswith("summary:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message.startswith("status:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message.startswith(("done:", "fail:")) and len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    if message.startswith("pause:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message == "complete":
        return "complete"
    return parts[0]


def mailbox_send(planspace: Path, target: str, message: str) -> None:
    """Send a message to a target mailbox.

    Summary-worthy messages (summary, done, complete, status, fail, pause
    prefixes) are also recorded as summary events in the database so that
    monitors can query them via ``db.sh tail``/``db.sh query``.
    """
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "send", str(planspace / "run.db"),  # noqa: S607
         target, "--from", AGENT_NAME, message],
        check=True,
        capture_output=True,
        text=True,
    )
    log(f"  mail → {target}: {message[:80]}")
    # Record summary event for messages that monitors track
    for prefix in ("summary:", "done:", "complete", "status:", "fail:",
                   "pause:"):
        if message.startswith(prefix):
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
                 "summary", _summary_tag(message), message,
                 "--agent", AGENT_NAME],
                capture_output=True,
                text=True,
            )
            break


def mailbox_recv(planspace: Path, timeout: int = 0) -> str:
    """Block until a message arrives in our mailbox. Returns message text."""
    log(f"  mail ← waiting (timeout={timeout})...")
    result = subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "recv", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME, str(timeout)],
        capture_output=True,
        text=True,
    )
    msg = result.stdout.strip()
    if result.returncode != 0 or msg == "TIMEOUT":
        return "TIMEOUT"
    log(f"  mail ← received: {msg[:80]}")
    return msg


def mailbox_drain(planspace: Path) -> list[str]:
    """Read all pending messages without blocking."""
    result = subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "drain", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        capture_output=True,
        text=True,
    )
    msgs = []
    # Split on line-delimited separator (matches db.sh's "---" on its
    # own line).  Using regex to avoid misparse when message body contains
    # the literal string "---".
    for chunk in re.split(r'\n---\n', result.stdout):
        chunk = chunk.strip()
        if chunk:
            msgs.append(chunk)
    return msgs


def mailbox_register(planspace: Path) -> None:
    """Register this agent for receiving messages."""
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "register", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        check=True, capture_output=True, text=True,
    )


def mailbox_cleanup(planspace: Path) -> None:
    """Clean up and unregister this agent."""
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "cleanup", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        capture_output=True, text=True,
    )
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "unregister", str(planspace / "run.db"),  # noqa: S607
         AGENT_NAME],
        capture_output=True, text=True,
    )


def _log_artifact(planspace: Path, name: str) -> None:
    """Log an artifact lifecycle event to the database."""
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
         "lifecycle", f"artifact:{name}", "created",
         "--agent", AGENT_NAME],
        capture_output=True, text=True,
    )


def _record_traceability(
    planspace: Path,
    section: str,
    artifact: str,
    source: str,
    detail: str = "",
) -> None:
    """Append a traceability entry to artifacts/traceability.json.

    Records the provenance chain: section → excerpt → proposal →
    microstrategy → files/changes. Each entry captures what artifact
    was produced, from what source, and for which section.
    """
    trace_path = planspace / "artifacts" / "traceability.json"
    entries: list[dict] = []
    if trace_path.exists():
        try:
            entries = json.loads(trace_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            entries = []
    entries.append({
        "section": section,
        "artifact": artifact,
        "source": source,
        "detail": detail,
    })
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(entries, indent=2) + "\n", encoding="utf-8",
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
            # Requeue completed sections (works when real structures passed)
            for sec_num in list(completed):
                completed.discard(sec_num)
                if sec_num not in queue:
                    queue.append(sec_num)
    return False


# ---------------------------------------------------------------------------
# Section file parsing
# ---------------------------------------------------------------------------

def parse_related_files(section_path: Path) -> list[str]:
    """Extract file paths from ## Related Files / ### <path> entries.

    Only parses lines after the ``## Related Files`` header and skips
    content inside markdown code fences (``` blocks).
    """
    text = section_path.read_text(encoding="utf-8")
    # Only look at the content after ## Related Files header
    marker = "## Related Files"
    idx = text.find(marker)
    if idx == -1:
        return []
    tail = text[idx + len(marker):]
    # Strip code fences before matching
    tail = re.sub(r'```.*?```', '', tail, flags=re.DOTALL)
    return re.findall(r'^### (.+)$', tail, re.MULTILINE)


def load_sections(sections_dir: Path) -> list[Section]:
    """Load all section files and their related file maps.

    Only matches files named ``section-<number>.md`` (the actual spec
    files). Excerpt artifacts like ``section-01-proposal-excerpt.md`` are
    explicitly excluded so they are never mistaken for section specs.
    """
    sections = []
    for path in sorted(sections_dir.glob("section-*.md")):
        m = re.match(r'^section-(\d+)\.md$', path.name)
        if not m:
            continue
        related = parse_related_files(path)
        sections.append(Section(number=m.group(1), path=path,
                                related_files=related))
    return sections


def build_file_to_sections(sections: list[Section]) -> dict[str, list[str]]:
    """Map each file path to the section numbers that reference it."""
    mapping: dict[str, list[str]] = {}
    for sec in sections:
        for f in sec.related_files:
            mapping.setdefault(f, []).append(sec.number)
    return mapping


# ---------------------------------------------------------------------------
# Agent dispatch
# ---------------------------------------------------------------------------

def dispatch_agent(model: str, prompt_path: Path, output_path: Path,
                   planspace: Path | None = None,
                   parent: str | None = None,
                   agent_name: str | None = None,
                   codespace: Path | None = None,
                   section_number: str | None = None,
                   agent_file: str | None = None) -> str:
    """Run an agent via uv run agents and return the output text.

    If planspace and parent are provided, checks pipeline state before
    dispatching and waits if paused.

    If agent_name is provided, launches an agent-monitor alongside the
    agent to watch for loops and stuck states. The monitor is a GLM
    agent that reads the agent's mailbox.

    If codespace is provided, passes --project to the agent so it runs
    with the correct working directory and model config lookup.

    If agent_file is provided (basename like "alignment-judge.md"), the
    agent definition is prepended to the prompt via --agent-file. The
    agent file encodes the "method of thinking" for the role.
    """
    if planspace and parent:
        wait_if_paused(planspace, parent)
        # If alignment_changed was received during the pause (or was
        # already pending), do NOT launch the agent — excerpts are stale.
        if alignment_changed_pending(planspace):
            log("  dispatch_agent: alignment_changed pending — skipping")
            return "ALIGNMENT_CHANGED_PENDING"

    monitor_name = f"{agent_name}-monitor" if agent_name else None
    monitor_proc = None
    dispatch_start_id = None

    if planspace and agent_name:
        db_path = str(planspace / "run.db")
        # Register agent's mailbox (agents send narration here)
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "register",  # noqa: S607
             db_path, agent_name],
            check=True, capture_output=True, text=True,
        )
        # Record dispatch-start event and capture its ID for signal scoping
        start_result = subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "log", db_path,  # noqa: S607
             "lifecycle", f"dispatch:{agent_name}", "start",
             "--agent", AGENT_NAME],
            capture_output=True, text=True,
        )
        # Output format: "logged:<id>:lifecycle:dispatch:<agent-name>"
        start_out = start_result.stdout.strip()
        if start_out.startswith("logged:"):
            dispatch_start_id = start_out.split(":")[1]
        # Launch agent-monitor (GLM) in background
        assert monitor_name is not None  # narrowed by `if agent_name`  # noqa: S101
        monitor_prompt = _write_agent_monitor_prompt(
            planspace, agent_name, monitor_name,
        )
        monitor_proc = subprocess.Popen(  # noqa: S603
            ["uv", "run", "--frozen", "agents", "--agent-file",  # noqa: S607
             str(WORKFLOW_HOME / "agents" / "agent-monitor.md"),
             "--file", str(monitor_prompt)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log(f"  agent-monitor started (pid={monitor_proc.pid})")

    log(f"  dispatch {model} → {prompt_path.name}")
    # Emit per-section dispatch summary event for QA monitor rule C1
    if planspace and section_number:
        name_label = agent_name or model
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
             "summary", f"dispatch:{section_number}",
             f"{name_label} dispatched",
             "--agent", AGENT_NAME],
            capture_output=True, text=True,
        )
    cmd = ["uv", "run", "--frozen", "agents", "--model", model,
           "--file", str(prompt_path)]
    if agent_file:
        cmd.extend(["--agent-file",
                     str(WORKFLOW_HOME / "agents" / agent_file)])
    if codespace:
        cmd.extend(["--project", str(codespace)])
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            log(f"  WARNING: agent returned {result.returncode}")
    except subprocess.TimeoutExpired:
        output = "TIMEOUT: Agent exceeded 600s time limit"
        log("  WARNING: agent timed out after 600s")

    # Shut down agent-monitor after agent finishes
    if monitor_proc:
        assert monitor_name is not None  # set when monitor_proc created  # noqa: S101
        # Send stop signal to monitor
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "send", str(planspace / "run.db"),  # noqa: S607
             monitor_name, "--from", AGENT_NAME, "agent-finished"],
            capture_output=True, text=True,
        )
        try:
            monitor_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            monitor_proc.terminate()
        # Query DB for signal events from this dispatch
        if dispatch_start_id:
            sig_result = subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "query",  # noqa: S607
                 str(planspace / "run.db"), "signal",
                 "--tag", agent_name,
                 "--since", dispatch_start_id],
                capture_output=True, text=True,
            )
            for sig_line in sig_result.stdout.strip().splitlines():
                # Output format: id|ts|kind|tag|body|agent
                parts = sig_line.split("|")
                if len(parts) >= 5:
                    sig_body = parts[4]
                    if sig_body:
                        log(f"  SIGNAL from monitor: {sig_body[:100]}")
                        output += "\nLOOP_DETECTED: " + sig_body
                        # Re-log signal from section-loop for QA monitor rule A4
                        subprocess.run(  # noqa: S603
                            ["bash", str(DB_SH), "log",  # noqa: S607
                             str(planspace / "run.db"),
                             "signal", f"loop_detected:{agent_name}",
                             sig_body,
                             "--agent", AGENT_NAME],
                            capture_output=True, text=True,
                        )

    # Write output AFTER signal check (so the saved file includes
    # the LOOP_DETECTED line for forensic debugging)
    output_path.write_text(output, encoding="utf-8")
    if planspace is not None:
        _log_artifact(planspace, f"output:{output_path.stem}")

    if monitor_proc:
        assert monitor_name is not None  # set when monitor_proc created  # noqa: S101
        assert agent_name is not None  # noqa: S101  # monitor_proc only set when agent_name provided
        # Clean up agent
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "cleanup",  # noqa: S607
             str(planspace / "run.db"), agent_name],
            capture_output=True, text=True,
        )
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "unregister",  # noqa: S607
             str(planspace / "run.db"), agent_name],
            capture_output=True, text=True,
        )
        # Clean up monitor
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "cleanup",  # noqa: S607
             str(planspace / "run.db"), monitor_name],
            capture_output=True, text=True,
        )
        subprocess.run(  # noqa: S603
            ["bash", str(DB_SH), "unregister",  # noqa: S607
             str(planspace / "run.db"), monitor_name],
            capture_output=True, text=True,
        )

    return output


def _write_agent_monitor_prompt(
    planspace: Path, agent_name: str, monitor_name: str,
) -> Path:
    """Write the prompt file for a per-agent GLM monitor."""
    db_path = planspace / "run.db"
    prompt_path = planspace / "artifacts" / f"{monitor_name}-prompt.md"
    prompt_path.write_text(f"""# Agent Monitor: {agent_name}

## Your Job
Watch mailbox `{agent_name}` for messages from a running agent.
Detect if the agent is looping (repeating the same actions).
Report loops by logging signal events to the database.

## Setup
```bash
bash "{DB_SH}" register "{db_path}" {monitor_name}
```

## Paths
- Planspace: `{planspace}`
- Database: `{db_path}`
- Agent mailbox to watch: `{agent_name}`
- Your mailbox: `{monitor_name}`

## Monitor Loop
1. Drain all messages from `{agent_name}` mailbox
2. Track "plan:" messages in memory
3. If you see the same plan repeated (same action on same file) → loop detected
4. Check your own mailbox for `agent-finished` signal → exit
5. Wait 10 seconds, repeat

## Loop Detection
Keep a list of all `plan:` messages received. If a new `plan:` message
is substantially similar to a previous one (same file, same action),
the agent is looping.

**Agent self-reported loop:** If ANY drained message starts with
`LOOP_DETECTED:`, the agent has self-detected a loop. Immediately log
that payload as a signal event and exit — no further analysis needed.

When loop detected (either self-reported or by your analysis), log a
signal event:
```bash
bash "{DB_SH}" log "{db_path}" signal {agent_name} "LOOP_DETECTED:{agent_name}:<repeated action>" --agent {monitor_name}
```

Do NOT send loop signals via mailbox — only log signal events as above.

## Exit Conditions
- Receive `agent-finished` on your mailbox → exit normally
- 5 minutes with no messages from agent → log stalled warning, then exit:
  ```bash
  bash "{DB_SH}" log "{db_path}" signal {agent_name} "STALLED:{agent_name}:no messages for 5 minutes" --agent {monitor_name}
  ```
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:agent-monitor-{agent_name}")
    return prompt_path


def summarize_output(output: str, max_len: int = 200) -> str:
    """Extract a brief summary from agent output for status messages."""
    # Look for explicit summary lines first
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("summary:"):
            return stripped[len("summary:"):].strip()[:max_len]
    # Fall back to first non-empty, non-heading line
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:max_len]
    return "(no output)"


def read_agent_signal(signal_path: Path) -> tuple[str | None, str]:
    """Read a structured signal file written by an agent.

    Agents write JSON signal files when they need to pause the pipeline.
    Returns (signal_type, detail) or (None, "") if no signal file exists.
    The detail includes structured fields (needs, assumptions_refused,
    suggested_escalation_target) when available for richer context.
    """
    if not signal_path.exists():
        return None, ""
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
        state = data.get("state", "").lower()
        detail = data.get("detail", "")
        # Enrich detail with structured fields when present
        needs = data.get("needs", "")
        refused = data.get("assumptions_refused", "")
        target = data.get("suggested_escalation_target", "")
        extras = []
        if needs:
            extras.append(f"Needs: {needs}")
        if refused:
            extras.append(f"Refused assumptions: {refused}")
        if target:
            extras.append(f"Escalation target: {target}")
        if extras:
            detail = f"{detail} [{'; '.join(extras)}]"
        if state in ("underspec", "underspecified"):
            return "underspec", detail
        if state in ("need_decision",):
            return "need_decision", detail
        if state in ("dependency",):
            return "dependency", detail
        if state in ("loop_detected",):
            return "loop_detected", detail
        return None, ""
    except (json.JSONDecodeError, KeyError):
        return None, ""


def adjudicate_agent_output(
    output_path: Path, planspace: Path, parent: str,
    codespace: Path | None = None,
) -> tuple[str | None, str]:
    """Dispatch GLM state-adjudicator to classify ambiguous agent output.

    Used when structured signal file is absent but output may contain
    signals. Returns (signal_type, detail) or (None, "").
    """
    artifacts = planspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    adj_prompt = artifacts / "adjudicate-prompt.md"
    adj_output = artifacts / "adjudicate-output.md"

    adj_prompt.write_text(f"""# Task: Classify Agent Output

Read the agent output file and determine its state.

## Agent Output File
`{output_path}`

## Instructions

Classify the output into exactly one state. Reply with a JSON block:

```json
{{
  "state": "<STATE>",
  "detail": "<brief explanation>"
}}
```

States: ALIGNED, PROBLEMS, UNDERSPECIFIED, NEED_DECISION, DEPENDENCY,
LOOP_DETECTED, NEEDS_PARENT, OUT_OF_SCOPE, COMPLETED, UNKNOWN.
""", encoding="utf-8")

    result = dispatch_agent(
        "glm", adj_prompt, adj_output,
        planspace, parent, codespace=codespace,
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return None, "ALIGNMENT_CHANGED_PENDING"

    # Parse JSON from adjudicator output
    try:
        json_start = result.find("{")
        json_end = result.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(result[json_start:json_end + 1])
            state = data.get("state", "").lower()
            detail = data.get("detail", "")
            if state in ("underspecified", "underspec"):
                return "underspec", detail
            if state == "need_decision":
                return "need_decision", detail
            if state == "dependency":
                return "dependency", detail
            if state == "loop_detected":
                return "loop_detected", detail
            if state == "needs_parent":
                return "needs_parent", detail
            if state in ("out_of_scope", "out-of-scope"):
                return "out_of_scope", detail
    except (json.JSONDecodeError, KeyError):
        pass
    return None, ""


def check_agent_signals(
    output: str, signal_path: Path | None = None,
    output_path: Path | None = None,
    planspace: Path | None = None,
    parent: str | None = None,
    codespace: Path | None = None,
) -> tuple[str | None, str]:
    """Check for agent signals using structured file + adjudicator fallback.

    Priority:
    1. Read structured signal file (agents write JSON when they need input)
    2. Delegate to GLM state-adjudicator for output interpretation
       (scripts dispatch, agents decide — no regex/prefix heuristics)
    """
    # 1. Structured signal file (most reliable — agents write JSON)
    if signal_path:
        sig, detail = read_agent_signal(signal_path)
        if sig:
            return sig, detail

    # 2. Log prefix matches for diagnostics (NOT used for decisions)
    for line in output.split("\n"):
        line = line.strip()
        for prefix in ("UNDERSPECIFIED:", "NEED_DECISION:",
                       "DEPENDENCY:", "LOOP_DETECTED:"):
            if line.startswith(prefix):
                detail = line[len(prefix):].strip()
                if detail and not (detail.startswith("<")
                                   and detail.endswith(">")):
                    log(f"  signal hint in output: {prefix} {detail[:100]}")

    # 3. Adjudicator interprets output (agent decides, not script regex)
    if output_path and planspace and parent:
        return adjudicate_agent_output(
            output_path, planspace, parent, codespace,
        )

    return None, ""


# ---------------------------------------------------------------------------
# Content-hash change detection
# ---------------------------------------------------------------------------

def hash_file(path: Path) -> str:
    """Return SHA-256 hex digest of a file, or empty string if missing."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_files(codespace: Path, rel_paths: list[str]) -> dict[str, str]:
    """Hash all files before implementation. Returns {rel_path: hash}."""
    return {rp: hash_file(codespace / rp) for rp in rel_paths}


def diff_files(codespace: Path, before: dict[str, str],
               reported: list[str]) -> list[str]:
    """Filter reported modified files to only those that actually changed."""
    changed = []
    for rp in reported:
        after = hash_file(codespace / rp)
        if after != before.get(rp, ""):
            changed.append(rp)
    return changed


# ---------------------------------------------------------------------------
# Cross-section communication
# ---------------------------------------------------------------------------

def compute_text_diff(old_path: Path, new_path: Path) -> str:
    """Compute a unified text diff between two files.

    Returns a human-readable unified diff string. If either file is
    missing, returns an appropriate message instead.
    """
    if not old_path.exists() and not new_path.exists():
        return ""
    if not old_path.exists():
        old_lines: list[str] = []
        old_label = "(did not exist)"
    else:
        old_lines = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
        old_label = str(old_path)
    if not new_path.exists():
        new_lines: list[str] = []
        new_label = "(deleted)"
    else:
        new_lines = new_path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_label = str(new_path)

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=old_label, tofile=new_label,
        lineterm="",
    )
    return "\n".join(diff)


def post_section_completion(
    section: Section,
    modified_files: list[str],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> None:
    """Post-completion steps after a section is ALIGNED.

    a) Snapshot modified files to artifacts/snapshots/section-NN/
    b) Run semantic impact analysis via GLM
    c) Leave consequence notes for materially impacted sections
    """
    artifacts = planspace / "artifacts"
    sec_num = section.number

    # -----------------------------------------------------------------
    # (a) Snapshot modified files
    # -----------------------------------------------------------------
    snapshot_dir = artifacts / "snapshots" / f"section-{sec_num}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    codespace_resolved = codespace.resolve()
    snapshot_resolved = snapshot_dir.resolve()
    for rel_path in modified_files:
        src = (codespace / rel_path).resolve()
        if not src.exists():
            continue
        # Verify src is under codespace (belt-and-suspenders)
        if not src.is_relative_to(codespace_resolved):
            log(f"Section {sec_num}: WARNING — snapshot path escapes "
                f"codespace, skipping: {rel_path}")
            continue
        # Preserve relative directory structure inside the snapshot
        dest = (snapshot_dir / rel_path).resolve()
        if not dest.is_relative_to(snapshot_resolved):
            log(f"Section {sec_num}: WARNING — dest path escapes "
                f"snapshot dir, skipping: {rel_path}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))

    log(f"Section {sec_num}: snapshotted {len(modified_files)} files "
        f"to {snapshot_dir}")
    _log_artifact(planspace, f"snapshot:section-{sec_num}")

    # -----------------------------------------------------------------
    # (b) Semantic impact analysis via GLM
    # -----------------------------------------------------------------
    other_sections = [s for s in all_sections if s.number != sec_num
                      and s.related_files]
    if not other_sections:
        log(f"Section {sec_num}: no other sections to check for impact")
        return

    # Build file-change description
    change_lines = []
    for rel_path in modified_files:
        change_lines.append(f"- `{rel_path}`")
    changes_text = "\n".join(change_lines) if change_lines else "(none)"

    # Build other-sections description
    other_section_lines = []
    for other in other_sections:
        files_str = ", ".join(f"`{f}`" for f in other.related_files[:10])
        if len(other.related_files) > 10:
            files_str += f" (+{len(other.related_files) - 10} more)"
        summary = extract_section_summary(other.path)
        other_section_lines.append(
            f"- SECTION-{other.number}: {summary}\n"
            f"  Related files: {files_str}"
        )
    other_text = "\n".join(other_section_lines)

    section_summary = extract_section_summary(section.path)

    impact_prompt_path = artifacts / f"impact-{sec_num}-prompt.md"
    impact_output_path = artifacts / f"impact-{sec_num}-output.md"
    heading = f"# Task: Semantic Impact Analysis for Section {sec_num}"
    impact_prompt_path.write_text(f"""{heading}

## What Section {sec_num} Did
{section_summary}

## Files Modified by Section {sec_num}
{changes_text}

## Other Sections and Their Files
{other_text}

## Instructions

For each other section listed above, determine if the changes made by
section {sec_num} have a MATERIAL impact on that section's problem, or
if it is just a coincidental file overlap that does not affect the other
section's work.

A change is MATERIAL if:
- It modifies an interface, contract, or API that the other section depends on
- It changes control flow or data structures the other section needs to work with
- It introduces constraints or assumptions the other section must accommodate

A change is NO_IMPACT if:
- The files overlap but the changes are in unrelated parts
- The other section only reads data that was not affected
- The change is purely cosmetic or stylistic

Reply with one line per section, using EXACTLY this format:
SECTION-NN: MATERIAL <brief reason>
or
SECTION-NN: NO_IMPACT
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:impact-{sec_num}")

    log(f"Section {sec_num}: running impact analysis")
    # Emit GLM exploration event for QA monitor rule C2
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
         "summary", f"glm-explore:{sec_num}",
         "impact analysis",
         "--agent", AGENT_NAME],
        capture_output=True, text=True,
    )
    impact_result = dispatch_agent(
        "glm", impact_prompt_path, impact_output_path,
        planspace, parent, codespace=codespace,
        section_number=sec_num,
    )

    # -----------------------------------------------------------------
    # (c) Parse impact results and leave consequence notes
    # -----------------------------------------------------------------
    # Normalize section numbers to canonical form (handles "4" vs "04")
    sec_num_map = build_section_number_map(all_sections)

    impacted_sections: list[tuple[str, str]] = []
    for line in impact_result.split("\n"):
        line = line.strip()
        match = re.match(r'SECTION-(\d+):\s*MATERIAL\s*(.*)', line)
        if match:
            canonical = normalize_section_number(
                match.group(1), sec_num_map,
            )
            impacted_sections.append((canonical, match.group(2)))

    if not impacted_sections:
        log(f"Section {sec_num}: no material impacts on other sections")
        return

    log(f"Section {sec_num}: material impact on sections "
        f"{[s[0] for s in impacted_sections]}")

    notes_dir = artifacts / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Read the section's integration proposal for contract/interface context
    integration_proposal = (artifacts / "proposals"
                            / f"section-{sec_num}-integration-proposal.md")
    contracts_context = ""
    if integration_proposal.exists():
        contracts_context = integration_proposal.read_text(encoding="utf-8")

    # Extract contract/interface sections from proposal for inline notes
    contracts_summary = _extract_contracts_summary(contracts_context)

    for target_num, reason in impacted_sections:
        note_path = notes_dir / f"from-{sec_num}-to-{target_num}.md"

        # Build the list of modified files with brief context
        file_changes = "\n".join(
            f"- `{rel_path}`" for rel_path in modified_files
        )
        heading = (
            f"# Consequence Note: Section {sec_num}"
            f" -> Section {target_num}"
        )
        contracts = (
            contracts_summary
            if contracts_summary
            else "(No explicit contracts extracted "
                 "from integration proposal.)"
        )

        note_path.write_text(f"""{heading}

## Contract Deltas (read this first)
{contracts}

## What Section {target_num} Must Accommodate
{reason}

## Why This Happened
Section {sec_num} ({section_summary}) implemented changes to solve its
designated problem. Impact reason: {reason}

## Files Modified (for reference)
{file_changes}

Full integration proposal: `{integration_proposal}`
Snapshot directory: `{snapshot_dir}`
""", encoding="utf-8")
        _log_artifact(planspace, f"note:from-{sec_num}-to-{target_num}")
        log(f"Section {sec_num}: left note for section {target_num} "
            f"at {note_path}")


def read_incoming_notes(
    section: Section,
    planspace: Path,
    codespace: Path,
) -> str:
    """Read incoming consequence notes from other sections.

    Globs for artifacts/notes/from-*-to-{section.number}.md, reads each
    note, and computes text diffs for shared files that have changed
    since the authoring section last saw them.

    Returns a combined context string suitable for inclusion in prompts.
    Empty string if no notes exist.
    """
    artifacts = planspace / "artifacts"
    notes_dir = artifacts / "notes"
    sec_num = section.number

    if not notes_dir.exists():
        return ""

    note_pattern = f"from-*-to-{sec_num}.md"
    note_files = sorted(notes_dir.glob(note_pattern))

    if not note_files:
        return ""

    log(f"Section {sec_num}: found {len(note_files)} incoming notes")

    parts: list[str] = []
    for note_path in note_files:
        note_text = note_path.read_text(encoding="utf-8")
        parts.append(note_text)

        # Extract the source section number from the filename
        name_match = re.match(r'from-(\d+)-to-\d+\.md', note_path.name)
        if not name_match:
            continue
        source_num = name_match.group(1)

        # Compute diffs for files this section shares with the source
        source_snapshot_dir = (artifacts / "snapshots"
                               / f"section-{source_num}")
        if not source_snapshot_dir.exists():
            continue

        diff_parts: list[str] = []
        for rel_path in section.related_files:
            snapshot_file = source_snapshot_dir / rel_path
            current_file = codespace / rel_path
            if not snapshot_file.exists():
                continue
            diff_text = compute_text_diff(snapshot_file, current_file)
            if diff_text:
                diff_parts.append(
                    f"### Diff: `{rel_path}` "
                    f"(section {source_num}'s snapshot vs current)\n"
                    f"```diff\n{diff_text}\n```"
                )

        if diff_parts:
            parts.append(
                f"### File Diffs Since Section {source_num}\n\n"
                + "\n\n".join(diff_parts)
            )

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_section_summary(section_path: Path) -> str:
    """Extract summary from YAML frontmatter of a section file."""
    text = section_path.read_text(encoding="utf-8")
    match = re.search(r'^---\s*\n.*?^summary:\s*(.+?)$.*?^---',
                      text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: first non-blank, non-heading line
    for line in text.split('\n'):
        line = line.strip()
        if line and not line.startswith('---') and not line.startswith('#'):
            return line[:200]
    return "(no summary available)"


def read_decisions(planspace: Path, section_number: str) -> str:
    """Read accumulated decisions from parent for a section.

    Returns the decisions text (may be multi-entry), or empty string
    if no decisions file exists.
    """
    decisions_file = (planspace / "artifacts" / "decisions"
                      / f"section-{section_number}.md")
    if decisions_file.exists():
        return decisions_file.read_text(encoding="utf-8")
    return ""


def persist_decision(planspace: Path, section_number: str,
                     payload: str) -> None:
    """Persist a resume payload as a decision for a section."""
    decisions_dir = planspace / "artifacts" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_file = decisions_dir / f"section-{section_number}.md"
    with decision_file.open("a", encoding="utf-8") as f:
        f.write(f"\n## Decision (from parent)\n{payload}\n")
    _log_artifact(planspace, f"decision:section-{section_number}")


def normalize_section_number(
    raw_num: str,
    sec_num_map: dict[int, str],
) -> str:
    """Normalize a parsed section number to its canonical form.

    Handles mismatches like "4" vs "04" by mapping through int values.
    Falls back to the raw string if no canonical mapping exists.
    """
    try:
        return sec_num_map.get(int(raw_num), raw_num)
    except ValueError:
        return raw_num


def build_section_number_map(sections: list[Section]) -> dict[int, str]:
    """Build a mapping from int section number to canonical string form."""
    return {int(s.number): s.number for s in sections}


def _extract_contracts_summary(proposal_text: str) -> str:
    """Extract contract/interface mentions from an integration proposal.

    Scans for headings containing 'contract', 'interface', 'api', or
    'integration point' and returns their content. Returns empty string
    if no relevant sections found.
    """
    if not proposal_text:
        return ""
    lines = proposal_text.split("\n")
    parts: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("#") and any(
            kw in stripped for kw in
            ["contract", "interface", "api", "integration point",
             "change strategy", "risks"]
        ):
            capturing = True
            parts.append(line)
        elif capturing and line.strip().startswith("#"):
            capturing = False
        elif capturing:
            parts.append(line)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Prompt builders (filepath-based — agents read files themselves)
# ---------------------------------------------------------------------------

def signal_instructions(signal_path: Path) -> str:
    """Return signal instructions for an agent prompt.

    Includes the signal file path so agents know where to write.
    """
    return f"""
## Signals (if you encounter problems)

If you cannot complete the task, write a structured JSON signal file.
This is the primary and mandatory channel for signaling blockers.

**Signal file**: Write to `{signal_path}`
Format:
```json
{{
  "state": "<STATE>",
  "detail": "<brief explanation of the blocker>",
  "needs": "<what specific information or action is needed to unblock>",
  "assumptions_refused": "<what assumptions you chose NOT to make and why>",
  "suggested_escalation_target": "<who should handle this: parent, user, or specific section>"
}}
```
States: UNDERSPECIFIED, NEED_DECISION, DEPENDENCY

**Backup output line**: Also output EXACTLY ONE of these on its own line:
UNDERSPECIFIED: <what information is missing and why you can't proceed>
NEED_DECISION: <what tradeoff or constraint question needs a human answer>
DEPENDENCY: <which other section must be implemented first and why>

Only use these if you truly cannot proceed. Do NOT silently invent
constraints or make assumptions — signal upward and let the parent decide.
"""


def agent_mail_instructions(planspace: Path, agent_name: str,
                            monitor_name: str) -> str:
    """Return narration-via-mailbox instructions for an agent.

    Agents send narration to their OWN mailbox (agent_name), which the
    per-agent monitor watches. This keeps narration separate from the
    section-loop's control mailbox.
    """
    mailbox_cmd = f'bash "{DB_SH}" send "{planspace / "run.db"}" {agent_name} --from {agent_name}'
    return f"""
## Progress Reporting (CRITICAL — do this throughout)

Your agent name: `{agent_name}`
Your narration mailbox: `{agent_name}`
Your monitor: `{monitor_name}`

**Before each significant action**, send a mail message describing what
you are about to do. Use this exact command:

```bash
{mailbox_cmd} "plan: <what you are about to do>"
```

Send mail at these points:
- Before reading a file: `plan: reading <filepath> to understand <why>`
- Before making a decision: `plan: deciding <what> because <reasoning>`
- Before editing a file: `plan: editing <filepath> to <what change>`
- After completing a step: `done: <what was completed>`

**If you notice you are about to do something you already did**, you have
entered a loop (likely from context compaction). Send:
```bash
{mailbox_cmd} "LOOP_DETECTED: <what task was repeated>"
```
and stop immediately. Do NOT continue working.

This mail goes to your narration mailbox where a monitor watches for
problems. Do NOT skip it.
"""


def _extract_todos_from_files(
    codespace: Path, related_files: list[str],
) -> str:
    """Extract TODO/FIXME/HACK blocks from related files.

    Returns a markdown document with each TODO and its surrounding
    context (±3 lines), grouped by file. Empty string if no TODOs found.
    """
    parts: list[str] = []
    for rel_path in related_files:
        full_path = codespace / rel_path
        if not full_path.exists():
            continue
        try:
            lines = full_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        file_todos: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(marker in stripped.upper()
                   for marker in ("TODO", "FIXME", "HACK", "XXX")):
                start = max(0, i - 3)
                end = min(len(lines), i + 4)
                context = "\n".join(
                    f"  {j + 1}: {lines[j]}" for j in range(start, end)
                )
                file_todos.append(
                    f"**Line {i + 1}**: `{stripped}`\n\n"
                    f"```\n{context}\n```\n"
                )
        if file_todos:
            parts.append(f"### {rel_path}\n\n" + "\n".join(file_todos))

    if not parts:
        return ""
    return "# TODO Blocks (In-Code Microstrategies)\n\n" + "\n".join(parts)


def _check_needs_microstrategy(proposal_path: Path) -> bool:
    """Check if the integration proposal requests a microstrategy.

    The integration proposer includes ``needs_microstrategy: true``
    in its output when the section is complex enough to benefit from
    a tactical per-file breakdown. The script reads this mechanically.
    """
    if not proposal_path.exists():
        return False
    text = proposal_path.read_text(encoding="utf-8").lower()
    return "needs_microstrategy: true" in text or "needs_microstrategy:true" in text


def _append_open_problem(
    planspace: Path, section_number: str,
    problem: str, source: str,
) -> None:
    """Append an open problem to the section's spec file.

    Open problems are first-class artifacts — any agent (scan, proposal,
    implementation) can surface them. They represent issues that could not
    be resolved at the current level and need upward routing.
    """
    sec_file = (planspace / "artifacts" / "sections"
                / f"section-{section_number}.md")
    if not sec_file.exists():
        return
    content = sec_file.read_text(encoding="utf-8")
    entry = f"- **[{source}]** {problem}\n"
    if "## Open Problems" in content:
        # Append to existing section
        content = content.replace(
            "## Open Problems\n",
            f"## Open Problems\n{entry}",
        )
    else:
        # Add new section at the end
        content = content.rstrip() + f"\n\n## Open Problems\n{entry}"
    sec_file.write_text(content, encoding="utf-8")


def _reexplore_section(
    section: Section, planspace: Path, codespace: Path, parent: str,
) -> str | None:
    """Dispatch an Opus re-explorer when a section has no related files.

    The agent reads the codemap + section text and either proposes
    candidate files or declares greenfield. If files are found, the
    agent appends ``## Related Files`` to the section file directly.

    Returns the raw agent output, or "ALIGNMENT_CHANGED_PENDING" if
    alignment changed during dispatch.
    """
    artifacts = planspace / "artifacts"
    codemap_path = artifacts / "codemap.md"
    prompt_path = artifacts / f"reexplore-{section.number}-prompt.md"
    output_path = artifacts / f"reexplore-{section.number}-output.md"
    summary = extract_section_summary(section.path)

    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"3. Codemap: `{codemap_path}`"

    prompt_path.write_text(f"""# Task: Re-Explore Section {section.number}

## Summary
{summary}

## Files to Read
1. Section specification: `{section.path}`
2. Codespace root: `{codespace}`
{codemap_ref}

## Context
This section has NO related files after the initial codemap exploration.
Your job is to determine why and classify the situation.

## Instructions
1. Read the section specification to understand the problem
2. Read the codemap (if it exists) for project structure context
3. Explore the codespace strategically — search for files that relate
   to this section's problem space
4. Use GLM sub-agents for quick file reads:
   ```bash
   uv run --frozen agents --model glm --project "{codespace}" "<instructions>"
   ```

## Output

If you find related files, append them to the section file at
`{section.path}` using the standard format:

```
## Related Files

### <relative-path>
Brief reason why this file matters.
```

Then write a brief classification to `{output_path}`:
- `section_mode: brownfield | greenfield | hybrid`
- Justification (1-2 sentences)
- Any open problems or research questions
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:reexplore-{section.number}")

    result = dispatch_agent(
        "claude-opus", prompt_path, output_path,
        planspace, parent, f"reexplore-{section.number}",
        codespace=codespace, section_number=section.number,
        agent_file="section-re-explorer.md",
    )
    return result


def write_section_setup_prompt(
    section: Section, planspace: Path, codespace: Path,
    global_proposal: Path, global_alignment: Path,
) -> Path:
    """Write the prompt for extracting section-level excerpts from globals.

    Produces a prompt for an agent to read the global proposal and global
    alignment documents, find the parts relevant to this section, and write
    two excerpt files: section-NN-proposal-excerpt.md and
    section-NN-alignment-excerpt.md.
    """
    artifacts = planspace / "artifacts"
    sections_dir = artifacts / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / f"setup-{section.number}-prompt.md"
    proposal_excerpt = sections_dir / f"section-{section.number}-proposal-excerpt.md"
    alignment_excerpt = sections_dir / f"section-{section.number}-alignment-excerpt.md"
    a_name = f"setup-{section.number}"
    m_name = f"{a_name}-monitor"
    summary = extract_section_summary(section.path)

    # Reference decisions file if it exists (filepath-based, not embedded)
    decisions_file = (planspace / "artifacts" / "decisions"
                      / f"section-{section.number}.md")
    decisions_block = ""
    if decisions_file.exists():
        decisions_block = f"""
## Parent Decisions (from prior pause/resume cycles)
Read decisions file: `{decisions_file}`

Use this context to inform your excerpt extraction — the parent has
provided additional guidance about this section.
"""

    prompt_path.write_text(f"""# Task: Extract Section {section.number} Excerpts

## Summary
{summary}
{decisions_block}
## Files to Read
1. Section specification: `{section.path}`
2. Global proposal: `{global_proposal}`
3. Global alignment: `{global_alignment}`

## Instructions

Read the section specification first to understand what section {section.number}
covers. Then read both global documents.

### Output 1: Proposal Excerpt
From the global proposal, extract the parts relevant to this section.
Copy/paste the relevant content WITH enough surrounding context to be
self-contained. Do NOT rewrite or interpret — use the original text.
Include any context paragraphs needed for the excerpt to make sense
on its own.

Write to: `{proposal_excerpt}`

### Output 2: Alignment Excerpt
From the global alignment, extract the parts relevant to this section.
Same rules: copy/paste with context, do NOT rewrite. Include alignment
criteria, constraints, examples, and anti-patterns that apply to this
section's problem space.

Write to: `{alignment_excerpt}`

### Output 3: Problem Frame
Write a brief problem frame for this section — a pre-exploration gate
that captures understanding BEFORE any integration work begins:

1. **Problem**: What problem is this section solving? (1-2 sentences)
2. **Evidence**: What evidence from the proposal/alignment supports this
   being the right problem to solve? (bullet points)
3. **Constraints**: What constraints from the global alignment apply to
   this section specifically? (bullet points)

Write to: `{artifacts / "sections" / f"section-{section.number}-problem-frame.md"}`

### Important
- Excerpts are copy/paste, not summaries. Use the original text.
- Include enough surrounding context that each file stands alone.
- If the global document covers this section across multiple places,
  include all relevant parts.
- Preserve section headings and structure from the originals.
- The problem frame IS a summary — keep it brief and focused.
{signal_instructions(artifacts / "signals" / f"setup-{section.number}-signal.json")}
{agent_mail_instructions(planspace, a_name, m_name)}
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:setup-{section.number}")
    return prompt_path


def write_integration_proposal_prompt(
    section: Section, planspace: Path, codespace: Path,
    alignment_problems: str | None = None,
    incoming_notes: str | None = None,
) -> Path:
    """Write the prompt for GPT to create an integration proposal.

    GPT reads the section excerpts + source files, explores the codebase
    strategically using sub-agents, and writes a high-level integration
    proposal: HOW to wire the existing proposal into the codebase.
    """
    artifacts = planspace / "artifacts"
    proposals_dir = artifacts / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / f"intg-proposal-{section.number}-prompt.md"
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{section.number}-proposal-excerpt.md")
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{section.number}-alignment-excerpt.md")
    integration_proposal = (
        proposals_dir
        / f"section-{section.number}-integration-proposal.md"
    )
    a_name = f"intg-proposal-{section.number}"
    m_name = f"{a_name}-monitor"
    summary = extract_section_summary(section.path)

    file_list = []
    for rel_path in section.related_files:
        full_path = codespace / rel_path
        status = "" if full_path.exists() else " (to be created)"
        file_list.append(f"   - `{full_path}`{status}")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    # Write alignment problems to file if present (avoid inline embedding)
    problems_block = ""
    if alignment_problems:
        problems_file = (artifacts
                         / f"intg-proposal-{section.number}-problems.md")
        problems_file.write_text(alignment_problems, encoding="utf-8")
        problems_block = f"""
## Previous Alignment Problems

The alignment check found problems with your previous integration
proposal. Read them and address ALL of them in this revision:
`{problems_file}`
"""

    existing_note = ""
    if integration_proposal.exists():
        existing_note = f"""
## Existing Integration Proposal
There is an existing proposal from a previous round at:
`{integration_proposal}`
Read it and revise it to address the alignment problems above.
"""

    # Write incoming notes to file if present (avoid inline embedding)
    notes_block = ""
    if incoming_notes:
        notes_file = (artifacts
                      / f"intg-proposal-{section.number}-notes.md")
        notes_file.write_text(incoming_notes, encoding="utf-8")
        notes_block = f"""
## Notes from Other Sections

Other sections have completed work that may affect this section. Read
these notes carefully — they describe consequences, contracts, and
interfaces that may constrain or inform your integration strategy:
`{notes_file}`
"""

    # Reference decisions file if it exists (filepath-based)
    decisions_file = (planspace / "artifacts" / "decisions"
                      / f"section-{section.number}.md")
    decisions_block = ""
    if decisions_file.exists():
        decisions_block = f"""
## Decisions from Parent (answers to earlier questions)

Read the decisions provided in response to earlier signals and
incorporate them into your proposal: `{decisions_file}`
"""

    codemap_path = artifacts / "codemap.md"
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"\n5. Codemap (project understanding): `{codemap_path}`"

    tools_path = (artifacts / "sections"
                  / f"section-{section.number}-tools-available.md")
    tools_ref = ""
    if tools_path.exists():
        tools_ref = f"\n6. Available tools from earlier sections: `{tools_path}`"

    # Detect section-level mode (takes priority over project-level)
    section_mode_file = (artifacts / "sections"
                         / f"section-{section.number}-mode.txt")
    project_mode_file = artifacts / "project-mode.txt"
    section_mode = None
    if section_mode_file.exists():
        section_mode = section_mode_file.read_text(encoding="utf-8").strip()
    project_mode = "brownfield"
    if project_mode_file.exists():
        project_mode = project_mode_file.read_text(
            encoding="utf-8").strip()
    effective_mode = section_mode or project_mode
    mode_block = ""
    if effective_mode == "greenfield":
        mode_block = """
## Section Mode: GREENFIELD

This section has no existing code to modify. Your integration proposal
should focus on:
- What NEW files and modules to create
- Where in the project structure they belong
- How they connect to existing architecture (imports, interfaces)
- What scaffolding is needed before implementation
"""
    elif effective_mode == "hybrid":
        mode_block = """
## Section Mode: HYBRID

This section has some existing code but also needs new files. Your
integration proposal should cover both:
- How to modify existing files (brownfield integration)
- What new files to create and where they fit
- How new and existing code connect
"""

    prompt_path.write_text(f"""# Task: Integration Proposal for Section {section.number}

## Summary
{summary}

## Files to Read
1. Section proposal excerpt: `{proposal_excerpt}`
2. Section alignment excerpt: `{alignment_excerpt}`
3. Section specification: `{section.path}`
4. Related source files (read each one):
{files_block}{codemap_ref}{tools_ref}
{existing_note}{problems_block}{notes_block}{decisions_block}{mode_block}
## Instructions

You are writing an INTEGRATION PROPOSAL — a strategic document describing
HOW to wire the existing proposal into the codebase. The proposal excerpt
already says WHAT to build. Your job is to figure out how it maps onto the
real code.

### Phase 1: Explore and Understand

Before writing anything, explore the codebase strategically. You MUST
understand the existing code before proposing how to integrate.

**Start with the codemap** if available — it captures the project's
structure, key files, and how parts relate. Use it to orient yourself
before diving into individual files.

**Dispatch GLM sub-agents for targeted exploration:**
```bash
uv run --frozen agents --model glm --project "{codespace}" "<instructions>"
```

Use GLM to:
- Read files related to this section and understand their structure
- Find callers/callees of functions you need to modify
- Check what interfaces or contracts currently exist
- Understand the module organization and import patterns
- Verify assumptions about how the code works

Do NOT try to understand everything upfront. Explore strategically:
form a hypothesis, verify it with a targeted read, adjust, repeat.

### Phase 2: Write the Integration Proposal

After exploring, write a high-level integration strategy covering:

1. **Problem mapping** — How does the section proposal map onto what
   currently exists in the code? What's the gap between current and target?
2. **Integration points** — Where does the new functionality connect to
   existing code? Which interfaces, call sites, or data flows are affected?
3. **Change strategy** — High-level approach: which files change, what kind
   of changes (new functions, modified control flow, new modules, etc.),
   and in what order?
4. **Risks and dependencies** — What could go wrong? What assumptions are
   we making? What depends on other sections?

This is STRATEGIC — not line-by-line changes. Think about the shape of
the solution, not the exact code.

Write your integration proposal to: `{integration_proposal}`

### Microstrategy Decision

At the end of your proposal, include this line:
```
needs_microstrategy: true
```
or
```
needs_microstrategy: false
```

Set it to `true` if the section is complex enough that an implementation
agent would benefit from a tactical per-file breakdown (many files, complex
interactions, ordering dependencies). Set `false` for simple sections where
the integration proposal is sufficient guidance.
{signal_instructions(artifacts / "signals" / f"proposal-{section.number}-signal.json")}
{agent_mail_instructions(planspace, a_name, m_name)}
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:proposal-{section.number}")
    return prompt_path


def write_integration_alignment_prompt(
    section: Section, planspace: Path, codespace: Path,
) -> Path:
    """Write the prompt for Opus to review the integration proposal.

    Checks shape and direction: is the integration proposal still solving
    the right problem? Has intent drifted? NOT checking tiny details.
    """
    artifacts = planspace / "artifacts"
    prompt_path = artifacts / f"intg-align-{section.number}-prompt.md"
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{section.number}-alignment-excerpt.md")
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{section.number}-proposal-excerpt.md")
    integration_proposal = (artifacts / "proposals"
                            / f"section-{section.number}-integration-proposal.md")
    summary = extract_section_summary(section.path)
    sec = section.number
    heading = (
        f"# Task: Integration Proposal Alignment Check"
        f" — Section {sec}"
    )

    prompt_path.write_text(f"""{heading}

## Summary
{summary}

## Files to Read
1. Section alignment excerpt: `{alignment_excerpt}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Section specification: `{section.path}`
4. Integration proposal to review: `{integration_proposal}`

## Instructions

Read the alignment excerpt and proposal excerpt first — these define the
PROBLEM and CONSTRAINTS. Then read the integration proposal.

Check SHAPE AND DIRECTION only:
- Is the integration proposal still solving the RIGHT PROBLEM?
- Has the intent drifted from what the proposal/alignment describe?
- Does the integration strategy make sense given the actual codebase?
- Are there any fundamental misunderstandings about what's needed?

Do NOT check:
- Tiny implementation details (those get resolved during implementation)
- Exact code patterns or style choices
- Whether every edge case is covered
- Completeness of the strategy (some details are fetched on demand later)

Reply with EXACTLY one of:

ALIGNED

or

PROBLEMS:
- <specific problem 1: what's wrong and why it matters>
- <specific problem 2: what's wrong and why it matters>
...

or

UNDERSPECIFIED: <what information is missing and why alignment can't be checked>

Each problem must be specific and actionable. "Needs more detail" is NOT
a valid problem. "The proposal routes X through Y, but the alignment says
X must go through Z because of constraint C" IS a valid problem.
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:proposal-align-{section.number}")
    return prompt_path


def write_strategic_impl_prompt(
    section: Section, planspace: Path, codespace: Path,
    alignment_problems: str | None = None,
) -> Path:
    """Write the prompt for GPT to implement strategically.

    GPT reads the aligned integration proposal + source files, thinks
    strategically, and implements. Dispatches sub-agents as needed.
    Tackles the section holistically — multiple files at once.
    """
    artifacts = planspace / "artifacts"
    prompt_path = artifacts / f"impl-{section.number}-prompt.md"
    integration_proposal = (artifacts / "proposals"
                            / f"section-{section.number}-integration-proposal.md")
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{section.number}-proposal-excerpt.md")
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{section.number}-alignment-excerpt.md")
    modified_report = artifacts / f"impl-{section.number}-modified.txt"
    a_name = f"impl-{section.number}"
    m_name = f"{a_name}-monitor"
    summary = extract_section_summary(section.path)

    file_list = []
    for rel_path in section.related_files:
        full_path = codespace / rel_path
        status = "" if full_path.exists() else " (to be created)"
        file_list.append(f"   - `{full_path}`{status}")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    # Write alignment problems to file if present (avoid inline embedding)
    problems_block = ""
    if alignment_problems:
        problems_file = artifacts / f"impl-{section.number}-problems.md"
        problems_file.write_text(alignment_problems, encoding="utf-8")
        problems_block = f"""
## Previous Implementation Alignment Problems

The alignment check found problems with your previous implementation.
Read them and address ALL of them: `{problems_file}`
"""

    # Reference decisions file if it exists (filepath-based)
    decisions_file = (planspace / "artifacts" / "decisions"
                      / f"section-{section.number}.md")
    decisions_block = ""
    if decisions_file.exists():
        decisions_block = f"""
## Decisions from Parent (answers to earlier questions)

Read decisions: `{decisions_file}`
"""

    codemap_path = artifacts / "codemap.md"
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"\n7. Codemap (project understanding): `{codemap_path}`"

    microstrategy_path = (artifacts / "proposals"
                          / f"section-{section.number}-microstrategy.md")
    micro_ref = ""
    if microstrategy_path.exists():
        micro_ref = (f"\n6. Microstrategy (tactical per-file breakdown): "
                     f"`{microstrategy_path}`")

    tools_path = (artifacts / "sections"
                  / f"section-{section.number}-tools-available.md")
    impl_tools_ref = ""
    if tools_path.exists():
        impl_tools_ref = (f"\n8. Available tools from earlier sections: "
                          f"`{tools_path}`")

    impl_heading = (
        f"# Task: Strategic Implementation"
        f" for Section {section.number}"
    )
    prompt_path.write_text(f"""{impl_heading}

## Summary
{summary}

## Files to Read
1. Integration proposal (ALIGNED): `{integration_proposal}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Section alignment excerpt: `{alignment_excerpt}`
4. Section specification: `{section.path}`
5. Related source files:
{files_block}{micro_ref}{codemap_ref}{impl_tools_ref}
{problems_block}{decisions_block}
## Instructions

You are implementing the changes described in the integration proposal.
The proposal has been alignment-checked and approved. Your job is to
execute it strategically.

### How to Work

**Think strategically, not mechanically.** Read the integration proposal
and understand the SHAPE of the changes. Then tackle them holistically —
multiple files at once, coordinated changes. Use the codemap if available
to understand how your changes fit into the broader project structure.

**Dispatch sub-agents for exploration and targeted work:**

For cheap exploration (reading, checking, verifying):
```bash
uv run --frozen agents --model glm --project "{codespace}" "<instructions>"
```

For targeted implementation of specific areas:
```bash
uv run --frozen agents --model gpt-5.3-codex-high \\
  --project "{codespace}" "<instructions>"
```

Use sub-agents when:
- You need to read several files to understand context before changing them
- A specific area of the implementation is self-contained and can be delegated
- You want to verify your changes didn't break something

Do NOT use sub-agents for everything — handle straightforward changes
yourself directly.

### Implementation Guidelines

1. Follow the integration proposal's strategy
2. Make coordinated changes across files — don't treat each file in isolation
3. If you discover the proposal missed something (a file that needs changing,
   an interface that doesn't work as expected), handle it — you have authority
   to go beyond the proposal where necessary
4. Update docstrings and comments to reflect changes
5. Ensure imports and references are consistent across modified files

### Report Modified Files

After implementation, write a list of ALL files you modified to:
`{modified_report}`

One file path per line (relative to codespace root `{codespace}`).
Include files modified by sub-agents. Include ALL files — both directly
modified and indirectly affected.
{signal_instructions(artifacts / "signals" / f"impl-{section.number}-signal.json")}
{agent_mail_instructions(planspace, a_name, m_name)}
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:impl-{section.number}")
    return prompt_path


def write_impl_alignment_prompt(
    section: Section, planspace: Path, codespace: Path,
) -> Path:
    """Write the prompt for Opus to verify implementation alignment.

    Same shape/direction check as the integration alignment, but applied
    to the actual code changes.
    """
    artifacts = planspace / "artifacts"
    prompt_path = artifacts / f"impl-align-{section.number}-prompt.md"
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{section.number}-alignment-excerpt.md")
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{section.number}-proposal-excerpt.md")
    integration_proposal = (artifacts / "proposals"
                            / f"section-{section.number}-integration-proposal.md")
    summary = extract_section_summary(section.path)

    # Collect modified files via the validated collector (sanitizes
    # absolute/traversal paths) and union with section's related files.
    all_paths = set(section.related_files) | set(
        collect_modified_files(planspace, section, codespace)
    )

    file_list = []
    for rel_path in sorted(all_paths):
        full_path = codespace / rel_path
        if full_path.exists():
            file_list.append(f"   - `{full_path}`")
    files_block = "\n".join(file_list) if file_list else "   (none)"
    sec = section.number

    prompt_path.write_text(f"""# Task: Implementation Alignment Check — Section {sec}

## Summary
{summary}

## Files to Read
1. Section alignment excerpt: `{alignment_excerpt}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Integration proposal: `{integration_proposal}`
4. Section specification: `{section.path}`
5. Implemented files (read each one):
{files_block}

## Worktree root
`{codespace}`

## Instructions

Read the alignment excerpt and proposal excerpt first — these define the
PROBLEM and CONSTRAINTS. Then read the integration proposal to understand
WHAT was planned. Finally read the implemented files.

Check SHAPE AND DIRECTION:
- Is the implementation still solving the RIGHT PROBLEM?
- Does the code match the intent of the integration proposal?
- Has anything drifted from the original problem definition?
- Are the changes internally consistent across files?

**Go beyond the file list.** The section spec may require creating new
files or producing artifacts at specific paths. Check the worktree for
any file the section mentions that should exist.

Do NOT check:
- Code style or formatting preferences
- Whether variable names are perfect
- Minor documentation wording
- Edge cases that weren't in the alignment constraints

Reply with EXACTLY one of:

ALIGNED

or

PROBLEMS:
- <specific problem 1: what's wrong, why it matters, what should change>
- <specific problem 2: what's wrong, why it matters, what should change>
...

or

UNDERSPECIFIED: <what information is missing and why alignment can't be checked>

Each problem must be specific and actionable.
""",
        encoding="utf-8",
    )
    _log_artifact(planspace, f"prompt:impl-align-{section.number}")
    return prompt_path


# ---------------------------------------------------------------------------
# Modified file collection
# ---------------------------------------------------------------------------

def collect_modified_files(
    planspace: Path, section: Section, codespace: Path,
) -> list[str]:
    """Collect modified file paths from the implementation report.

    Normalizes all paths to safe relative paths under ``codespace``.
    Absolute paths are converted to relative (if under codespace) or
    rejected. Paths containing ``..`` that escape codespace are rejected.
    """
    artifacts = planspace / "artifacts"
    modified_report = artifacts / f"impl-{section.number}-modified.txt"
    codespace_resolved = codespace.resolve()
    modified = set()
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                # Convert absolute to relative if under codespace
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    log(f"  WARNING: reported path outside codespace, "
                        f"skipping: {line}")
                    continue
            else:
                # Resolve relative path and ensure it stays under codespace
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    log(f"  WARNING: reported path escapes codespace, "
                        f"skipping: {line}")
                    continue
            modified.add(str(rel))
    return list(modified)


def _extract_problems(result: str) -> str | None:
    """Extract problem list from an alignment check result.

    Returns the problems text if PROBLEMS: found, None if ALIGNED.
    Uses first non-empty line for exact-match classification to avoid
    misclassifying outputs containing substrings like "MISALIGNED".
    """
    # Find the first non-empty line for exact classification
    first_line = ""
    for line in result.split("\n"):
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break

    # Exact match ALIGNED on first line (not a substring of MISALIGNED etc.)
    if first_line == "ALIGNED" and "PROBLEMS:" not in result \
            and "UNDERSPECIFIED" not in result:
        return None
    # Extract everything after PROBLEMS:
    idx = result.find("PROBLEMS:")
    if idx != -1:
        return result[idx + len("PROBLEMS:"):].strip()
    # Fallback: return the whole result as problems if not ALIGNED
    return result.strip()


def _run_alignment_check_with_retries(
    section: Section, planspace: Path, codespace: Path, parent: str,
    sec_num: str,
    output_prefix: str = "align",
    max_retries: int = 2,
) -> str | None:
    """Run an alignment check with TIMEOUT retry logic.

    Dispatches Opus for an implementation alignment check. If the agent
    times out, retries up to max_retries times. Returns the alignment
    result text, or None if all retries exhausted.
    """
    artifacts = planspace / "artifacts"
    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        # Poll for control messages before each dispatch attempt
        ctrl = poll_control_messages(planspace, parent,
                                     current_section=sec_num)
        if ctrl == "alignment_changed":
            return "ALIGNMENT_CHANGED_PENDING"
        align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = artifacts / f"{output_prefix}-{sec_num}-output.md"
        result = dispatch_agent(
            "claude-opus", align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=sec_num,
            agent_file="alignment-judge.md",
        )
        if result == "ALIGNMENT_CHANGED_PENDING":
            return result  # Caller must handle
        if not result.startswith("TIMEOUT:"):
            return result
        log(f"  alignment check for section {sec_num} timed out "
            f"(attempt {attempt}/{max_retries + 1})")
    return None


# ---------------------------------------------------------------------------
# Section execution with signal handling
# ---------------------------------------------------------------------------

def run_section(
    planspace: Path, codespace: Path, section: Section, parent: str,
    all_sections: list[Section] | None = None,
) -> list[str] | None:
    """Run a section through the strategic flow.

    0. Read incoming notes from other sections (pre-section)
    1. Section setup (once) — extract proposal/alignment excerpts
    2. Integration proposal loop — GPT proposes, Opus checks alignment
    3. Strategic implementation — GPT implements, Opus checks alignment
    4. Post-completion — snapshot, impact analysis, consequence notes

    Returns modified files on success, or None if paused (waiting for
    parent to handle underspec/decision/dependency and send resume).
    """
    artifacts = planspace / "artifacts"

    # -----------------------------------------------------------------
    # Step 0: Read incoming notes from other sections
    # -----------------------------------------------------------------
    incoming_notes = read_incoming_notes(section, planspace, codespace)
    if incoming_notes:
        log(f"Section {section.number}: received incoming notes from "
            f"other sections")

    # -----------------------------------------------------------------
    # Step 0b: Surface section-relevant tools from tool registry
    # -----------------------------------------------------------------
    tools_available_path = (artifacts / "sections"
                            / f"section-{section.number}-tools-available.md")
    tool_registry_path = artifacts / "tool-registry.json"
    if tool_registry_path.exists():
        try:
            registry = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            all_tools = (registry if isinstance(registry, list)
                         else registry.get("tools", []))
            # Filter to section-relevant: cross-section tools + tools
            # created by this section (section-local from other sections
            # are not surfaced)
            sec_key = f"section-{section.number}"
            relevant_tools = [
                t for t in all_tools
                if t.get("scope") == "cross-section"
                or t.get("created_by") == sec_key
            ]
            if relevant_tools:
                lines = ["# Available Tools\n",
                         "Cross-section and section-local tools:\n"]
                for tool in relevant_tools:
                    path = tool.get("path", "unknown")
                    desc = tool.get("description", "")
                    scope = tool.get("scope", "section-local")
                    creator = tool.get("created_by", "unknown")
                    lines.append(
                        f"- `{path}` ({scope}, from {creator}): {desc}")
                tools_available_path.write_text(
                    "\n".join(lines) + "\n", encoding="utf-8",
                )
                log(f"Section {section.number}: {len(relevant_tools)} "
                    f"relevant tools (of {len(all_tools)} total)")
        except (json.JSONDecodeError, ValueError):
            log(f"Section {section.number}: WARNING — tool-registry.json "
                f"is malformed, skipping")

    # -----------------------------------------------------------------
    # Step 1: Section setup — extract excerpts from global documents
    # -----------------------------------------------------------------
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{section.number}-proposal-excerpt.md")
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{section.number}-alignment-excerpt.md")

    # Setup loop: runs until excerpts exist. Retries after pause/resume.
    while not proposal_excerpt.exists() or not alignment_excerpt.exists():
        log(f"Section {section.number}: setup — extracting excerpts")
        setup_prompt = write_section_setup_prompt(
            section, planspace, codespace,
            section.global_proposal_path,
            section.global_alignment_path,
        )
        setup_output = artifacts / f"setup-{section.number}-output.md"
        setup_agent = f"setup-{section.number}"
        output = dispatch_agent("claude-opus", setup_prompt, setup_output,
                                planspace, parent, setup_agent,
                                codespace=codespace,
                                section_number=section.number,
                                agent_file="setup-excerpter.md")
        if output == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(planspace, parent,
                     f"summary:setup:{section.number}:"
                     f"{summarize_output(output)}")

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            output,
            signal_path=signal_dir / f"setup-{section.number}-signal.json",
            output_path=setup_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal:
            # Surface needs-parent / out-of-scope as open problems
            if signal in ("needs_parent", "out_of_scope"):
                _append_open_problem(
                    planspace, section.number, detail, signal)
                mailbox_send(planspace, parent,
                             f"open-problem:{section.number}:"
                             f"{signal}:{detail[:200]}")
            response = pause_for_parent(
                planspace, parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # Persist resume payload and retry setup
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue  # Retry setup with new decisions context

        # Verify excerpts were created
        if not proposal_excerpt.exists() or not alignment_excerpt.exists():
            log(f"Section {section.number}: ERROR — setup failed to create "
                f"excerpt files")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:setup failed to create "
                         f"excerpt files")
            return None
        break  # Excerpts exist, proceed

    if proposal_excerpt.exists() and alignment_excerpt.exists():
        log(f"Section {section.number}: setup — excerpts ready")
        _record_traceability(
            planspace, section.number,
            f"section-{section.number}-proposal-excerpt.md",
            str(section.global_proposal_path),
            "excerpt extraction from global proposal",
        )
        _record_traceability(
            planspace, section.number,
            f"section-{section.number}-alignment-excerpt.md",
            str(section.global_alignment_path),
            "excerpt extraction from global alignment",
        )

    # -----------------------------------------------------------------
    # Step 1.5: Extract TODO blocks from related files (conditional)
    # -----------------------------------------------------------------
    todos_path = (artifacts / "todos"
                  / f"section-{section.number}-todos.md")
    if not todos_path.exists() and section.related_files:
        todos_path.parent.mkdir(parents=True, exist_ok=True)
        todo_entries = _extract_todos_from_files(codespace, section.related_files)
        if todo_entries:
            todos_path.write_text(todo_entries, encoding="utf-8")
            log(f"Section {section.number}: extracted TODOs from "
                f"related files")
            _record_traceability(
                planspace, section.number,
                f"section-{section.number}-todos.md",
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        else:
            log(f"Section {section.number}: no TODOs found in related files")

    # -----------------------------------------------------------------
    # Step 2: Integration proposal loop
    # -----------------------------------------------------------------
    integration_proposal = (artifacts / "proposals"
                            / f"section-{section.number}-integration-proposal.md")
    proposal_problems: str | None = None
    proposal_attempt = 0

    while True:
        # Check for pending messages between iterations
        if handle_pending_messages(planspace, [], set()):
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:aborted")
            return None  # abort

        # Bail out if alignment_changed arrived (excerpts deleted)
        if alignment_changed_pending(planspace):
            log(f"Section {section.number}: alignment changed — "
                "aborting section to restart Phase 1")
            return None

        proposal_attempt += 1
        tag = "revise " if proposal_problems else ""
        log(f"Section {section.number}: {tag}integration proposal "
            f"(attempt {proposal_attempt})")

        # 2a: GPT writes integration proposal
        # Adaptive model escalation: escalate on repeated misalignment
        # or heavy cross-section coupling
        proposal_model = "gpt-5.3-codex-high"
        notes_count = 0
        notes_dir = planspace / "artifacts" / "notes"
        if notes_dir.exists():
            notes_count = len(list(
                notes_dir.glob(f"from-*-to-{section.number}.md")))
        if proposal_attempt >= 3 or notes_count >= 3:
            proposal_model = "gpt-5.3-codex-xhigh"
            log(f"Section {section.number}: escalating to "
                f"{proposal_model} (attempt={proposal_attempt}, "
                f"notes={notes_count})")

        intg_prompt = write_integration_proposal_prompt(
            section, planspace, codespace, proposal_problems,
            incoming_notes=incoming_notes,
        )
        intg_output = artifacts / f"intg-proposal-{section.number}-output.md"
        intg_agent = f"intg-proposal-{section.number}"
        intg_result = dispatch_agent(
            proposal_model, intg_prompt, intg_output,
            planspace, parent, intg_agent, codespace=codespace,
            section_number=section.number,
            agent_file="integration-proposer.md",
        )
        if intg_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(planspace, parent,
                     f"summary:proposal:{section.number}:"
                     f"{summarize_output(intg_result)}")

        # Detect timeout explicitly (callers handle, not dispatch_agent)
        if intg_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: integration proposal agent "
                f"timed out")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:integration proposal "
                         f"agent timed out")
            return None

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            intg_result,
            signal_path=signal_dir / f"proposal-{section.number}-signal.json",
            output_path=intg_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal:
            # Surface needs-parent / out-of-scope as open problems
            if signal in ("needs_parent", "out_of_scope"):
                _append_open_problem(
                    planspace, section.number, detail, signal)
                mailbox_send(planspace, parent,
                             f"open-problem:{section.number}:"
                             f"{signal}:{detail[:200]}")
            response = pause_for_parent(
                planspace, parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # Persist resume payload and retry the step
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            # Check if alignment changed during the pause
            if alignment_changed_pending(planspace):
                return None
            continue  # Restart proposal step with new context

        # Verify proposal was written
        if not integration_proposal.exists():
            log(f"Section {section.number}: ERROR — integration proposal "
                f"not written")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:integration proposal "
                         f"not written")
            return None

        # 2b: Opus checks alignment
        log(f"Section {section.number}: proposal alignment check")
        align_prompt = write_integration_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = (artifacts
                        / f"intg-align-{section.number}-output.md")
        # No agent_name → no per-agent monitor for alignment checks
        # (Opus alignment prompts don't include narration instructions,
        # so a monitor would false-positive STALLED after 5 min silence)
        align_result = dispatch_agent(
            "claude-opus", align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=section.number,
            agent_file="alignment-judge.md",
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        # Detect timeout on alignment check
        if align_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: proposal alignment check "
                f"timed out — retrying")
            proposal_problems = "Previous alignment check timed out."
            continue

        # 2c/2d: Check result
        problems = _extract_problems(align_result)

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=(signal_dir
                         / f"proposal-align-{section.number}-signal.json"),
            output_path=(artifacts
                         / f"align-proposal-{section.number}-output.md"),
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal == "underspec":
            response = pause_for_parent(
                planspace, parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if problems is None:
            # ALIGNED — proceed to implementation
            log(f"Section {section.number}: integration proposal ALIGNED")
            mailbox_send(planspace, parent,
                         f"summary:proposal-align:{section.number}:ALIGNED")
            break

        # Problems found — feed back into next proposal attempt
        proposal_problems = problems
        short = problems[:200]
        log(f"Section {section.number}: integration proposal problems "
            f"(attempt {proposal_attempt}): {short}")
        mailbox_send(planspace, parent,
                     f"summary:proposal-align:{section.number}:"
                     f"PROBLEMS-attempt-{proposal_attempt}:{short}")

    # -----------------------------------------------------------------
    # Step 2.5: Generate microstrategy (agent-driven decision)
    # -----------------------------------------------------------------
    # The integration proposer decides whether a microstrategy is needed
    # by including "needs_microstrategy: true" in its output. The script
    # checks mechanically — no hardcoded file-count thresholds.
    microstrategy_path = (artifacts / "proposals"
                          / f"section-{section.number}-microstrategy.md")
    needs_microstrategy = (
        _check_needs_microstrategy(integration_proposal)
        and not microstrategy_path.exists()
    )
    if not needs_microstrategy and not microstrategy_path.exists():
        log(f"Section {section.number}: integration proposer did not "
            f"request microstrategy — skipping")
    if needs_microstrategy:
        log(f"Section {section.number}: generating microstrategy")
        micro_prompt_path = (artifacts
                             / f"microstrategy-{section.number}-prompt.md")
        micro_output_path = (artifacts
                             / f"microstrategy-{section.number}-output.md")
        integration_proposal = (
            artifacts / "proposals"
            / f"section-{section.number}-integration-proposal.md"
        )
        a_name = f"microstrategy-{section.number}"
        m_name = f"{a_name}-monitor"

        file_list = "\n".join(
            f"- `{codespace / rp}`"
            for rp in section.related_files
        )
        todos_ref = ""
        section_todos = (artifacts / "todos"
                         / f"section-{section.number}-todos.md")
        if section_todos.exists():
            todos_ref = f"\nRead the TODO extraction: `{section_todos}`"

        micro_prompt_path.write_text(f"""# Task: Microstrategy for Section {section.number}

## Context
Read the integration proposal: `{integration_proposal}`
Read the alignment excerpt: `{artifacts / "sections" / f"section-{section.number}-alignment-excerpt.md"}`{todos_ref}

## Related Files
{file_list}

## Instructions

The integration proposal describes the HIGH-LEVEL strategy for this
section. Your job is to produce a MICROSTRATEGY — a tactical per-file
breakdown that an implementation agent can follow directly.

For each file that needs changes, write:
1. **File path** and whether it's new or modified
2. **What changes** — specific functions, classes, or blocks to add/modify
3. **Order** — which file changes depend on which others
4. **Risks** — what could go wrong with this specific change

Write the microstrategy to: `{microstrategy_path}`

Keep it tactical and concrete. The integration proposal already justified
WHY — you're capturing WHAT and WHERE at the file level.
{agent_mail_instructions(planspace, a_name, m_name)}
""", encoding="utf-8")
        _log_artifact(planspace, f"prompt:microstrategy-{section.number}")

        ctrl = poll_control_messages(planspace, parent,
                                     current_section=section.number)
        if ctrl == "alignment_changed":
            return None
        micro_result = dispatch_agent(
            "gpt-5.3-codex-high", micro_prompt_path, micro_output_path,
            planspace, parent, a_name, codespace=codespace,
            section_number=section.number,
            agent_file="microstrategy-writer.md",
        )
        if micro_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        log(f"Section {section.number}: microstrategy generated")
        _record_traceability(
            planspace, section.number,
            f"section-{section.number}-microstrategy.md",
            f"section-{section.number}-integration-proposal.md",
            "tactical breakdown from integration proposal",
        )
        mailbox_send(planspace, parent,
                     f"summary:microstrategy:{section.number}:generated")

    # -----------------------------------------------------------------
    # Step 3: Strategic implementation
    # -----------------------------------------------------------------

    # Snapshot all known files before implementation.
    # Used after alignment to detect real vs. phantom modifications.
    all_known_paths = list(section.related_files)
    pre_hashes = snapshot_files(codespace, all_known_paths)

    impl_problems: str | None = None
    impl_attempt = 0

    while True:
        # Check for pending messages between iterations
        if handle_pending_messages(planspace, [], set()):
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:aborted")
            return None  # abort

        # Bail out if alignment_changed arrived (excerpts deleted)
        if alignment_changed_pending(planspace):
            log(f"Section {section.number}: alignment changed — "
                "aborting section to restart Phase 1")
            return None

        impl_attempt += 1
        tag = "fix " if impl_problems else ""
        log(f"Section {section.number}: {tag}strategic implementation "
            f"(attempt {impl_attempt})")

        # 3a: GPT implements strategically
        impl_prompt = write_strategic_impl_prompt(
            section, planspace, codespace, impl_problems,
        )
        impl_output = artifacts / f"impl-{section.number}-output.md"
        impl_agent = f"impl-{section.number}"
        impl_result = dispatch_agent(
            "gpt-5.3-codex-high", impl_prompt, impl_output,
            planspace, parent, impl_agent, codespace=codespace,
            section_number=section.number,
            agent_file="implementation-strategist.md",
        )
        if impl_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(planspace, parent,
                     f"summary:impl:{section.number}:"
                     f"{summarize_output(impl_result)}")

        # Detect timeout explicitly
        if impl_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: implementation agent timed out")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:implementation agent "
                         f"timed out")
            return None

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            impl_result,
            signal_path=signal_dir / f"impl-{section.number}-signal.json",
            output_path=(artifacts
                         / f"impl-{section.number}-output.md"),
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal:
            response = pause_for_parent(
                planspace, parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # Persist resume payload and retry the step
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue  # Restart implementation step with new context

        # 3b: Opus checks implementation alignment
        log(f"Section {section.number}: implementation alignment check")
        impl_align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        impl_align_output = (artifacts
                             / f"impl-align-{section.number}-output.md")
        # No agent_name → no per-agent monitor (same rationale as 2b)
        impl_align_result = dispatch_agent(
            "claude-opus", impl_align_prompt, impl_align_output,
            planspace, parent, codespace=codespace,
            section_number=section.number,
            agent_file="alignment-judge.md",
        )
        if impl_align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        # Detect timeout on alignment check
        if impl_align_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: implementation alignment check "
                f"timed out — retrying")
            impl_problems = "Previous alignment check timed out."
            continue

        # 3c/3d: Check result
        problems = _extract_problems(impl_align_result)

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            impl_align_result,
            signal_path=(signal_dir
                         / f"impl-align-{section.number}-signal.json"),
            output_path=impl_align_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal == "underspec":
            response = pause_for_parent(
                planspace, parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if problems is None:
            # ALIGNED — section complete
            log(f"Section {section.number}: implementation ALIGNED")
            mailbox_send(planspace, parent,
                         f"summary:impl-align:{section.number}:ALIGNED")
            break

        # Problems found — feed back into next implementation attempt
        impl_problems = problems
        short = problems[:200]
        log(f"Section {section.number}: implementation problems "
            f"(attempt {impl_attempt}): {short}")
        mailbox_send(planspace, parent,
                     f"summary:impl-align:{section.number}:"
                     f"PROBLEMS-attempt-{impl_attempt}:{short}")

    # Validate modifications against actual file content changes.
    # Two categories:
    # 1. Snapshotted files (related_files) — verified via content-hash diff
    # 2. Reported-but-not-snapshotted files — trusted as "touched" only if
    #    they exist on disk (avoids inflated counts from empty-hash default)
    reported = collect_modified_files(planspace, section, codespace)
    snapshotted_set = set(section.related_files)
    # Diff snapshotted files (related_files union reported that were snapshotted)
    snapshotted_candidates = sorted(
        snapshotted_set | (set(reported) & set(pre_hashes))
    )
    verified_changed = diff_files(codespace, pre_hashes, snapshotted_candidates)
    # Files reported but NOT in the pre-snapshot — include if they exist
    unsnapshotted_reported = [
        rp for rp in reported
        if rp not in pre_hashes and (codespace / rp).exists()
    ]
    if unsnapshotted_reported:
        log(f"Section {section.number}: {len(unsnapshotted_reported)} "
            f"reported files were outside the pre-snapshot set (trusted)")
    actually_changed = sorted(set(verified_changed) | set(unsnapshotted_reported))
    if len(reported) != len(actually_changed):
        log(f"Section {section.number}: {len(reported)} reported, "
            f"{len(actually_changed)} actually changed (detected via diff)")

    # Record change provenance in traceability chain
    for changed_file in actually_changed:
        _record_traceability(
            planspace, section.number,
            changed_file,
            f"section-{section.number}-integration-proposal.md",
            "implementation change",
        )

    # -----------------------------------------------------------------
    # Step 3b: Validate tool registry after implementation
    # -----------------------------------------------------------------
    if tool_registry_path.exists():
        try:
            post_registry = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            post_tools = (post_registry if isinstance(post_registry, list)
                          else post_registry.get("tools", []))
            # Check if implementation added new tools
            pre_count = len(relevant_tools) if "relevant_tools" in dir() else 0
            if len(post_tools) > pre_count:
                log(f"Section {section.number}: new tools registered — "
                    f"dispatching tool-registrar for validation")
                registrar_prompt = (
                    artifacts / f"tool-registrar-{section.number}-prompt.md"
                )
                registrar_prompt.write_text(
                    f"# Validate Tool Registry\n\n"
                    f"Section {section.number} just completed implementation.\n"
                    f"Validate the tool registry at: `{tool_registry_path}`\n\n"
                    f"For each tool entry:\n"
                    f"1. Read the tool file and verify it exists and is "
                    f"legitimate\n"
                    f"2. Verify scope classification is correct\n"
                    f"3. Remove entries for files that don't exist or "
                    f"aren't tools\n"
                    f"4. If any cross-section tools were added, verify "
                    f"they are genuinely reusable\n\n"
                    f"Write the validated registry back to the same path.\n",
                    encoding="utf-8",
                )
                registrar_output = (
                    artifacts / f"tool-registrar-{section.number}-output.md"
                )
                dispatch_agent(
                    "glm", registrar_prompt, registrar_output,
                    planspace, parent,
                    f"tool-registrar-{section.number}",
                    codespace=codespace,
                    agent_file="tool-registrar",
                    section_number=section.number,
                )
        except (json.JSONDecodeError, ValueError):
            pass  # Malformed registry — already warned in Step 0b

    # -----------------------------------------------------------------
    # Step 4: Post-completion — snapshots, impact analysis, notes
    # -----------------------------------------------------------------
    if actually_changed and all_sections:
        post_section_completion(
            section, actually_changed, all_sections,
            planspace, codespace, parent,
        )

    return actually_changed


# ---------------------------------------------------------------------------
# Global problem coordinator
# ---------------------------------------------------------------------------

@dataclass
class SectionResult:
    """Stores the outcome of a section's initial pass."""
    section_number: str
    aligned: bool = False
    problems: str | None = None
    modified_files: list[str] = field(default_factory=list)


def _collect_outstanding_problems(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
) -> list[dict[str, Any]]:
    """Collect all outstanding problems across sections.

    Includes both misaligned sections AND unaddressed consequence notes
    from the cross-section communication system.

    Returns a list of problem dicts, each with:
      - section: section number
      - type: "misaligned" | "unaddressed_note"
      - description: the problem text
      - files: list of files related to this section
    """
    problems = []
    for sec_num, result in section_results.items():
        if result.aligned:
            continue
        section = sections_by_num.get(sec_num)
        files = list(section.related_files) if section else []

        if result.problems:
            problems.append({
                "section": sec_num,
                "type": "misaligned",
                "description": result.problems,
                "files": files,
            })

    # Scan for unaddressed consequence notes:
    # Notes targeting aligned sections whose content hasn't been
    # incorporated (the aligned section completed before the note arrived).
    notes_dir = planspace / "artifacts" / "notes"
    if notes_dir.exists():
        for note_path in sorted(notes_dir.glob("from-*-to-*.md")):
            name_match = re.match(
                r'from-(\d+)-to-(\d+)\.md', note_path.name,
            )
            if not name_match:
                continue
            target_num = name_match.group(2)
            source_num = name_match.group(1)
            target_result = section_results.get(target_num)
            # If the target is aligned (completed before or after the
            # note) but the source completed AFTER the target, the note
            # may be unaddressed.
            if (target_result and target_result.aligned
                    and int(source_num) > int(target_num)):
                section = sections_by_num.get(target_num)
                files = list(section.related_files) if section else []
                note_text = note_path.read_text(encoding="utf-8")
                problems.append({
                    "section": target_num,
                    "type": "unaddressed_note",
                    "description": (
                        f"Consequence note from section {source_num} "
                        f"arrived after section {target_num} was aligned. "
                        f"Note content:\n{note_text[:500]}"
                    ),
                    "files": files,
                })
    return problems


def _parse_coordination_plan(
    agent_output: str, problems: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Parse JSON coordination plan from agent output.

    Returns the parsed plan dict, or None if parsing fails or the plan
    is structurally invalid (missing indices, duplicate indices, etc.).
    """
    # Extract JSON block from agent output (may be in a code fence)
    json_text = None
    in_fence = False
    fence_lines: list[str] = []
    for line in agent_output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") and not in_fence:
            in_fence = True
            fence_lines = []
            continue
        if stripped.startswith("```") and in_fence:
            in_fence = False
            candidate = "\n".join(fence_lines)
            if '"groups"' in candidate:
                json_text = candidate
                break
            continue
        if in_fence:
            fence_lines.append(line)

    if json_text is None:
        # Try raw JSON (no code fence)
        start = agent_output.find("{")
        end = agent_output.rfind("}")
        if start >= 0 and end > start:
            json_text = agent_output[start:end + 1]

    if json_text is None:
        log("  coordinator: no JSON found in coordination plan output")
        return None

    try:
        plan = json.loads(json_text)
    except json.JSONDecodeError as exc:
        log(f"  coordinator: JSON parse error in coordination plan: {exc}")
        return None

    # Validate structure
    if "groups" not in plan or not isinstance(plan["groups"], list):
        log("  coordinator: coordination plan missing 'groups' array")
        return None

    # Validate all problem indices are covered exactly once
    seen_indices: set[int] = set()
    n = len(problems)
    for g in plan["groups"]:
        if "problems" not in g or not isinstance(g["problems"], list):
            log("  coordinator: group missing 'problems' array")
            return None
        for idx in g["problems"]:
            if not isinstance(idx, int) or idx < 0 or idx >= n:
                log(f"  coordinator: invalid problem index {idx}")
                return None
            if idx in seen_indices:
                log(f"  coordinator: duplicate problem index {idx}")
                return None
            seen_indices.add(idx)

    if len(seen_indices) != n:
        missing = set(range(n)) - seen_indices
        log(f"  coordinator: coordination plan missing indices: {missing}")
        return None

    return plan


def write_coordination_plan_prompt(
    problems: list[dict[str, Any]], planspace: Path,
) -> Path:
    """Write an Opus prompt to plan coordination strategy for problems.

    The coordination-planner agent receives the full problem list and
    produces a JSON plan with groups, strategies, and execution order.
    The script then executes the plan mechanically.
    """
    artifacts = planspace / "artifacts" / "coordination"
    artifacts.mkdir(parents=True, exist_ok=True)
    prompt_path = artifacts / "coordination-plan-prompt.md"

    # Write problems as JSON for the agent
    problems_json = json.dumps(problems, indent=2)

    prompt_path.write_text(f"""# Task: Plan Coordination Strategy

## Outstanding Problems

```json
{problems_json}
```

## Instructions

You are the coordination planner. Read the problems above and produce
a JSON coordination plan. Think strategically about problem relationships —
don't just match files. Understand whether problems share root causes,
whether fixing one affects another, and what order minimizes rework.

Reply with a JSON block:

```json
{{
  "groups": [
    {{
      "problems": [0, 1],
      "reason": "Both problems stem from incomplete event model in config.py",
      "strategy": "sequential"
    }},
    {{
      "problems": [2],
      "reason": "Independent API endpoint issue",
      "strategy": "parallel"
    }}
  ],
  "execution_order": "Groups can run in parallel if files don't overlap.",
  "notes": "Optional observations about cross-group dependencies."
}}
```

Each group's `problems` array contains indices into the problems list above.
Every problem index (0 through {len(problems) - 1}) must appear in exactly
one group.

Strategy values:
- `sequential`: problems within this group must be fixed in order
- `parallel`: problems within this group can be fixed concurrently

The `execution_order` field describes how GROUPS relate to each other —
which groups can run in parallel and which must wait.
""", encoding="utf-8")
    _log_artifact(planspace, "prompt:coordination-plan")
    return prompt_path


def write_coordinator_fix_prompt(
    group: list[dict[str, Any]], planspace: Path, codespace: Path,
    group_id: int,
) -> Path:
    """Write a Codex prompt to fix a group of related problems.

    The prompt lists the grouped problems with section context, the
    affected files, and instructs the agent to fix ALL listed problems
    in a coordinated way.
    """
    artifacts = planspace / "artifacts" / "coordination"
    artifacts.mkdir(parents=True, exist_ok=True)
    prompt_path = artifacts / f"fix-{group_id}-prompt.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    problem_descriptions = []
    for i, p in enumerate(group):
        desc = (
            f"### Problem {i + 1} (Section {p['section']}, "
            f"type: {p['type']})\n"
            f"{p['description']}"
        )
        problem_descriptions.append(desc)
    problems_text = "\n\n".join(problem_descriptions)

    # Collect all unique files across the group
    all_files: list[str] = []
    seen: set[str] = set()
    for p in group:
        for f in p.get("files", []):
            if f not in seen:
                all_files.append(f)
                seen.add(f)

    file_list = "\n".join(f"- `{codespace / f}`" for f in all_files)

    # Collect section specs for context (include both actual spec and excerpts)
    section_nums = sorted({p["section"] for p in group})
    sec_dir = planspace / "artifacts" / "sections"
    section_specs = "\n".join(
        f"- Section {n} specification:"
        f" `{sec_dir / f'section-{n}.md'}`\n"
        f"  - Proposal excerpt:"
        f" `{sec_dir / f'section-{n}-proposal-excerpt.md'}`"
        for n in section_nums
    )
    alignment_specs = "\n".join(
        f"- Section {n} alignment excerpt:"
        f" `{sec_dir / f'section-{n}-alignment-excerpt.md'}`"
        for n in section_nums
    )

    codemap_path = planspace / "artifacts" / "codemap.md"
    codemap_block = ""
    if codemap_path.exists():
        codemap_block = (
            f"\n## Project Understanding\n"
            f"- Codemap: `{codemap_path}`\n"
        )

    # Include cross-section tools if any exist
    tools_block = ""
    tool_registry_path = planspace / "artifacts" / "tool-registry.json"
    if tool_registry_path.exists():
        try:
            reg = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            cross_tools = [
                t for t in (reg if isinstance(reg, list)
                            else reg.get("tools", []))
                if t.get("scope") == "cross-section"
            ]
            if cross_tools:
                tool_lines = "\n".join(
                    f"- `{t.get('path', '?')}`: {t.get('description', '')}"
                    for t in cross_tools
                )
                tools_block = (
                    f"\n## Available Cross-Section Tools\n{tool_lines}\n"
                )
        except (json.JSONDecodeError, ValueError):
            pass

    prompt_path.write_text(f"""# Task: Coordinated Fix for Problem Group {group_id}

## Problems to Fix

{problems_text}

## Affected Files
{file_list}

## Section Context
{section_specs}
{alignment_specs}
{codemap_block}{tools_block}
## Instructions

Fix ALL the problems listed above in a COORDINATED way. These problems
are related — they share files and/or have a common root cause. Fixing
them together avoids the cascade where fixing one problem in isolation
creates or re-triggers another.

### Strategy

1. **Explore first.** Before making changes, understand the full picture.
   Read the codemap if available to understand how these files fit into
   the broader project structure. Then dispatch GLM sub-agents to read
   files and understand context:
   ```bash
   uv run --frozen agents --model glm --project "{codespace}" "<instructions>"
   ```

2. **Plan holistically.** Consider how all the problems interact. A single
   coordinated change may fix multiple problems at once.

3. **Implement.** Make the changes. For targeted sub-tasks:
   ```bash
   uv run --frozen agents --model gpt-5.3-codex-high \\
     --project "{codespace}" "<instructions>"
   ```

4. **Verify.** After implementation, dispatch GLM to verify the fixes
   address all listed problems without introducing new issues.

### Report Modified Files

After implementation, write a list of ALL files you modified to:
`{modified_report}`

One file path per line (relative to codespace root `{codespace}`).
Include files modified by sub-agents.
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:coordinator-fix-{group_id}")
    return prompt_path


def _dispatch_fix_group(
    group: list[dict[str, Any]], group_id: int,
    planspace: Path, codespace: Path, parent: str,
) -> tuple[int, list[str] | None]:
    """Dispatch a Codex agent to fix a single problem group.

    Returns (group_id, list_of_modified_files) on success.
    Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.
    """
    artifacts = planspace / "artifacts" / "coordination"
    fix_prompt = write_coordinator_fix_prompt(
        group, planspace, codespace, group_id,
    )
    fix_output = artifacts / f"fix-{group_id}-output.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    # Check for model escalation (triggered by coordination churn)
    fix_model = "gpt-5.3-codex-high"
    escalation_file = artifacts / "model-escalation.txt"
    if escalation_file.exists():
        fix_model = escalation_file.read_text(encoding="utf-8").strip()
        log(f"  coordinator: using escalated model {fix_model}")

    log(f"  coordinator: dispatching fix for group {group_id} "
        f"({len(group)} problems)")
    result = dispatch_agent(
        fix_model, fix_prompt, fix_output,
        planspace, parent, codespace=codespace,
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return group_id, None  # Sentinel — caller must check

    # Collect modified files from the report (validated to be safe
    # relative paths under codespace — same logic as collect_modified_files)
    codespace_resolved = codespace.resolve()
    modified: list[str] = []
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    log(f"  coordinator: WARNING — fix path outside "
                        f"codespace, skipping: {line}")
                    continue
            else:
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    log(f"  coordinator: WARNING — fix path escapes "
                        f"codespace, skipping: {line}")
                    continue
            modified.append(str(rel))
    return group_id, modified


def run_global_coordination(
    sections: list[Section],
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> bool:
    """Run the global problem coordinator.

    Collects outstanding problems across all sections, groups related
    problems, dispatches coordinated fixes, and re-runs alignment on
    affected sections.

    Returns True if all sections are ALIGNED (or no problems remain).
    """
    coord_dir = planspace / "artifacts" / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------
    # Step 1: Collect all outstanding problems
    # -----------------------------------------------------------------
    problems = _collect_outstanding_problems(
        section_results, sections_by_num, planspace,
    )

    if not problems:
        log("  coordinator: no outstanding problems — all ALIGNED")
        return True

    log(f"  coordinator: {len(problems)} outstanding problems across "
        f"{len({p['section'] for p in problems})} sections")

    # Save coordination state for debugging / inspection
    state_path = coord_dir / "problems.json"
    state_path.write_text(json.dumps(problems, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:problems")

    # -----------------------------------------------------------------
    # Step 2: Dispatch coordination-planner agent to group problems
    # -----------------------------------------------------------------
    ctrl = poll_control_messages(planspace, parent)
    if ctrl == "alignment_changed":
        return False

    plan_prompt = write_coordination_plan_prompt(problems, planspace)
    plan_output = coord_dir / "coordination-plan-output.md"
    log("  coordinator: dispatching coordination-planner agent")
    plan_result = dispatch_agent(
        "claude-opus", plan_prompt, plan_output,
        planspace, parent, agent_file="coordination-planner.md",
    )
    if plan_result == "ALIGNMENT_CHANGED_PENDING":
        return False

    # Parse the JSON coordination plan from agent output
    coord_plan = _parse_coordination_plan(plan_result, problems)
    if coord_plan is None:
        # Fallback: treat each problem as its own group, sequential
        log("  coordinator: WARNING — could not parse coordination plan, "
            "falling back to one-problem-per-group")
        coord_plan = {
            "groups": [
                {"problems": [i], "reason": "fallback", "strategy": "parallel"}
                for i in range(len(problems))
            ],
            "execution_order": "all sequential (fallback)",
        }

    # Build confirmed groups from the plan
    confirmed_groups: list[list[dict[str, Any]]] = []
    group_strategies: list[str] = []
    for g in coord_plan["groups"]:
        group_problems = [problems[i] for i in g["problems"]]
        confirmed_groups.append(group_problems)
        group_strategies.append(g.get("strategy", "sequential"))
        log(f"  coordinator: group {len(confirmed_groups) - 1} — "
            f"{len(group_problems)} problems, "
            f"strategy={group_strategies[-1]}, "
            f"reason={g.get('reason', '(none)')}")

    log(f"  coordinator: {len(confirmed_groups)} problem groups from "
        f"coordination plan")

    # Save plan and groups for debugging
    plan_path = coord_dir / "coordination-plan.json"
    plan_path.write_text(json.dumps(coord_plan, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:plan")

    groups_path = coord_dir / "groups.json"
    groups_data = []
    for i, g in enumerate(confirmed_groups):
        groups_data.append({
            "group_id": i,
            "problem_count": len(g),
            "strategy": group_strategies[i],
            "sections": sorted({p["section"] for p in g}),
            "files": sorted({f for p in g for f in p.get("files", [])}),
        })
    groups_path.write_text(json.dumps(groups_data, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:groups")

    # -----------------------------------------------------------------
    # Step 3: Execute the coordination plan
    # -----------------------------------------------------------------
    # Identify which groups can run in parallel (disjoint file sets)
    # and which must be sequential (overlapping files). The agent's
    # execution_order notes inform us, but we enforce file safety.
    group_file_sets = [
        set(f for p in g for f in p.get("files", []))
        for g in confirmed_groups
    ]

    # Build safe parallel batches: groups with disjoint files
    batches: list[list[int]] = []
    for gidx, files in enumerate(group_file_sets):
        if not files:
            # Unknown scope — isolate
            batches.append([gidx])
            continue
        placed = False
        for batch in batches:
            batch_files = set()
            for bidx in batch:
                batch_files |= group_file_sets[bidx]
            if not batch_files:
                continue
            if not (files & batch_files):
                batch.append(gidx)
                placed = True
                break
        if not placed:
            batches.append([gidx])

    log(f"  coordinator: {len(batches)} execution batches")

    all_modified: list[str] = []
    for batch_num, batch in enumerate(batches):
        ctrl = poll_control_messages(planspace, parent)
        if ctrl == "alignment_changed":
            return False

        # Bridge agent: dispatch for groups with multi-section friction
        # (multiple sections contending over shared files)
        for gidx in batch:
            group = confirmed_groups[gidx]
            group_sections = sorted({p["section"] for p in group})
            group_files = sorted({
                f for p in group for f in p.get("files", [])})
            if len(group_sections) >= 2 and len(group_files) >= 1:
                bridge_prompt = (
                    coord_dir / f"bridge-{gidx}-prompt.md")
                bridge_output = (
                    coord_dir / f"bridge-{gidx}-output.md")
                contract_path = (
                    coord_dir / f"contract-patch-{gidx}.md")
                sec_dir = planspace / "artifacts" / "sections"
                sec_refs = "\n".join(
                    f"- Section {s}: `{sec_dir / f'section-{s}-proposal-excerpt.md'}`"
                    for s in group_sections
                )
                proposals_dir = planspace / "artifacts" / "proposals"
                prop_refs = "\n".join(
                    f"- `{proposals_dir / f'section-{s}-integration-proposal.md'}`"
                    for s in group_sections
                )
                bridge_prompt.write_text(
                    f"# Bridge: Resolve Cross-Section Friction "
                    f"(Group {gidx})\n\n"
                    f"## Sections in Conflict\n{sec_refs}\n\n"
                    f"## Integration Proposals\n{prop_refs}\n\n"
                    f"## Shared Files\n"
                    + "\n".join(f"- `{f}`" for f in group_files)
                    + f"\n\n## Output\n"
                    f"Write your contract patch to: `{contract_path}`\n"
                    f"Write per-section consequence notes to:\n"
                    + "\n".join(
                        f"- `{planspace / 'artifacts' / 'notes' / f'bridge-{gidx}-to-{s}.md'}`"
                        for s in group_sections
                    ) + "\n",
                    encoding="utf-8",
                )
                log(f"  coordinator: dispatching bridge agent for group "
                    f"{gidx} ({group_sections})")
                dispatch_agent(
                    "gpt-5.3-codex-xhigh", bridge_prompt,
                    bridge_output, planspace, parent,
                    codespace=codespace,
                    agent_file="bridge-agent.md",
                )

        if len(batch) == 1:
            gidx = batch[0]
            _, modified = _dispatch_fix_group(
                confirmed_groups[gidx], gidx,
                planspace, codespace, parent,
            )
            if modified is None:
                return False
            all_modified.extend(modified)
        else:
            log(f"  coordinator: batch {batch_num} — "
                f"{len(batch)} groups in parallel")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(
                        _dispatch_fix_group,
                        confirmed_groups[gidx], gidx,
                        planspace, codespace, parent,
                    ): gidx
                    for gidx in batch
                }
                sentinel_hit = False
                for future in as_completed(futures):
                    gidx = futures[future]
                    try:
                        _, modified = future.result()
                        if modified is None:
                            sentinel_hit = True
                            continue
                        all_modified.extend(modified)
                        log(f"  coordinator: group {gidx} fix "
                            f"complete ({len(modified)} files "
                            f"modified)")
                    except Exception as exc:
                        log(f"  coordinator: group {gidx} fix "
                            f"FAILED: {exc}")
            if sentinel_hit:
                return False

    log(f"  coordinator: fixes complete, "
        f"{len(all_modified)} total files modified")

    # -----------------------------------------------------------------
    # Step 4: Re-run per-section alignment on affected sections
    # -----------------------------------------------------------------
    # Determine which sections need re-checking:
    # sections that had problems + sections whose files were modified
    affected_sections: set[str] = set()

    # Sections that had problems
    for p in problems:
        affected_sections.add(p["section"])

    # Sections whose files were modified by the coordinator
    file_to_sections = build_file_to_sections(sections)
    for mod_file in all_modified:
        for sec_num in file_to_sections.get(mod_file, []):
            affected_sections.add(sec_num)

    log(f"  coordinator: re-checking alignment for sections "
        f"{sorted(affected_sections)}")

    # Incremental alignment: track per-section input hashes to skip
    # unchanged sections
    inputs_hash_dir = coord_dir / "inputs-hashes"
    inputs_hash_dir.mkdir(parents=True, exist_ok=True)

    for sec_num in sorted(affected_sections):
        section = sections_by_num.get(sec_num)
        if not section:
            continue

        # Compute inputs hash for this section
        sec_artifacts = planspace / "artifacts"
        hash_sources = [
            sec_artifacts / "sections"
            / f"section-{sec_num}-alignment-excerpt.md",
            sec_artifacts / "proposals"
            / f"section-{sec_num}-integration-proposal.md",
        ]
        hasher = hashlib.sha256()
        for hp in hash_sources:
            if hp.exists():
                hasher.update(hp.read_bytes())
        # Include incoming notes hash
        notes_dir = planspace / "artifacts" / "notes"
        if notes_dir.exists():
            for note_path in sorted(notes_dir.glob(f"from-*-to-{sec_num}.md")):
                hasher.update(note_path.read_bytes())
        # Include modified files hash (coordinator may have changed files)
        for mod_f in sorted(all_modified):
            mod_path = codespace / mod_f
            if mod_path.exists():
                hasher.update(mod_path.read_bytes())
        current_hash = hasher.hexdigest()

        prev_hash_file = inputs_hash_dir / f"section-{sec_num}.hash"
        if prev_hash_file.exists():
            prev_hash = prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash == current_hash:
                log(f"  coordinator: section {sec_num} inputs unchanged "
                    f"— skipping alignment recheck")
                continue
        prev_hash_file.write_text(current_hash, encoding="utf-8")

        # Poll for control messages before each re-check
        ctrl = poll_control_messages(planspace, parent, sec_num)
        if ctrl == "alignment_changed":
            log("  coordinator: alignment changed — aborting re-checks")
            return False

        # Read any incoming notes for this section (cross-section context)
        notes = read_incoming_notes(section, planspace, codespace)
        if notes:
            log(f"  coordinator: section {sec_num} has incoming notes "
                f"from other sections")

        # Re-run implementation alignment check with TIMEOUT retry
        align_result = _run_alignment_check_with_retries(
            section, planspace, codespace, parent, sec_num,
            output_prefix="coord-align",
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return False  # Let outer loop restart Phase 1
        if align_result is None:
            # All retries timed out
            log(f"  coordinator: section {sec_num} alignment check "
                f"timed out after retries")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="alignment check timed out after retries",
            )
            continue

        align_problems = _extract_problems(align_result)
        coord_signal_dir = coord_dir / "signals"
        coord_signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=(coord_signal_dir
                         / f"coord-align-{sec_num}-signal.json"),
            output_path=coord_dir / f"coord-align-{sec_num}-output.md",
            planspace=planspace, parent=parent, codespace=codespace,
        )

        if align_problems is None and signal is None:
            log(f"  coordinator: section {sec_num} now ALIGNED")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
            )
        else:
            log(f"  coordinator: section {sec_num} still has problems")
            # Fold signal info into problems string (SectionResult has
            # no signal fields — only problems)
            combined_problems = align_problems or ""
            if signal:
                combined_problems += (
                    f"\n[signal:{signal}] {detail}" if combined_problems
                    else f"[signal:{signal}] {detail}"
                )
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems=combined_problems or None,
            )

    # Check if everything is now aligned
    remaining = [r for r in section_results.values() if not r.aligned]
    if not remaining:
        log("  coordinator: all sections now ALIGNED")
        return True

    log(f"  coordinator: {len(remaining)} sections still not aligned")
    return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the section loop orchestrator CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Section loop orchestrator for the implementation pipeline.",
    )
    parser.add_argument("planspace", type=Path,
                        help="Path to the planspace directory")
    parser.add_argument("codespace", type=Path,
                        help="Path to the codespace directory")
    parser.add_argument("--global-proposal", type=Path, required=True,
                        dest="global_proposal",
                        help="Path to the global proposal document")
    parser.add_argument("--global-alignment", type=Path, required=True,
                        dest="global_alignment",
                        help="Path to the global alignment document")
    parser.add_argument("--parent", type=str, default="orchestrator",
                        help="Parent agent mailbox name (default: orchestrator)")

    args = parser.parse_args()

    # Validate paths
    if not args.global_proposal.exists():
        print(f"Error: global proposal not found: {args.global_proposal}")
        sys.exit(1)
    if not args.global_alignment.exists():
        print(f"Error: global alignment not found: {args.global_alignment}")
        sys.exit(1)

    sections_dir = args.planspace / "artifacts" / "sections"

    # Initialize coordination DB (idempotent) and register
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "init", str(args.planspace / "run.db")],  # noqa: S607
        check=True, capture_output=True, text=True,
    )
    mailbox_register(args.planspace)
    log(f"Registered: {AGENT_NAME} (parent: {args.parent})")

    try:
        _run_loop(args.planspace, args.codespace, args.parent, sections_dir,
                  args.global_proposal, args.global_alignment)
    finally:
        mailbox_cleanup(args.planspace)
        log("Mailbox cleaned up")


def _run_loop(planspace: Path, codespace: Path, parent: str,
              sections_dir: Path, global_proposal: Path,
              global_alignment: Path) -> None:
    # Project mode (greenfield vs brownfield) is determined by the
    # codemap agent during Stage 3 scan.sh. The mode file is written
    # to artifacts/project-mode.txt by the codemap agent — not by
    # hardcoded script logic. If it doesn't exist yet, default to
    # brownfield (the safe assumption).
    mode_file = planspace / "artifacts" / "project-mode.txt"
    project_mode = "brownfield"
    if mode_file.exists():
        project_mode = mode_file.read_text(encoding="utf-8").strip()
    log(f"Project mode: {project_mode} (from {'codemap agent' if mode_file.exists() else 'default'})")

    # Load sections and build cross-reference map
    all_sections = load_sections(sections_dir)

    # Attach global document paths to each section
    for sec in all_sections:
        sec.global_proposal_path = global_proposal
        sec.global_alignment_path = global_alignment

    sections_by_num = {s.number: s for s in all_sections}

    log(f"Loaded {len(all_sections)} sections")

    # Outer loop: alignment_changed during Phase 2 restarts from Phase 1.
    # Each iteration runs Phase 1 (per-section) then Phase 2 (global).
    # The loop exits on: complete, fail, abort, or exhaustion.
    while True:

        # -----------------------------------------------------------------
        # Phase 1: Initial pass through all sections
        # -----------------------------------------------------------------
        section_results: dict[str, SectionResult] = {}
        queue = [s.number for s in all_sections]
        completed: set[str] = set()

        while queue:
            # Check for abort or alignment changes before each section
            if handle_pending_messages(planspace, queue, completed):
                log("Aborted by parent")
                mailbox_send(planspace, parent, "fail:aborted")
                return

            # If alignment_changed flag is already pending (set by
            # handle_pending_messages above or a prior run_section),
            # skip directly to the _check_and_clear below instead of
            # wasting an Opus setup call.
            if alignment_changed_pending(planspace):  # noqa: SIM102
                # Clear the flag and requeue all completed sections so
                # they re-run setup with fresh excerpts.
                if _check_and_clear_alignment_changed(planspace):
                    log("Alignment changed (pre-section) — "
                        "requeuing all completed sections")
                    for done_num in list(completed):
                        completed.discard(done_num)
                        if done_num not in queue:
                            queue.append(done_num)
                    continue

            sec_num = queue.pop(0)

            if sec_num in completed:
                continue

            section = sections_by_num[sec_num]
            section.solve_count += 1
            log(f"=== Section {sec_num} ({len(queue)} remaining) "
                f"[round {section.solve_count}] ===")
            # Emit section lifecycle start event for QA monitor rule A6
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
                 "lifecycle", f"start:section:{sec_num}",
                 f"round {section.solve_count}",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

            if not section.related_files:
                # Agent-driven re-exploration: dispatch an Opus agent to
                # investigate why the section has no files and determine
                # whether it's greenfield, brownfield-missed, or hybrid.
                log(f"Section {sec_num}: no related files — dispatching "
                    f"re-explorer agent")
                reexplore_result = _reexplore_section(
                    section, planspace, codespace, parent,
                )
                if reexplore_result == "ALIGNMENT_CHANGED_PENDING":
                    if _check_and_clear_alignment_changed(planspace):
                        for done_num in list(completed):
                            completed.discard(done_num)
                            if done_num not in queue:
                                queue.append(done_num)
                        if sec_num not in queue:
                            queue.insert(0, sec_num)
                    continue
                # Extract and persist section_mode from re-explorer output
                reexplore_output_path = (
                    planspace / "artifacts"
                    / f"reexplore-{section.number}-output.md")
                section_mode = "brownfield"  # default
                if reexplore_output_path.exists():
                    reexplore_text = reexplore_output_path.read_text(
                        encoding="utf-8").lower()
                    if "section_mode: greenfield" in reexplore_text:
                        section_mode = "greenfield"
                    elif "section_mode: hybrid" in reexplore_text:
                        section_mode = "hybrid"
                mode_path = (planspace / "artifacts" / "sections"
                             / f"section-{section.number}-mode.txt")
                mode_path.parent.mkdir(parents=True, exist_ok=True)
                mode_path.write_text(section_mode, encoding="utf-8")
                log(f"Section {sec_num}: mode = {section_mode}")

                # Re-parse related files (agent may have appended them)
                section.related_files = parse_related_files(section.path)
                if not section.related_files:
                    # Still no files — agent declared greenfield or
                    # couldn't find matches. Mark as needing research,
                    # not silently "aligned".
                    log(f"Section {sec_num}: re-explorer found no files "
                        f"(greenfield / needs research)")
                    completed.add(sec_num)
                    section_results[sec_num] = SectionResult(
                        section_number=sec_num, aligned=True,
                        problems="greenfield — no existing code to "
                                 "integrate with",
                    )
                    mailbox_send(planspace, parent,
                                 f"done:{sec_num}:0 files modified "
                                 f"(greenfield)")
                    subprocess.run(  # noqa: S603
                        ["bash", str(DB_SH), "log",  # noqa: S607
                         str(planspace / "run.db"),
                         "lifecycle", f"end:section:{sec_num}",
                         "done (greenfield — re-explored)",
                         "--agent", AGENT_NAME],
                        capture_output=True, text=True,
                    )
                    continue
                log(f"Section {sec_num}: re-explorer found "
                    f"{len(section.related_files)} files — continuing")

            # Run the section
            modified_files = run_section(
                planspace, codespace, section, parent,
                all_sections=all_sections,
            )

            # Check if alignment_changed arrived during run_section
            # (via handle_pending_messages or pause_for_parent)
            if _check_and_clear_alignment_changed(planspace):
                log("Alignment changed during section processing — "
                    "requeuing all completed sections")
                for done_num in list(completed):
                    completed.discard(done_num)
                    if done_num not in queue:
                        queue.append(done_num)
                # Re-add current section to front of queue
                if sec_num not in queue:
                    queue.insert(0, sec_num)
                continue

            if modified_files is None:
                # Section was paused and parent told us to stop
                log(f"Section {sec_num}: paused, exiting")
                subprocess.run(  # noqa: S603
                    ["bash", str(DB_SH), "log",  # noqa: S607
                     str(planspace / "run.db"),
                     "lifecycle", f"end:section:{sec_num}", "failed",
                     "--agent", AGENT_NAME],
                    capture_output=True, text=True,
                )
                return

            completed.add(sec_num)
            mailbox_send(planspace, parent,
                         f"done:{sec_num}:{len(modified_files)} files "
                         f"modified")

            # Record result — section passed its internal alignment
            # loop, so it's initially ALIGNED. The coordinator may find
            # cross-section issues later.
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
                modified_files=modified_files,
            )

            log(f"Section {sec_num}: done")
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log",  # noqa: S607
                 str(planspace / "run.db"),
                 "lifecycle", f"end:section:{sec_num}", "done",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

        log(f"=== Phase 1 complete: {len(completed)} sections "
            f"processed ===")

        # -------------------------------------------------------------
        # Phase 2: Global coordination loop
        # -------------------------------------------------------------
        # Re-run alignment on ALL sections to get a global snapshot.
        # Sections may have been individually aligned but cross-section
        # changes (shared files modified by later sections) can
        # introduce problems invisible during the initial pass.
        log("=== Phase 2: global coordination ===")
        log("Re-checking alignment across all sections...")

        restart_phase1 = False
        for sec_num, section in sections_by_num.items():
            if not section.related_files:
                continue

            # Poll for control messages before each dispatch
            ctrl = poll_control_messages(planspace, parent, sec_num)
            if ctrl == "alignment_changed":
                log("Alignment changed during Phase 2 — restarting "
                    "from Phase 1")
                restart_phase1 = True
                break

            # Read incoming notes for cross-section awareness
            notes = read_incoming_notes(section, planspace, codespace)
            if notes:
                log(f"Section {sec_num}: has incoming notes for global "
                    f"alignment check")

            # Alignment check with TIMEOUT retry (max 2 retries)
            align_result = _run_alignment_check_with_retries(
                section, planspace, codespace, parent, sec_num,
                output_prefix="global-align",
            )
            if align_result == "ALIGNMENT_CHANGED_PENDING":
                # Alignment changed mid-check — let outer loop restart
                restart_phase1 = True
                break
            if align_result is None:
                # All retries timed out
                log(f"Section {sec_num}: global alignment check timed "
                    f"out after retries")
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=False,
                    problems="alignment check timed out after retries",
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )
                continue

            problems = _extract_problems(align_result)
            main_signal_dir = (planspace / "artifacts" / "signals")
            main_signal_dir.mkdir(parents=True, exist_ok=True)
            signal, detail = check_agent_signals(
                align_result,
                signal_path=(main_signal_dir
                             / f"global-align-{sec_num}-signal.json"),
                output_path=(planspace / "artifacts"
                             / f"global-align-{sec_num}-output.md"),
                planspace=planspace, parent=parent, codespace=codespace,
            )

            if problems is None and signal is None:
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=True,
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )
            else:
                log(f"Section {sec_num}: global alignment found "
                    f"problems")
                combined_problems = problems or ""
                if signal:
                    combined_problems += (
                        f"\n[signal:{signal}] {detail}"
                        if combined_problems
                        else f"[signal:{signal}] {detail}"
                    )
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=False,
                    problems=combined_problems or None,
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )

        if restart_phase1:
            continue  # outer while True → restart Phase 1

        # Check if everything is already aligned
        misaligned = [
            r for r in section_results.values() if not r.aligned
        ]
        if not misaligned:
            # Final control-message drain — catch alignment_changed or
            # abort that arrived during the last dispatch.
            ctrl = poll_control_messages(planspace, parent)
            if ctrl == "alignment_changed":
                log("Alignment changed just before completion — "
                    "restarting from Phase 1")
                continue  # outer while True → restart Phase 1
            log("=== All sections ALIGNED after initial pass ===")
            mailbox_send(planspace, parent, "complete")
            return

        log(f"{len(misaligned)} sections need coordination: "
            f"{sorted(r.section_number for r in misaligned)}")

        # Run the coordinator loop (adaptive: continues while improving)
        prev_unresolved = len(misaligned)
        stall_count = 0
        round_num = 0
        while round_num < MAX_COORDINATION_ROUNDS:
            round_num += 1
            # Poll for control messages before each round
            ctrl = poll_control_messages(planspace, parent)
            if ctrl == "alignment_changed":
                log("Alignment changed during coordination — "
                    "restarting from Phase 1")
                restart_phase1 = True
                break

            log(f"=== Coordination round {round_num} "
                f"(prev unresolved: {prev_unresolved}) ===")
            mailbox_send(planspace, parent,
                         f"status:coordination:round-{round_num}")

            all_done = run_global_coordination(
                all_sections, section_results, sections_by_num,
                planspace, codespace, parent,
            )

            # Check if alignment_changed was received during
            # coordination (consumed inside run_global_coordination,
            # which sets the flag file)
            if _check_and_clear_alignment_changed(planspace):
                log("Alignment changed during coordination — "
                    "restarting from Phase 1")
                restart_phase1 = True
                break

            if all_done:
                # Final control-message drain — catch alignment_changed
                # or abort that arrived during the last dispatch.
                ctrl = poll_control_messages(planspace, parent)
                if ctrl == "alignment_changed":
                    log("Alignment changed just before completion — "
                        "restarting from Phase 1")
                    restart_phase1 = True
                    break
                log(f"=== All sections ALIGNED after coordination "
                    f"round {round_num} ===")
                mailbox_send(planspace, parent, "complete")
                return

            remaining = [
                r for r in section_results.values() if not r.aligned
            ]
            cur_unresolved = len(remaining)
            log(f"Coordination round {round_num}: "
                f"{cur_unresolved} sections still unresolved "
                f"(was {prev_unresolved})")

            # Adaptive termination: stop if not making progress
            if cur_unresolved >= prev_unresolved:
                stall_count += 1
                if stall_count == 2:
                    # Escalation on churn: flag for stronger model on
                    # next round's coordination fixes
                    log(f"Coordination churning ({stall_count} rounds "
                        f"without improvement) — escalating model")
                    escalation_file = (
                        planspace / "artifacts" / "coordination"
                        / "model-escalation.txt"
                    )
                    escalation_file.write_text(
                        "gpt-5.3-codex-xhigh", encoding="utf-8")
                    mailbox_send(planspace, parent,
                                 f"escalation:coordination:"
                                 f"round-{round_num}:stall_count="
                                 f"{stall_count}")
                if round_num >= MIN_COORDINATION_ROUNDS and stall_count >= 3:
                    log(f"Coordination stalled ({stall_count} rounds "
                        f"without improvement) — stopping")
                    break
            else:
                stall_count = 0  # reset on progress

            prev_unresolved = cur_unresolved

        if not restart_phase1:
            # Coordination exhausted or stalled — do NOT send "complete".
            remaining = [
                r for r in section_results.values() if not r.aligned
            ]
            if remaining:
                log(f"=== Coordination finished after {round_num} rounds, "
                    f"{len(remaining)} sections still unresolved ===")
                for r in remaining:
                    summary = (r.problems or "unknown")[:120]
                    log(f"  - Section {r.section_number}: {summary}")
                    mailbox_send(
                        planspace, parent,
                        f"fail:{r.section_number}:"
                        f"coordination_exhausted:{summary}",
                    )
                return  # exhausted — exit

        if restart_phase1:
            continue  # outer while True → restart Phase 1

        # If we reach here without restart, we're done
        return


if __name__ == "__main__":
    main()
