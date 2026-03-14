from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry

from pipeline.template import TASK_SUBMISSION_SEMANTICS
from orchestrator.types import Section
from dispatch.types import ALIGNMENT_CHANGED_PENDING


def _compose_reexplore_text(
    section_number: str,
    section_path: Path,
    summary: str,
    codespace: Path,
    codemap_ref: str,
    corrections_ref: str,
    planspace: Path,
) -> str:
    """Return the re-exploration prompt text."""
    output_path = PathRegistry(planspace).artifacts / f"reexplore-{section_number}-output.md"
    return f"""# Task: Re-Explore Section {section_number}

## Summary
{summary}

## Files to Read
1. Section specification: `{section_path}`
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
   `{planspace}/artifacts/signals/task-requests-reexplore-{section_number}.json`:
   ```json
   {{"task_type": "scan.explore", "concern_scope": "section-{section_number}", "payload_path": "<path-to-exploration-prompt>", "priority": "normal"}}
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
`{section_path}` using the standard format:

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
`{planspace}/artifacts/signals/section-{section_number}-mode.json`:
```json
{{"mode": "brownfield|greenfield|hybrid", "confidence": "high|medium|low", "reason": "..."}}
```
This is how the pipeline reads your classification — the script reads
the JSON, not unstructured text.
"""


def _build_reexplore_prompt(
    section: Section,
    planspace: Path,
    codespace: Path,
) -> str:
    """Build the re-exploration prompt for a section with no related files."""
    paths = PathRegistry(planspace)
    summary = Services.cross_section().extract_section_summary(section.path)
    codemap_path = paths.codemap()
    codemap_ref = f"3. Codemap: `{codemap_path}`" if codemap_path.exists() else ""
    corrections_path = paths.corrections()
    corrections_ref = (
        f"4. Codemap corrections (authoritative fixes): `{corrections_path}`"
        if corrections_path.exists()
        else ""
    )
    return _compose_reexplore_text(
        section_number=section.number,
        section_path=section.path,
        summary=summary,
        codespace=codespace,
        codemap_ref=codemap_ref,
        corrections_ref=corrections_ref,
        planspace=planspace,
    )


def reexplore_section(
    section: Section, planspace: Path, codespace: Path, parent: str,
    model: str,
) -> str | None:
    """Dispatch a re-explorer when a section has no related files."""
    paths = PathRegistry(planspace)
    prompt_path = paths.artifacts / f"reexplore-{section.number}-prompt.md"
    output_path = paths.artifacts / f"reexplore-{section.number}-output.md"

    rendered = _build_reexplore_prompt(section, planspace, codespace)
    violations = Services.prompt_guard().validate_dynamic(rendered)
    if violations:
        Services.logger().log(
            f"  ERROR: prompt {prompt_path.name} blocked — template "
            f"violations: {violations}"
        )
        return None
    prompt_path.write_text(rendered, encoding="utf-8")
    Services.communicator().log_artifact(planspace, f"prompt:reexplore-{section.number}")

    result = Services.dispatcher().dispatch(
        model, prompt_path, output_path,
        planspace, parent, f"reexplore-{section.number}",
        codespace=codespace, section_number=section.number,
        agent_file=Services.task_router().agent_for("implementation.reexplore"),
    )

    if result != ALIGNMENT_CHANGED_PENDING:
        Services.flow_ingestion().ingest_and_submit(
            planspace,
            submitted_by=f"reexplore-{section.number}",
            signal_path=paths.signals_dir()
            / f"task-requests-reexplore-{section.number}.json",
            origin_refs=[str(output_path)],
        )

    return result


def _collect_surface_entries(
    paths: PathRegistry, sec: str,
) -> list[tuple[str, Path]]:
    """Collect (label, path) pairs for all existing alignment artifacts."""
    entries: list[tuple[str, Path]] = []
    simple_artifacts: list[tuple[str, Path]] = [
        ("Proposal excerpt", paths.proposal_excerpt(sec)),
        ("Alignment excerpt", paths.alignment_excerpt(sec)),
        ("Integration proposal", paths.proposal(sec)),
        ("Proposal-state artifact", paths.proposal_state(sec)),
        ("TODO extraction", paths.todos(sec)),
        ("Microstrategy", paths.microstrategy(sec)),
    ]
    for label, path in simple_artifacts:
        if path.exists():
            entries.append((label, path))

    problem_frame = paths.problem_frame(sec)
    if problem_frame.exists():
        entries.append((
            "Problem frame (derived summary; defer to excerpts on conflict)",
            problem_frame,
        ))

    notes_dir = paths.notes_dir()
    if notes_dir.exists():
        for note in sorted(notes_dir.glob(f"from-*-to-{sec}.md")):
            entries.append(("Incoming note", note))

    decisions_dir = paths.decisions_dir()
    if decisions_dir.exists():
        for dec in sorted(decisions_dir.glob(f"section-{sec}*.md")):
            entries.append(("Decision", dec))

    intent_sec_dir = paths.intent_section_dir(sec)
    intent_artifacts: list[tuple[str, Path]] = [
        ("Intent problem definition", intent_sec_dir / "problem.md"),
        ("Intent alignment rubric", intent_sec_dir / "problem-alignment.md"),
        ("Philosophy excerpt", intent_sec_dir / "philosophy-excerpt.md"),
        ("Surface registry", intent_sec_dir / "surface-registry.json"),
    ]
    for label, path in intent_artifacts:
        if path.exists():
            entries.append((label, path))

    return entries


def write_alignment_surface(
    planspace: Path, section: Section,
) -> None:
    """Write a single file listing all authoritative alignment inputs.

    This gives the alignment judge a single file to read first, so it
    knows exactly which artifacts exist for this section and where to
    find them.
    """
    registry = PathRegistry(planspace)
    sec = section.number
    registry.sections_dir().mkdir(parents=True, exist_ok=True)
    surface_path = registry.alignment_surface(sec)

    lines = [
        f"# Alignment Surface: Section {sec}\n",
        "Authoritative inputs for alignment judgement:\n",
    ]
    for label, path in _collect_surface_entries(registry, sec):
        lines.append(f"- **{label}**: `{path}`")
    lines.append("")  # trailing newline
    surface_path.write_text("\n".join(lines), encoding="utf-8")
