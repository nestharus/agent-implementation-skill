"""Shared context builder for prompt generation.

Centralizes the repeated .exists() path resolutions so each prompt writer
only needs to add prompt-specific keys.
"""

from __future__ import annotations

from pathlib import Path

from signals.artifact_io import read_json
from orchestrator.path_registry import PathRegistry

from coordination.cross_section import extract_section_summary
from orchestrator.types import Section


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
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sec = section.number
    sections_dir = paths.sections_dir()

    # --- summary ---
    summary = extract_section_summary(section.path)

    # --- decisions ---
    decisions_file = paths.decisions_dir() / f"section-{sec}.md"
    decisions_json = paths.decisions_dir() / f"section-{sec}.json"
    decisions_block = ""
    if decisions_file.exists():
        json_ref = ""
        if decisions_json.exists():
            json_ref = (
                f"\n   - Structured decisions (JSON): `{decisions_json}`"
            )
        decisions_block = (
            f"\n## Parent Decisions (from prior pause/resume cycles)\n"
            f"Read decisions file: `{decisions_file}`{json_ref}\n\n"
            f"Use this context to inform your excerpt extraction — the parent has\n"
            f"provided additional guidance about this section.\n"
        )

    # --- strategic state ---
    strategic_state_path = paths.strategic_state()
    strategic_state_ref = ""
    if strategic_state_path.exists():
        strategic_state_ref = (
            f"\n   - Strategic state snapshot: `{strategic_state_path}`"
        )

    # --- codemap ---
    codemap_path = paths.codemap()
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"\n5. Codemap (project understanding): `{codemap_path}`"

    # --- codemap corrections ---
    codemap_corrections_path = paths.corrections()
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
    todos_path = paths.todos(sec)
    todos_ref = ""
    if todos_path.exists():
        todos_ref = (
            f"\n7. TODO extraction (in-code microstrategies): `{todos_path}`"
        )

    # --- microstrategy ---
    microstrategy_path = paths.microstrategy(sec)
    micro_ref = ""
    if microstrategy_path.exists():
        micro_ref = (
            f"\n6. Microstrategy (tactical per-file breakdown): "
            f"`{microstrategy_path}`"
        )

    # --- problem frame ---
    problem_frame_path = paths.problem_frame(sec)
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

    # --- substrate awareness ---
    substrate_path = paths.substrate_dir() / "substrate.md"
    substrate_ref = ""
    if substrate_path.exists():
        substrate_ref = (
            f"\n   - Shared integration substrate: `{substrate_path}`"
        )

    # Mode is recorded as telemetry but does NOT shape proposer instructions
    # or output format. The proposal-state schema is mode-agnostic: brownfield
    # sections will have more resolved fields, greenfield sections will have
    # more unresolved fields — the shape does not change.
    mode_block = ""

    # --- intent layer artifacts ---
    intent_sec_dir = paths.intent_section_dir(sec)
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
    intent_global_path = paths.intent_global_dir() / "philosophy.md"
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
    inputs_dir = paths.input_refs_dir(sec)
    additional_inputs_block = ""
    risk_inputs_block = ""
    if inputs_dir.exists():
        roal_index_path = inputs_dir / f"section-{sec}-roal-input-index.json"
        roal_index = read_json(roal_index_path)
        roal_paths: set[str] = set()
        risk_lines = []
        if isinstance(roal_index, list):
            for entry in roal_index:
                if not isinstance(entry, dict):
                    continue
                path_value = str(entry.get("path", "")).strip()
                if not path_value:
                    continue
                referenced_path = Path(path_value)
                if not referenced_path.exists():
                    continue
                roal_paths.add(str(referenced_path.resolve()))
                kind = str(entry.get("kind", "unknown")).strip() or "unknown"
                risk_lines.append(f"   - `{referenced_path}` ({kind})")
        if risk_lines:
            risk_inputs_block = (
                "\n\n## Risk Inputs (from ROAL)\n\n"
                "These artifacts were produced by the "
                "Risk-Optimization Adaptive Loop.\n"
                "The accepted frontier is your current local execution "
                "authority.\n"
                "Deferred steps are NOT in scope. Reopened steps are "
                "NOT locally solvable.\n"
                + "\n".join(risk_lines)
            )

        ref_files = sorted(inputs_dir.glob("*.ref"))
        if ref_files:
            input_lines = []
            for ref_file in ref_files:
                try:
                    referenced = ref_file.read_text(encoding="utf-8").strip()
                    referenced_path = Path(referenced)
                    if referenced_path.exists():
                        if str(referenced_path.resolve()) in roal_paths:
                            continue
                        input_lines.append(
                            f"   - `{referenced_path}` (from {ref_file.stem})"
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

    governance_ref = ""
    gov_packet = paths.governance_packet(sec)
    if gov_packet.exists():
        governance_ref = (
            f"\n   - Governance packet (problems, patterns, philosophy): "
            f"`{gov_packet}`"
        )

    # --- related files block ---
    file_list = []
    for rel_path in section.related_files:
        full_path = codespace / rel_path
        file_list.append(f"   - `{full_path}`")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    ctx = {
        "section_number": sec,
        "section_path": section.path,
        "codespace": codespace,
        "planspace": planspace,
        "artifacts": artifacts,
        "summary": summary,
        "decisions_block": decisions_block,
        "strategic_state_ref": strategic_state_ref,
        "codemap_ref": codemap_ref,
        "corrections_ref": corrections_ref,
        "substrate_ref": substrate_ref,
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
        "governance_ref": governance_ref,
        "risk_inputs_block": risk_inputs_block,
        "additional_inputs_block": additional_inputs_block,
        "intent_problem_ref": intent_problem_ref,
        "intent_rubric_ref": intent_rubric_ref,
        "intent_philosophy_ref": intent_philosophy_ref,
        "intent_registry_ref": intent_registry_ref,
    }
    ctx.update(overrides)
    return ctx
