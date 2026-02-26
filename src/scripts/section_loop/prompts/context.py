"""Shared context builder for prompt generation.

Centralizes the repeated .exists() path resolutions so each prompt writer
only needs to add prompt-specific keys.
"""

from pathlib import Path

from ..cross_section import extract_section_summary
from ..types import Section


def build_prompt_context(
    section: Section,
    planspace: Path,
    codespace: Path,
    **overrides: object,
) -> dict:
    """Build the shared context dict used by all prompt templates.

    Every optional reference defaults to "" so templates degrade gracefully
    when artifacts are absent.
    """
    artifacts = planspace / "artifacts"
    sec = section.number
    sections_dir = artifacts / "sections"

    # --- summary ---
    summary = extract_section_summary(section.path)

    # --- decisions ---
    decisions_file = artifacts / "decisions" / f"section-{sec}.md"
    decisions_block = ""
    if decisions_file.exists():
        decisions_block = (
            f"\n## Parent Decisions (from prior pause/resume cycles)\n"
            f"Read decisions file: `{decisions_file}`\n\n"
            f"Use this context to inform your excerpt extraction — the parent has\n"
            f"provided additional guidance about this section.\n"
        )

    # --- codemap ---
    codemap_path = artifacts / "codemap.md"
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"\n5. Codemap (project understanding): `{codemap_path}`"

    # --- codemap corrections ---
    codemap_corrections_path = artifacts / "signals" / "codemap-corrections.json"
    corrections_ref = ""
    if codemap_corrections_path.exists():
        corrections_ref = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    # --- tools available ---
    tools_path = sections_dir / f"section-{sec}-tools-available.md"
    tools_ref = ""
    if tools_path.exists():
        tools_ref = f"\n6. Available tools from earlier sections: `{tools_path}`"

    # --- todos ---
    todos_path = artifacts / "todos" / f"section-{sec}-todos.md"
    todos_ref = ""
    if todos_path.exists():
        todos_ref = (
            f"\n7. TODO extraction (in-code microstrategies): `{todos_path}`"
        )

    # --- microstrategy ---
    microstrategy_path = (
        artifacts / "proposals" / f"section-{sec}-microstrategy.md"
    )
    micro_ref = ""
    if microstrategy_path.exists():
        micro_ref = (
            f"\n6. Microstrategy (tactical per-file breakdown): "
            f"`{microstrategy_path}`"
        )

    # --- problem frame ---
    problem_frame_path = sections_dir / f"section-{sec}-problem-frame.md"
    problem_frame_ref = ""
    if problem_frame_path.exists():
        problem_frame_ref = (
            f"\n   - Problem frame (derived summary): `{problem_frame_path}`"
        )

    # --- alignment surface ---
    alignment_surface = sections_dir / f"section-{sec}-alignment-surface.md"
    surface_line = ""
    if alignment_surface.exists():
        surface_line = (
            f"\n5. Alignment surface (read first): `{alignment_surface}`"
        )

    # --- codemap line (for alignment prompts, numbered differently) ---
    codemap_line = ""
    if codemap_path.exists():
        codemap_line = f"\n6. Project codemap (for context): `{codemap_path}`"

    corrections_line = ""
    if codemap_corrections_path.exists():
        corrections_line = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    # --- section mode ---
    section_mode_file = sections_dir / f"section-{sec}-mode.txt"
    project_mode_file = artifacts / "project-mode.txt"
    section_mode = None
    if section_mode_file.exists():
        section_mode = section_mode_file.read_text(encoding="utf-8").strip()
    project_mode = "brownfield"
    if project_mode_file.exists():
        project_mode = project_mode_file.read_text(encoding="utf-8").strip()
    effective_mode = section_mode or project_mode
    mode_block = ""
    if effective_mode == "greenfield":
        mode_block = (
            "\n## Section Mode: GREENFIELD\n\n"
            "This section has no existing code to modify. Your integration proposal\n"
            "should focus on:\n"
            "- What NEW files and modules to create\n"
            "- Where in the project structure they belong\n"
            "- How they connect to existing architecture (imports, interfaces)\n"
            "- What scaffolding is needed before implementation\n"
        )
    elif effective_mode == "hybrid":
        mode_block = (
            "\n## Section Mode: HYBRID\n\n"
            "This section has some existing code but also needs new files. Your\n"
            "integration proposal should cover both:\n"
            "- How to modify existing files (brownfield integration)\n"
            "- What new files to create and where they fit\n"
            "- How new and existing code connect\n"
        )

    # --- intent layer artifacts ---
    intent_sec_dir = (
        artifacts / "intent" / "sections" / f"section-{sec}"
    )
    intent_problem_ref = ""
    intent_problem_path = intent_sec_dir / "problem.md"
    if intent_problem_path.exists():
        intent_problem_ref = (
            f"\n   - Intent problem definition: `{intent_problem_path}`"
        )

    intent_rubric_ref = ""
    intent_rubric_path = intent_sec_dir / "problem-alignment.md"
    if intent_rubric_path.exists():
        intent_rubric_ref = (
            f"\n   - Intent alignment rubric: `{intent_rubric_path}`"
        )

    intent_philosophy_ref = ""
    intent_excerpt_path = intent_sec_dir / "philosophy-excerpt.md"
    intent_global_path = artifacts / "intent" / "global" / "philosophy.md"
    if intent_excerpt_path.exists():
        intent_philosophy_ref = (
            f"\n   - Philosophy excerpt: `{intent_excerpt_path}`"
        )
    elif intent_global_path.exists():
        intent_philosophy_ref = (
            f"\n   - Operational philosophy: `{intent_global_path}`"
        )

    intent_registry_ref = ""
    intent_registry_path = intent_sec_dir / "surface-registry.json"
    if intent_registry_path.exists():
        intent_registry_ref = (
            f"\n   - Surface registry: `{intent_registry_path}`"
        )

    # --- additional inputs (contract deltas, bridge notes, etc.) ---
    inputs_dir = artifacts / "inputs" / f"section-{sec}"
    additional_inputs_block = ""
    if inputs_dir.exists():
        ref_files = sorted(inputs_dir.glob("*.ref"))
        if ref_files:
            input_lines = []
            for ref_file in ref_files:
                try:
                    referenced = ref_file.read_text(encoding="utf-8").strip()
                    if Path(referenced).exists():
                        input_lines.append(
                            f"   - `{referenced}` (from {ref_file.stem})"
                        )
                except (OSError, ValueError) as exc:
                    print(
                        f"[CONTEXT][WARN] Failed to read ref "
                        f"{ref_file}: {exc}",
                    )
            if input_lines:
                additional_inputs_block = (
                    "\n\n## Additional Inputs (from coordination)\n\n"
                    "These artifacts were produced by cross-section "
                    "coordination or bridge agents.\n"
                    "Read them if relevant to your task:\n"
                    + "\n".join(input_lines)
                )

    # --- related files block ---
    file_list = []
    for rel_path in section.related_files:
        full_path = codespace / rel_path
        status = "" if full_path.exists() else " (to be created)"
        file_list.append(f"   - `{full_path}`{status}")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    ctx = {
        "section_number": sec,
        "section_path": section.path,
        "codespace": codespace,
        "planspace": planspace,
        "artifacts": artifacts,
        "summary": summary,
        "decisions_block": decisions_block,
        "codemap_ref": codemap_ref,
        "corrections_ref": corrections_ref,
        "tools_ref": tools_ref,
        "todos_ref": todos_ref,
        "micro_ref": micro_ref,
        "surface_line": surface_line,
        "codemap_line": codemap_line,
        "corrections_line": corrections_line,
        "mode_block": mode_block,
        "problem_frame_ref": problem_frame_ref,
        "problem_frame_path": problem_frame_path,
        "files_block": files_block,
        "additional_inputs_block": additional_inputs_block,
        "intent_problem_ref": intent_problem_ref,
        "intent_rubric_ref": intent_rubric_ref,
        "intent_philosophy_ref": intent_philosophy_ref,
        "intent_registry_ref": intent_registry_ref,
    }
    ctx.update(overrides)
    return ctx
