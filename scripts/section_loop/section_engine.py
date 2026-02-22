import json
import subprocess
from pathlib import Path

from .alignment import (
    _extract_problems,
    collect_modified_files,
)
from .change_detection import diff_files, snapshot_files
from .communication import (
    AGENT_NAME,
    DB_SH,
    WORKFLOW_HOME,
    _log_artifact,
    _record_traceability,
    log,
    mailbox_send,
)
from .cross_section import (
    extract_section_summary,
    persist_decision,
    post_section_completion,
    read_incoming_notes,
)
from .dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_agent_signal,
    summarize_output,
    write_model_choice_signal,
)
from .pipeline_control import (
    alignment_changed_pending,
    handle_pending_messages,
    pause_for_parent,
    poll_control_messages,
)
from .prompts import (
    agent_mail_instructions,
    signal_instructions,
    write_impl_alignment_prompt,
    write_integration_alignment_prompt,
    write_integration_proposal_prompt,
    write_section_setup_prompt,
    write_strategic_impl_prompt,
)
from .types import Section


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

**Also write a structured JSON signal** to
`{planspace}/artifacts/signals/section-{section.number}-mode.json`:
```json
{{"mode": "brownfield|greenfield|hybrid", "confidence": "high|medium|low", "reason": "..."}}
```
This is how the pipeline reads your classification — the script reads
the JSON, not unstructured text.
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:reexplore-{section.number}")

    result = dispatch_agent(
        "claude-opus", prompt_path, output_path,
        planspace, parent, f"reexplore-{section.number}",
        codespace=codespace, section_number=section.number,
        agent_file="section-re-explorer.md",
    )
    return result


def _write_alignment_surface(
    planspace: Path, section: Section,
) -> None:
    """Write a single file listing all authoritative alignment inputs.

    This gives the alignment judge a single file to read first, so it
    knows exactly which artifacts exist for this section and where to
    find them.
    """
    artifacts = planspace / "artifacts"
    sec = section.number
    sections_dir = artifacts / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    surface_path = sections_dir / f"section-{sec}-alignment-surface.md"

    lines = [f"# Alignment Surface: Section {sec}\n"]
    lines.append("Authoritative inputs for alignment judgement:\n")

    # Proposal excerpt
    proposal_excerpt = sections_dir / f"section-{sec}-proposal-excerpt.md"
    if proposal_excerpt.exists():
        lines.append(f"- **Proposal excerpt**: `{proposal_excerpt}`")

    # Alignment excerpt
    alignment_excerpt = sections_dir / f"section-{sec}-alignment-excerpt.md"
    if alignment_excerpt.exists():
        lines.append(f"- **Alignment excerpt**: `{alignment_excerpt}`")

    # Integration proposal
    integration_proposal = (
        artifacts / "proposals"
        / f"section-{sec}-integration-proposal.md"
    )
    if integration_proposal.exists():
        lines.append(
            f"- **Integration proposal**: `{integration_proposal}`")

    # TODO extraction
    todos_path = artifacts / "todos" / f"section-{sec}-todos.md"
    if todos_path.exists():
        lines.append(f"- **TODO extraction**: `{todos_path}`")

    # Microstrategy
    microstrategy_path = (
        artifacts / "proposals" / f"section-{sec}-microstrategy.md"
    )
    if microstrategy_path.exists():
        lines.append(f"- **Microstrategy**: `{microstrategy_path}`")

    # Incoming consequence notes
    notes_dir = artifacts / "notes"
    if notes_dir.exists():
        incoming = sorted(notes_dir.glob(f"from-*-to-{sec}.md"))
        for note in incoming:
            lines.append(f"- **Incoming note**: `{note}`")

    # Decisions
    decisions_dir = artifacts / "decisions"
    if decisions_dir.exists():
        decisions = sorted(decisions_dir.glob(f"section-{sec}-*.md"))
        for dec in decisions:
            lines.append(f"- **Decision**: `{dec}`")

    lines.append("")  # trailing newline
    surface_path.write_text("\n".join(lines), encoding="utf-8")


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
    # Recurrence signal: notify coordinator when a section loops
    # -----------------------------------------------------------------
    if section.solve_count >= 2:
        recurrence_signal = {
            "section": section.number,
            "attempt": section.solve_count,
            "recurring": True,
            "escalate_to_coordinator": True,
        }
        recurrence_path = (planspace / "artifacts" / "signals"
                           / f"section-{section.number}-recurrence.json")
        recurrence_path.parent.mkdir(parents=True, exist_ok=True)
        recurrence_path.write_text(
            json.dumps(recurrence_signal, indent=2), encoding="utf-8")
        log(f"Section {section.number}: recurrence signal written "
            f"(attempt {section.solve_count})")

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
    pre_tool_total = 0  # Total tool count before implementation
    if tool_registry_path.exists():
        try:
            registry = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            all_tools = (registry if isinstance(registry, list)
                         else registry.get("tools", []))
            pre_tool_total = len(all_tools)
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
                    status = tool.get("status", "experimental")
                    tool_id = tool.get("id", "")
                    id_tag = f" id={tool_id}" if tool_id else ""
                    lines.append(
                        f"- `{path}` [{status}] ({scope}, "
                        f"from {creator}{id_tag}): {desc}")
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
            if signal == "out_of_scope":
                scope_delta_dir = planspace / "artifacts" / "scope-deltas"
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                scope_delta = {
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                }
                (scope_delta_dir
                 / f"section-{section.number}-scope-delta.json"
                 ).write_text(
                    json.dumps(scope_delta, indent=2), encoding="utf-8")
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
        _write_alignment_surface(planspace, section)

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
        escalated_from = None
        if proposal_attempt >= 3 or notes_count >= 3:
            escalated_from = proposal_model
            proposal_model = "gpt-5.3-codex-xhigh"
            log(f"Section {section.number}: escalating to "
                f"{proposal_model} (attempt={proposal_attempt}, "
                f"notes={notes_count})")

        reason = (f"attempt={proposal_attempt}, notes={notes_count}"
                  if escalated_from
                  else "first attempt, default model")
        write_model_choice_signal(
            planspace, section.number, "integration-proposal",
            proposal_model, reason, escalated_from,
        )

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
            if signal == "out_of_scope":
                scope_delta_dir = planspace / "artifacts" / "scope-deltas"
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                scope_delta = {
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                }
                (scope_delta_dir
                 / f"section-{section.number}-scope-delta.json"
                 ).write_text(
                    json.dumps(scope_delta, indent=2), encoding="utf-8")
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
            _write_alignment_surface(planspace, section)
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
            if len(post_tools) > pre_tool_total:
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
                    f"3. Ensure required fields exist: `id`, `path`, "
                    f"`created_by`, `scope`, `status`, `description`, "
                    f"`registered_at`\n"
                    f"4. If `id` is missing, assign a short kebab-case "
                    f"identifier\n"
                    f"5. If `status` is missing, set to `experimental`\n"
                    f"6. Promote tools to `stable` if they have passing "
                    f"tests or are used by multiple sections\n"
                    f"7. Remove entries for files that don't exist or "
                    f"aren't tools\n"
                    f"8. If any cross-section tools were added, verify "
                    f"they are genuinely reusable\n\n"
                    f"After validation, write a tool digest to: "
                    f"`{artifacts / 'tool-digest.md'}`\n"
                    f"Format: one line per tool grouped by scope "
                    f"(cross-section, section-local, test-only).\n\n"
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
                    agent_file="tool-registrar.md",
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
