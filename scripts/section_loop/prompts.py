from pathlib import Path

from .alignment import collect_modified_files
from .communication import (
    DB_SH,
    _log_artifact,
)
from .cross_section import extract_section_summary
from .types import Section


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

    # Codemap reference so alignment judge sees project skeleton
    codemap_path = artifacts / "codemap.md"
    codemap_line = ""
    if codemap_path.exists():
        codemap_line = f"\n5. Project codemap (for context): `{codemap_path}`"

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
4. Integration proposal to review: `{integration_proposal}`{codemap_line}

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

### TODO Handling

If the section has in-code TODO blocks (microstrategies), you must either:
- **Implement** the TODO as specified
- **Rewrite/remove** the TODO with justification (if the approach changed)
- **Defer** with a clear reason pointing to which section/phase handles it

After handling TODOs, write a resolution summary to:
`{artifacts}/signals/section-{section.number}-todo-resolution.json`

```json
{{"todos": [{{"location": "file:line", "action": "implemented|rewritten|deferred", "reason": "..."}}]}}
```

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

    # Codemap reference so alignment judge sees project skeleton
    codemap_path = artifacts / "codemap.md"
    codemap_line = ""
    if codemap_path.exists():
        codemap_line = f"\n6. Project codemap (for context): `{codemap_path}`"

    # Microstrategy reference (hierarchical alignment boundary)
    microstrategy_path = (artifacts / "proposals"
                          / f"section-{section.number}-microstrategy.md")
    micro_line = ""
    if microstrategy_path.exists():
        micro_line = (f"\n7. Microstrategy (tactical per-file plan): "
                      f"`{microstrategy_path}`")

    # TODO extraction reference (in-code microstrategies)
    todo_path = (artifacts
                 / f"section-{section.number}-todo-extractions.md")
    todo_line = ""
    if todo_path.exists():
        todo_line = (f"\n8. TODO extractions (in-code microstrategies): "
                     f"`{todo_path}`")

    # TODO resolution signal (structured output from implementor)
    todo_resolution_path = (artifacts / "signals"
                            / f"section-{section.number}"
                            f"-todo-resolution.json")
    todo_resolution_line = ""
    if todo_resolution_path.exists():
        todo_resolution_line = (
            f"\n9. TODO resolution summary: "
            f"`{todo_resolution_path}`")

    prompt_path.write_text(f"""# Task: Implementation Alignment Check — Section {sec}

## Summary
{summary}

## Files to Read
1. Section alignment excerpt: `{alignment_excerpt}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Integration proposal: `{integration_proposal}`
4. Section specification: `{section.path}`
5. Implemented files (read each one):
{files_block}{codemap_line}{micro_line}{todo_line}{todo_resolution_line}

## Worktree root
`{codespace}`

## Instructions

Read the alignment excerpt and proposal excerpt first — these define the
PROBLEM and CONSTRAINTS. Then read the integration proposal to understand
WHAT was planned. If a microstrategy exists, it provides the tactical
per-file breakdown. Finally read the implemented files.

Check SHAPE AND DIRECTION:
- Is the implementation still solving the RIGHT PROBLEM?
- Does the code match the intent of the integration proposal?
- Has anything drifted from the original problem definition?
- Are the changes internally consistent across files?
- If TODO extractions exist, were they resolved appropriately?
  (implemented, rewritten with justification, or explicitly deferred)

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
