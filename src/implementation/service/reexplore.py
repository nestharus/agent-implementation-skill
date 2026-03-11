from pathlib import Path

from orchestrator.path_registry import PathRegistry

from dispatch.prompt.template import TASK_SUBMISSION_SEMANTICS
from dispatch.service.prompt_safety import validate_dynamic_content
from signals.service.communication import _log_artifact, log
from coordination.service.cross_section import extract_section_summary
from dispatch.engine.section_dispatch import dispatch_agent
from flow.service.section_ingestion import ingest_and_submit
from orchestrator.types import Section


def _reexplore_section(
    section: Section, planspace: Path, codespace: Path, parent: str,
    model: str,
) -> str | None:
    """Dispatch a re-explorer when a section has no related files.

    The agent reads the codemap + section text and either proposes
    candidate files or declares greenfield. If files are found, the
    agent appends ``## Related Files`` to the section file directly.

    Returns the raw agent output, or "ALIGNMENT_CHANGED_PENDING" if
    alignment changed during dispatch.

    Exploration model selection is delegated to ``task_router`` via the
    ``scan.exploration`` policy key when queued ``scan_explore`` tasks
    are dispatched — no ``exploration_model`` parameter is needed here.
    """
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    codemap_path = paths.codemap()
    prompt_path = artifacts / f"reexplore-{section.number}-prompt.md"
    output_path = artifacts / f"reexplore-{section.number}-output.md"
    summary = extract_section_summary(section.path)

    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"3. Codemap: `{codemap_path}`"

    corrections_path = paths.corrections()
    corrections_ref = ""
    if corrections_path.exists():
        corrections_ref = (
            f"4. Codemap corrections (authoritative fixes): "
            f"`{corrections_path}`"
        )

    rendered = f"""# Task: Re-Explore Section {section.number}

## Summary
{summary}

## Files to Read
1. Section specification: `{section.path}`
2. Codespace root: `{codespace}`
{codemap_ref}
{corrections_ref}

## Context
This section has NO related files after the initial codemap exploration.
Your job is to determine why and classify the situation.

## Instructions
1. Read the section specification to understand the problem
2. Read the codemap (if it exists) for project structure context.
   If codemap corrections exist, treat them as authoritative over codemap.md.
3. Explore the codespace strategically — search for files that relate
   to this section's problem space
4. If you need deeper exploration, submit a task request to
   `{planspace}/artifacts/signals/task-requests-reexplore-{section.number}.json`:
   ```json
   {{"task_type": "scan_explore", "concern_scope": "section-{section.number}", "payload_path": "<path-to-exploration-prompt>", "priority": "normal"}}
   ```
   The above is the legacy single-task format (still accepted). You may
   also use the v2 envelope format with chain or fanout actions — see
   your agent file for the full v2 format reference.

   If dispatched as part of a flow chain, your prompt will include a
   `<flow-context>` block. Read the flow context to understand what
   previous steps produced.

   Available task types: scan_explore
   {TASK_SUBMISSION_SEMANTICS}

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
"""
    # V3: Validate dynamic content — violations block dispatch
    violations = validate_dynamic_content(rendered)
    if violations:
        log(f"  ERROR: prompt {prompt_path.name} blocked — template "
            f"violations: {violations}")
        return None
    prompt_path.write_text(rendered, encoding="utf-8")
    _log_artifact(planspace, f"prompt:reexplore-{section.number}")

    result = dispatch_agent(
        model, prompt_path, output_path,
        planspace, parent, f"reexplore-{section.number}",
        codespace=codespace, section_number=section.number,
        agent_file="section-re-explorer.md",
    )

    # V6: Submit agent-emitted follow-up work into the queue
    if result != "ALIGNMENT_CHANGED_PENDING":
        ingest_and_submit(
            planspace,
            db_path=paths.run_db(),
            submitted_by=f"reexplore-{section.number}",
            signal_path=paths.signals_dir()
            / f"task-requests-reexplore-{section.number}.json",
            origin_refs=[str(output_path)],
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
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sec = section.number
    sections_dir = paths.sections_dir()
    sections_dir.mkdir(parents=True, exist_ok=True)
    surface_path = sections_dir / f"section-{sec}-alignment-surface.md"

    lines = [f"# Alignment Surface: Section {sec}\n"]
    lines.append("Authoritative inputs for alignment judgement:\n")

    # Proposal excerpt
    proposal_excerpt = paths.proposal_excerpt(sec)
    if proposal_excerpt.exists():
        lines.append(f"- **Proposal excerpt**: `{proposal_excerpt}`")

    # Alignment excerpt
    alignment_excerpt = paths.alignment_excerpt(sec)
    if alignment_excerpt.exists():
        lines.append(f"- **Alignment excerpt**: `{alignment_excerpt}`")

    # Integration proposal
    integration_proposal = paths.proposal(sec)
    if integration_proposal.exists():
        lines.append(
            f"- **Integration proposal**: `{integration_proposal}`")

    # Proposal-state artifact (machine-readable problem state)
    proposal_state_path = (
        paths.proposals_dir() / f"section-{sec}-proposal-state.json"
    )
    if proposal_state_path.exists():
        lines.append(
            f"- **Proposal-state artifact**: `{proposal_state_path}`")

    # TODO extraction
    todos_path = paths.todos(sec)
    if todos_path.exists():
        lines.append(f"- **TODO extraction**: `{todos_path}`")

    # Microstrategy
    microstrategy_path = paths.microstrategy(sec)
    if microstrategy_path.exists():
        lines.append(f"- **Microstrategy**: `{microstrategy_path}`")

    # Problem frame
    problem_frame = paths.problem_frame(sec)
    if problem_frame.exists():
        lines.append(
            f"- **Problem frame** (derived summary; defer to excerpts "
            f"on conflict): `{problem_frame}`")

    # Incoming consequence notes
    notes_dir = paths.notes_dir()
    if notes_dir.exists():
        incoming = sorted(notes_dir.glob(f"from-*-to-{sec}.md"))
        for note in incoming:
            lines.append(f"- **Incoming note**: `{note}`")

    # Decisions (glob matches both section-03.md and section-03-*.md)
    decisions_dir = paths.decisions_dir()
    if decisions_dir.exists():
        decisions = sorted(decisions_dir.glob(f"section-{sec}*.md"))
        for dec in decisions:
            lines.append(f"- **Decision**: `{dec}`")

    # V1/R61: Intent pack artifacts — propagate to alignment surface
    # so the surface is truly authoritative over all alignment inputs.
    intent_sec_dir = paths.intent_section_dir(sec)
    intent_problem = intent_sec_dir / "problem.md"
    if intent_problem.exists():
        lines.append(
            f"- **Intent problem definition**: `{intent_problem}`")

    intent_rubric = intent_sec_dir / "problem-alignment.md"
    if intent_rubric.exists():
        lines.append(
            f"- **Intent alignment rubric**: `{intent_rubric}`")

    intent_philosophy = intent_sec_dir / "philosophy-excerpt.md"
    if intent_philosophy.exists():
        lines.append(
            f"- **Philosophy excerpt**: `{intent_philosophy}`")

    intent_registry = intent_sec_dir / "surface-registry.json"
    if intent_registry.exists():
        lines.append(
            f"- **Surface registry**: `{intent_registry}`")

    lines.append("")  # trailing newline
    surface_path.write_text("\n".join(lines), encoding="utf-8")
