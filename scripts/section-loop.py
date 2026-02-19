#!/usr/bin/env python3
"""Section loop orchestrator for the implementation pipeline.

Manages the per-section execution cycle: solution → plan/implement per file
→ alignment check → rescheduling. Handles dynamic queue management,
alignment branching, cross-section rescheduling, and underspecification
routing via mailbox.

All agent dispatches run as background subprocesses. The script communicates
with its parent (the orchestrator or interactive session) via mailbox
messages. When paused (waiting for user input, research results, etc.),
the script blocks on its own mailbox recv until the parent sends a resume.

Mail protocol (sent TO parent mailbox):
    summary:solve:<section>:<text>
    summary:plan:<section>:<file>:<text>
    summary:impl:<section>:<file>:<text>
    summary:align:<section>:<text>
    status:reschedule:<section>:<targets>
    done:<section>:<summary>
    fail:<section>:<error>
    complete
    pause:underspec:<section>:<description>
    pause:need_decision:<section>:<question>
    pause:dependency:<section>:<needed_section>

Mail protocol (received FROM parent mailbox):
    resume:<payload>          — continue with answer/result
    abort                     — clean shutdown
    alignment_changed         — user input changed alignment, re-evaluate

Pipeline state file (<planspace>/pipeline-state):
    running   — normal execution (default if file missing)
    paused    — finish current agent, then wait
    Checked between each agent dispatch. Monitor or orchestrator writes
    this file to control the pipeline.

Multi-tier monitoring:
    - Each dispatched agent gets mail instructions + a per-agent GLM monitor
    - Agents narrate actions via mailbox (plan:/done:/LOOP_DETECTED:)
    - Per-agent monitors detect loops within a single agent dispatch
    - Task-level monitor detects cycles across sections (alignment stuck,
      rescheduling cycles)

Usage:
    section-loop.py <planspace> <codespace>

Requires:
    - Section files in <planspace>/artifacts/sections/section-*.md
    - Each section file has ## Related Files with ### <filepath> entries
    - uv run agents available for dispatching models
    - mailbox.sh available at $WORKFLOW_HOME/scripts/mailbox.sh
"""
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

WORKFLOW_HOME = Path(os.environ.get(
    "WORKFLOW_HOME",
    Path(__file__).resolve().parent.parent,
))
MAILBOX = WORKFLOW_HOME / "scripts" / "mailbox.sh"
AGENT_NAME = "section-loop"


@dataclass
class Section:
    number: str  # e.g., "01"
    path: Path
    related_files: list[str] = field(default_factory=list)
    solve_count: int = 0


def log(msg: str) -> None:
    print(f"[section-loop] {msg}", flush=True)


def mailbox_send(planspace: Path, target: str, message: str) -> None:
    """Send a message to a target mailbox."""
    subprocess.run(
        ["bash", str(MAILBOX), "send", str(planspace), target, message],
        check=True,
        capture_output=True,
        text=True,
    )
    log(f"  mail → {target}: {message[:80]}")


def mailbox_recv(planspace: Path, timeout: int = 0) -> str:
    """Block until a message arrives in our mailbox. Returns message text."""
    log(f"  mail ← waiting (timeout={timeout})...")
    result = subprocess.run(
        ["bash", str(MAILBOX), "recv", str(planspace), AGENT_NAME,
         str(timeout)],
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
    result = subprocess.run(
        ["bash", str(MAILBOX), "drain", str(planspace), AGENT_NAME],
        capture_output=True,
        text=True,
    )
    msgs = []
    for chunk in result.stdout.split("---"):
        chunk = chunk.strip()
        if chunk:
            msgs.append(chunk)
    return msgs


def mailbox_register(planspace: Path) -> None:
    subprocess.run(
        ["bash", str(MAILBOX), "register", str(planspace), AGENT_NAME],
        check=True, capture_output=True, text=True,
    )


def mailbox_cleanup(planspace: Path) -> None:
    subprocess.run(
        ["bash", str(MAILBOX), "cleanup", str(planspace), AGENT_NAME],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["bash", str(MAILBOX), "unregister", str(planspace), AGENT_NAME],
        capture_output=True, text=True,
    )


def check_pipeline_state(planspace: Path) -> str:
    """Read the pipeline state file. Returns 'running' if missing."""
    state_file = planspace / "pipeline-state"
    if state_file.exists():
        return state_file.read_text().strip()
    return "running"


def wait_if_paused(planspace: Path, parent: str) -> None:
    """Block if pipeline is paused. Polls until state returns to running."""
    state = check_pipeline_state(planspace)
    if state != "paused":
        return
    log("Pipeline paused — waiting for resume")
    mailbox_send(planspace, parent, "status:paused")
    while check_pipeline_state(planspace) == "paused":
        # Also check mailbox for abort while paused
        for msg in mailbox_drain(planspace):
            if msg.startswith("abort"):
                log("Received abort while paused — shutting down")
                mailbox_cleanup(planspace)
                sys.exit(0)
        time.sleep(5)
    log("Pipeline resumed")
    mailbox_send(planspace, parent, "status:resumed")


def pause_for_parent(planspace: Path, parent: str, signal: str) -> str:
    """Send a pause signal to parent and block until we get a response."""
    mailbox_send(planspace, parent, signal)
    while True:
        msg = mailbox_recv(planspace, timeout=0)
        if msg.startswith("abort"):
            log("Received abort — shutting down")
            mailbox_cleanup(planspace)
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            log("Alignment changed — waiting for resume with updated context")
            continue
        return msg


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
            log("Alignment changed — marking all completed sections dirty")
            for sec_num in list(completed):
                completed.discard(sec_num)
                if sec_num not in queue:
                    queue.append(sec_num)
    return False


# ---------------------------------------------------------------------------
# Section file parsing
# ---------------------------------------------------------------------------

def parse_related_files(section_path: Path) -> list[str]:
    """Extract file paths from ## Related Files / ### <path> entries."""
    text = section_path.read_text()
    return re.findall(r'^### (.+)$', text, re.MULTILINE)


def load_sections(sections_dir: Path) -> list[Section]:
    """Load all section files and their related file maps."""
    sections = []
    for path in sorted(sections_dir.glob("section-*.md")):
        num = re.search(r'section-(\d+)', path.name)
        if not num:
            continue
        related = parse_related_files(path)
        sections.append(Section(number=num.group(1), path=path,
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
                   agent_name: str | None = None) -> str:
    """Run an agent via uv run agents and return the output text.

    If planspace and parent are provided, checks pipeline state before
    dispatching and waits if paused.

    If agent_name is provided, launches an agent-monitor alongside the
    agent to watch for loops and stuck states. The monitor is a GLM
    agent that reads the agent's mailbox.
    """
    if planspace and parent:
        wait_if_paused(planspace, parent)

    monitor_name = f"{agent_name}-monitor" if agent_name else None
    monitor_proc = None

    if planspace and agent_name:
        # Register agent's mailbox (agents send narration here)
        subprocess.run(
            ["bash", str(MAILBOX), "register", str(planspace), agent_name],
            check=True, capture_output=True, text=True,
        )
        # Launch agent-monitor (GLM) in background
        monitor_prompt = _write_agent_monitor_prompt(
            planspace, agent_name, monitor_name, parent or AGENT_NAME,
        )
        monitor_proc = subprocess.Popen(
            ["uv", "run", "agents", "--agent-file",
             str(WORKFLOW_HOME / "agents" / "agent-monitor.md"),
             "--file", str(monitor_prompt)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        log(f"  agent-monitor started (pid={monitor_proc.pid})")

    log(f"  dispatch {model} → {prompt_path.name}")
    result = subprocess.run(
        ["uv", "run", "agents", "--model", model, "--file", str(prompt_path)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = result.stdout + result.stderr
    output_path.write_text(output)
    if result.returncode != 0:
        log(f"  WARNING: agent returned {result.returncode}")

    # Shut down agent-monitor after agent finishes
    if monitor_proc:
        # Send stop signal to monitor
        subprocess.run(
            ["bash", str(MAILBOX), "send", str(planspace),
             monitor_name, "agent-finished"],
            capture_output=True, text=True,
        )
        try:
            monitor_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            monitor_proc.terminate()
        # Drain any loop detection messages the monitor sent
        loop_msgs = []
        drain_result = subprocess.run(
            ["bash", str(MAILBOX), "drain", str(planspace), AGENT_NAME],
            capture_output=True, text=True,
        )
        for chunk in drain_result.stdout.split("---"):
            chunk = chunk.strip()
            if chunk and "LOOP_DETECTED" in chunk:
                loop_msgs.append(chunk)
        # Clean up agent mailbox
        subprocess.run(
            ["bash", str(MAILBOX), "cleanup", str(planspace), agent_name],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["bash", str(MAILBOX), "unregister", str(planspace), agent_name],
            capture_output=True, text=True,
        )
        if loop_msgs:
            log(f"  LOOP DETECTED by monitor: {loop_msgs[0][:100]}")
            # Append loop signal to output so check_agent_signals catches it
            output += "\nLOOP_DETECTED: " + loop_msgs[0]

    return output


def _write_agent_monitor_prompt(
    planspace: Path, agent_name: str, monitor_name: str,
    escalation_target: str,
) -> Path:
    """Write the prompt file for a per-agent GLM monitor."""
    prompt_path = planspace / "artifacts" / f"{monitor_name}-prompt.md"
    prompt_path.write_text(f"""# Agent Monitor: {agent_name}

## Your Job
Watch mailbox `{agent_name}` for messages from a running agent.
Detect if the agent is looping (repeating the same actions).
Report loops to `{escalation_target}` via mailbox.

## Setup
```bash
bash "{MAILBOX}" register "{planspace}" {monitor_name}
```

## Paths
- Planspace: `{planspace}`
- Agent mailbox to watch: `{agent_name}`
- Your mailbox: `{monitor_name}`
- Escalation target: `{escalation_target}`

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

When loop detected:
```bash
bash "{MAILBOX}" send "{planspace}" {escalation_target} "LOOP_DETECTED:{agent_name}:<repeated action>"
```

## Exit Conditions
- Receive `agent-finished` on your mailbox → exit normally
- 5 minutes with no messages from agent → send stalled warning, exit
""")
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


def check_agent_signals(output: str) -> tuple[str | None, str]:
    """Check agent output for signals.

    Skips template placeholders (lines with <...>) to avoid false positives
    from SIGNAL_INSTRUCTIONS echoed in stdout.
    """
    for line in output.split("\n"):
        line = line.strip()
        for prefix, signal_type in [
            ("UNDERSPECIFIED:", "underspec"),
            ("NEED_DECISION:", "need_decision"),
            ("DEPENDENCY:", "dependency"),
            ("LOOP_DETECTED:", "loop_detected"),
        ]:
            if line.startswith(prefix):
                detail = line[len(prefix):].strip()
                # Skip template placeholders like <what information...>
                if detail.startswith("<") and detail.endswith(">"):
                    continue
                if detail:
                    return signal_type, detail
    return None, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_section_summary(section_path: Path) -> str:
    """Extract summary from YAML frontmatter of a section file."""
    text = section_path.read_text()
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


# ---------------------------------------------------------------------------
# Prompt builders (filepath-based — agents read files themselves)
# ---------------------------------------------------------------------------

SIGNAL_INSTRUCTIONS = """
## Signals (if you encounter problems)

If you cannot complete the task, output EXACTLY ONE of these on its own line:
UNDERSPECIFIED: <what information is missing and why you can't proceed>
NEED_DECISION: <what tradeoff or constraint question needs a human answer>
DEPENDENCY: <which other section must be implemented first and why>

Only use these if you truly cannot proceed. Otherwise complete the task.
"""


def agent_mail_instructions(planspace: Path, agent_name: str,
                            monitor_name: str) -> str:
    """Return narration-via-mailbox instructions for an agent."""
    mailbox_cmd = f'bash "{MAILBOX}" send "{planspace}" {monitor_name}'
    return f"""
## Progress Reporting (CRITICAL — do this throughout)

Your agent name: `{agent_name}`
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

This mail goes to a monitor that watches for problems. Do NOT skip it.
"""


def write_solution_prompt(
    planspace: Path, codespace: Path, section: Section,
) -> Path:
    """Write the prompt file for Stage 4 (Solution)."""
    artifacts = planspace / "artifacts"
    solutions_dir = artifacts / "solutions"
    solutions_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / f"solution-{section.number}-prompt.md"
    solution_path = solutions_dir / f"section-{section.number}-solution.md"
    a_name = f"solve-{section.number}"
    m_name = f"{a_name}-monitor"
    summary = extract_section_summary(section.path)

    file_list = []
    for rel_path in section.related_files:
        full_path = codespace / rel_path
        status = "" if full_path.exists() else " (to be created)"
        file_list.append(f"   - `{full_path}`{status}")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    existing_note = ""
    if solution_path.exists():
        existing_note = f"""
## Existing Solution
There is an existing solution from a previous round at: `{solution_path}`
Read it and update it to account for the current file states.
"""

    prompt_path.write_text(f"""# Task: Write Solution Doc for Section {section.number}

## Summary
{summary}

## Files to Read
1. Section specification: `{section.path}`
2. Related source files (read each one):
{files_block}
{existing_note}
## Instructions

Read the section specification and all related source files listed above.
Write a solution doc covering:
- How to approach the changes
- Per-file: what needs to change and why
- Constraints and risks
- Cross-section dependencies (which other sections' files are affected)

This is DIRECTION SETTING — not detailed code changes.

Write your solution to: `{solution_path}`
{SIGNAL_INSTRUCTIONS}
{agent_mail_instructions(planspace, a_name, m_name)}
""")
    return prompt_path


def write_plan_prompt(
    planspace: Path, codespace: Path, section: Section, rel_path: str,
    alignment_feedback: str | None = None,
) -> Path:
    """Write the prompt file for planning changes to one file."""
    artifacts = planspace / "artifacts"
    plans_dir = artifacts / "plans" / f"section-{section.number}"
    plans_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(rel_path).name
    prompt_path = artifacts / f"plan-{section.number}-{filename}-prompt.md"
    plan_path = plans_dir / f"{filename}-plan.md"
    a_name = f"plan-{section.number}-{filename}"
    m_name = f"{a_name}-monitor"

    solution_path = (artifacts / "solutions"
                     / f"section-{section.number}-solution.md")
    full_path = codespace / rel_path
    summary = extract_section_summary(section.path)

    if alignment_feedback and plan_path.exists():
        prompt_path.write_text(f"""# Task: Fix Change Plan for {rel_path}

## Summary
{summary}

## Alignment Feedback
{alignment_feedback}

## Files to Read
1. Existing plan to fix: `{plan_path}`
2. Solution doc: `{solution_path}`
3. Source file: `{full_path}`

## Instructions

The alignment check found issues. Read the existing plan and fix it
in place to address the alignment feedback. Only change the parts
that need fixing — preserve what's working.
{SIGNAL_INSTRUCTIONS}
{agent_mail_instructions(planspace, a_name, m_name)}
""")
    else:
        prompt_path.write_text(f"""# Task: Write Change Plan for {rel_path}

## Summary
{summary}

## Files to Read
1. Solution doc: `{solution_path}`
2. Section specification: `{section.path}`
3. Source file: `{full_path}`

## Instructions

Read all files listed above. Write a change plan for `{rel_path}` covering:
- Specific changes needed
- Interface contracts (function signatures, types)
- Control flow changes
- Error handling changes
- Integration points (what calls this, what this calls)

If the file needs no changes (read-only reference), write:
"No changes needed — read-only reference" and explain why.

Write your plan to: `{plan_path}`
{SIGNAL_INSTRUCTIONS}
{agent_mail_instructions(planspace, a_name, m_name)}
""")
    return prompt_path


def write_implement_prompt(
    planspace: Path, codespace: Path, section: Section, rel_path: str,
    alignment_feedback: str | None = None,
) -> Path:
    """Write the prompt file for implementing changes to one file."""
    artifacts = planspace / "artifacts"
    plans_dir = artifacts / "plans" / f"section-{section.number}"
    filename = Path(rel_path).name
    prompt_path = artifacts / f"impl-{section.number}-{filename}-prompt.md"
    a_name = f"impl-{section.number}-{filename}"
    m_name = f"{a_name}-monitor"

    plan_path = plans_dir / f"{filename}-plan.md"
    solution_path = (artifacts / "solutions"
                     / f"section-{section.number}-solution.md")
    full_path = codespace / rel_path

    if alignment_feedback:
        prompt_path.write_text(f"""# Task: Fix Implementation of {rel_path}

## Alignment Feedback
{alignment_feedback}

## Files to Read
1. Change plan (updated): `{plan_path}`
2. Solution doc: `{solution_path}`

## Instructions

The alignment check found issues. Fix the implementation in place to
match the solution and updated plan. Only change the parts that need
fixing — preserve what's working.

Source file to edit: `{full_path}`

After fixing, report which files you modified by writing a list to:
`{artifacts}/impl-{section.number}-{filename}-modified.txt`

One file path per line (relative to codespace root).
{SIGNAL_INSTRUCTIONS}
{agent_mail_instructions(planspace, a_name, m_name)}
""")
    else:
        prompt_path.write_text(f"""# Task: Implement Changes to {rel_path}

## Files to Read
1. Change plan: `{plan_path}`
2. Solution doc (context): `{solution_path}`

## Instructions

Read the change plan and solution doc. Implement the changes described
in the change plan.

1. Implement the changes described in the change plan
2. Update the module docstring to reflect the changes
3. Do NOT make changes beyond what the plan specifies

Source file to edit: `{full_path}`

After implementation, report which files you modified by writing a list to:
`{artifacts}/impl-{section.number}-{filename}-modified.txt`

One file path per line (relative to codespace root). Include `{rel_path}`
itself and any other files you had to touch.
{SIGNAL_INSTRUCTIONS}
{agent_mail_instructions(planspace, a_name, m_name)}
""")
    return prompt_path


def write_alignment_prompt(
    planspace: Path, codespace: Path, section: Section,
) -> Path:
    """Write the prompt for the alignment check."""
    artifacts = planspace / "artifacts"
    prompt_path = artifacts / f"align-{section.number}-prompt.md"

    solution_path = (artifacts / "solutions"
                     / f"section-{section.number}-solution.md")
    summary = extract_section_summary(section.path)

    # Merge section's related files with files Codex reported as modified
    # (handles new files created during implementation, e.g. .dockerignore)
    all_paths = set(section.related_files)
    modified = collect_modified_files(planspace, section)
    all_paths.update(modified)

    file_list = []
    for rel_path in sorted(all_paths):
        full_path = codespace / rel_path
        if full_path.exists():
            file_list.append(f"   - `{full_path}`")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    prompt_path.write_text(f"""# Task: Alignment Check for Section {section.number}

## Summary
{summary}

## Files to Read
1. Section specification: `{section.path}`
2. Solution doc: `{solution_path}`
3. Known implemented files (read each one):
{files_block}

## Worktree root
`{codespace}`

## Instructions

Read the section specification and solution doc first. Then read the
known implemented files listed above.

**Go beyond the file list.** The section spec may require creating new
files, modifying files not listed above, or producing artifacts at
specific paths. Check the worktree for any file the section mentions
that should exist. If the spec says "create X" — verify X exists and
has the right content. Do not limit your review to the listed files.

Check whether the implementations match the section's intent:
- Do the file changes fulfill ALL section requirements?
- Are the changes internally consistent across files?
- Did any implementation drift from what the solution specified?
- Do all files that should exist actually exist in the worktree?

Reply with EXACTLY one of:
ALIGNED
or
MISALIGNED: <description of what's wrong and what needs to change>
or
UNDERSPECIFIED: <what information is missing from the section/solution>

Nothing else.
""")
    return prompt_path


# ---------------------------------------------------------------------------
# Modified file collection
# ---------------------------------------------------------------------------

def collect_modified_files(planspace: Path, section: Section) -> list[str]:
    """Collect all modified file paths reported by implement agents."""
    artifacts = planspace / "artifacts"
    modified = set()
    for rel_path in section.related_files:
        filename = Path(rel_path).name
        mod_file = (artifacts
                    / f"impl-{section.number}-{filename}-modified.txt")
        if mod_file.exists():
            for line in mod_file.read_text().strip().split("\n"):
                line = line.strip()
                if line:
                    modified.add(line)
    return list(modified)


# ---------------------------------------------------------------------------
# Section execution with signal handling
# ---------------------------------------------------------------------------

def run_section(
    planspace: Path, codespace: Path, section: Section, parent: str,
) -> list[str] | None:
    """Run a section through solution → plan/implement → alignment.

    Returns modified files on success, or None if paused (waiting for
    parent to handle underspec/decision/dependency and send resume).
    """
    artifacts = planspace / "artifacts"

    # Stage 4: Solution (once per section, unless rescheduled)
    log(f"Section {section.number}: solving")
    prompt = write_solution_prompt(planspace, codespace, section)
    output_path = artifacts / f"solution-{section.number}-output.md"
    solve_agent = f"solve-{section.number}"
    output = dispatch_agent("claude-opus", prompt, output_path,
                            planspace, parent, solve_agent)
    mailbox_send(planspace, parent,
                 f"summary:solve:{section.number}:"
                 f"{summarize_output(output)}")

    signal, detail = check_agent_signals(output)
    if signal:
        response = pause_for_parent(
            planspace, parent,
            f"pause:{signal}:{section.number}:{detail}",
        )
        if not response.startswith("resume"):
            return None
        # Parent handled it — re-solve with updated context
        output = dispatch_agent("claude-opus", prompt, output_path,
                                planspace, parent, solve_agent)

    # Stage 5: Plan + Implement per file, with alignment loop
    alignment_feedback = None
    align_attempt = 0

    while True:
        for rel_path in section.related_files:
            filename = Path(rel_path).name

            # Plan
            tag = "fix " if alignment_feedback else ""
            log(f"Section {section.number}: {tag}plan {filename}")
            plan_prompt = write_plan_prompt(
                planspace, codespace, section, rel_path, alignment_feedback,
            )
            plan_output = (artifacts
                           / f"plan-{section.number}-{filename}-output.md")
            plan_agent = f"plan-{section.number}-{filename}"
            plan_result = dispatch_agent(
                "gpt-5.3-codex-high", plan_prompt, plan_output,
                planspace, parent, plan_agent,
            )
            mailbox_send(planspace, parent,
                         f"summary:{tag}plan:{section.number}:{rel_path}:"
                         f"{summarize_output(plan_result)}")

            signal, detail = check_agent_signals(plan_result)
            if signal:
                response = pause_for_parent(
                    planspace, parent,
                    f"pause:{signal}:{section.number}:{detail}",
                )
                if not response.startswith("resume"):
                    return None

            # Skip implement if plan says read-only (no changes needed)
            plans_dir = artifacts / "plans" / f"section-{section.number}"
            plan_file = plans_dir / f"{filename}-plan.md"
            if plan_file.exists() and \
                    "no changes needed" in plan_file.read_text().lower():
                log(f"Section {section.number}: skip impl (read-only) "
                    f"{rel_path}")
                # Write empty modified file so collect_modified_files()
                # doesn't report this file, preventing infinite
                # rescheduling cycles
                empty_mod = (artifacts
                             / f"impl-{section.number}-{filename}"
                               f"-modified.txt")
                empty_mod.write_text("")
                continue

            # Implement
            log(f"Section {section.number}: {tag}implement {filename}")
            impl_prompt = write_implement_prompt(
                planspace, codespace, section, rel_path, alignment_feedback,
            )
            impl_output = (artifacts
                           / f"impl-{section.number}-{filename}-output.md")
            impl_agent = f"impl-{section.number}-{filename}"
            impl_result = dispatch_agent(
                "gpt-5.3-codex-high", impl_prompt, impl_output,
                planspace, parent, impl_agent,
            )
            mailbox_send(planspace, parent,
                         f"summary:{tag}impl:{section.number}:{rel_path}:"
                         f"{summarize_output(impl_result)}")

            signal, detail = check_agent_signals(impl_result)
            if signal:
                response = pause_for_parent(
                    planspace, parent,
                    f"pause:{signal}:{section.number}:{detail}",
                )
                if not response.startswith("resume"):
                    return None

        # Check for pending messages between alignment iterations
        if handle_pending_messages(planspace, [], set()):
            return None  # abort

        # Alignment check
        align_attempt += 1
        log(f"Section {section.number}: alignment check "
            f"(attempt {align_attempt})")
        align_prompt = write_alignment_prompt(planspace, codespace, section)
        align_output = artifacts / f"align-{section.number}-output.md"
        align_agent = f"align-{section.number}"
        result = dispatch_agent("claude-opus", align_prompt, align_output,
                                planspace, parent, align_agent)

        if "ALIGNED" in result and "MISALIGNED" not in result \
                and "UNDERSPECIFIED" not in result:
            log(f"Section {section.number}: ALIGNED")
            mailbox_send(planspace, parent,
                         f"summary:align:{section.number}:ALIGNED")
            break

        signal, detail = check_agent_signals(result)
        if signal == "underspec":
            response = pause_for_parent(
                planspace, parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # After research/eval cycle, re-run alignment
            continue

        # MISALIGNED — extract feedback and report to parent
        alignment_feedback = result.replace("MISALIGNED:", "").strip()
        short_feedback = alignment_feedback[:200]
        log(f"Section {section.number}: MISALIGNED (attempt "
            f"{align_attempt}) — fixing plan/impl")
        mailbox_send(planspace, parent,
                     f"summary:align:{section.number}:"
                     f"MISALIGNED-attempt-{align_attempt}:{short_feedback}")

    return collect_modified_files(planspace, section)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: section-loop.py <planspace> <codespace> [parent]")
        sys.exit(1)

    planspace = Path(sys.argv[1])
    codespace = Path(sys.argv[2])
    parent = sys.argv[3] if len(sys.argv) > 3 else "orchestrator"
    sections_dir = planspace / "artifacts" / "sections"

    # Register our mailbox
    mailbox_register(planspace)
    log(f"Registered mailbox: {AGENT_NAME} (parent: {parent})")

    try:
        _run_loop(planspace, codespace, parent, sections_dir)
    finally:
        mailbox_cleanup(planspace)
        log("Mailbox cleaned up")


def _run_loop(planspace: Path, codespace: Path, parent: str,
              sections_dir: Path) -> None:
    # Load sections and build cross-reference map
    all_sections = load_sections(sections_dir)
    sections_by_num = {s.number: s for s in all_sections}
    file_to_sections = build_file_to_sections(all_sections)

    log(f"Loaded {len(all_sections)} sections")

    queue = [s.number for s in all_sections]
    completed: set[str] = set()

    while queue:
        # Check for abort or alignment changes before each section
        if handle_pending_messages(planspace, queue, completed):
            log("Aborted by parent")
            mailbox_send(planspace, parent, "fail:aborted")
            return

        sec_num = queue.pop(0)

        if sec_num in completed:
            continue

        section = sections_by_num[sec_num]
        log(f"=== Section {sec_num} ({len(queue)} remaining) ===")

        if not section.related_files:
            log(f"Section {sec_num}: no related files, skipping")
            completed.add(sec_num)
            mailbox_send(planspace, parent,
                         f"done:{sec_num}:no related files")
            continue

        # Run the section
        modified_files = run_section(
            planspace, codespace, section, parent,
        )

        if modified_files is None:
            # Section was paused and parent told us to stop
            log(f"Section {sec_num}: paused, exiting")
            return

        completed.add(sec_num)
        mailbox_send(planspace, parent,
                     f"done:{sec_num}:{len(modified_files)} files modified")

        # Rescheduling: check if modified files appear in other sections
        rescheduled = set()
        for mod_file in modified_files:
            for other_num in file_to_sections.get(mod_file, []):
                if other_num != sec_num and other_num not in rescheduled:
                    rescheduled.add(other_num)

        if rescheduled:
            log(f"Section {sec_num}: rescheduling {sorted(rescheduled)}")
            mailbox_send(planspace, parent,
                         f"status:reschedule:{sec_num}:"
                         f"{','.join(sorted(rescheduled))}")
            for r in sorted(rescheduled):
                completed.discard(r)
                if r not in queue:
                    queue.append(r)
                sections_by_num[r].related_files = parse_related_files(
                    sections_by_num[r].path,
                )

        log(f"Section {sec_num}: done")

    log(f"=== All {len(completed)} sections complete ===")
    mailbox_send(planspace, parent, "complete")


if __name__ == "__main__":
    main()
