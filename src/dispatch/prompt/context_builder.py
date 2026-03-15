"""Shared context builder for prompt generation.

Centralizes the repeated .exists() path resolutions so each prompt writer
only needs to add prompt-specific keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.repository.input_refs import list_input_refs
from orchestrator.types import Section

if TYPE_CHECKING:
    from containers import ArtifactIOService, CrossSectionService


def _build_decisions_context(paths: PathRegistry, sec: str) -> dict:
    """Build the decisions block for prior pause/resume guidance."""
    decisions_file = paths.decision_md(sec)
    decisions_json = paths.decision_json(sec)
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
    return {"decisions_block": decisions_block}


def _build_strategic_context(paths: PathRegistry) -> dict:
    """Build strategic state, codemap, and corrections references."""
    strategic_state_path = paths.strategic_state()
    strategic_state_ref = ""
    if strategic_state_path.exists():
        strategic_state_ref = (
            f"\n   - Strategic state snapshot: `{strategic_state_path}`"
        )

    codemap_path = paths.codemap()
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"\n5. Codemap (project understanding): `{codemap_path}`"

    codemap_corrections_path = paths.corrections()
    corrections_ref = ""
    if codemap_corrections_path.exists():
        corrections_ref = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    return {
        "strategic_state_ref": strategic_state_ref,
        "codemap_ref": codemap_ref,
        "corrections_ref": corrections_ref,
    }


def _build_tools_and_todos_context(paths: PathRegistry, sec: str) -> dict:
    """Build tools, todos, and microstrategy references."""
    tools_path = paths.tools_available(sec)
    tools_ref = ""
    if tools_path.exists():
        tools_ref = f"\n6. Available tools from earlier sections: `{tools_path}`"

    todos_path = paths.todos(sec)
    todos_ref = ""
    if todos_path.exists():
        todos_ref = (
            f"\n7. TODO extraction (in-code microstrategies): `{todos_path}`"
        )

    microstrategy_path = paths.microstrategy(sec)
    micro_ref = ""
    if microstrategy_path.exists():
        micro_ref = (
            f"\n6. Microstrategy (tactical per-file breakdown): "
            f"`{microstrategy_path}`"
        )

    return {
        "tools_ref": tools_ref,
        "todos_ref": todos_ref,
        "micro_ref": micro_ref,
    }


def _build_alignment_context(paths: PathRegistry, sec: str) -> dict:
    """Build problem frame and alignment surface references."""
    problem_frame_path = paths.problem_frame(sec)
    problem_frame_ref = ""
    if problem_frame_path.exists():
        problem_frame_ref = (
            f"\n   - Problem frame (derived summary): `{problem_frame_path}`"
        )

    alignment_surface = paths.alignment_surface(sec)
    surface_line = ""
    if alignment_surface.exists():
        surface_line = (
            f"\n5. Alignment surface (read first): `{alignment_surface}`"
        )

    codemap_path = paths.codemap()
    codemap_line = ""
    if codemap_path.exists():
        codemap_line = f"\n6. Project codemap (for context): `{codemap_path}`"

    codemap_corrections_path = paths.corrections()
    corrections_line = ""
    if codemap_corrections_path.exists():
        corrections_line = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    return {
        "problem_frame_ref": problem_frame_ref,
        "problem_frame_path": problem_frame_path,
        "surface_line": surface_line,
        "codemap_line": codemap_line,
        "corrections_line": corrections_line,
    }


def _build_substrate_context(paths: PathRegistry) -> dict:
    """Build substrate and mode references."""
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

    return {
        "substrate_ref": substrate_ref,
        "mode_block": mode_block,
    }


def _build_intent_context(paths: PathRegistry, sec: str) -> dict:
    """Build intent layer artifact references."""
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

    return {
        "intent_problem_ref": intent_problem_ref,
        "intent_rubric_ref": intent_rubric_ref,
        "intent_philosophy_ref": intent_philosophy_ref,
        "intent_registry_ref": intent_registry_ref,
    }


def _build_ref_files_block(inputs_dir: Path, roal_paths: set[str]) -> str:
    """Build the additional inputs block from .ref files, excluding ROAL."""
    ref_files = list_input_refs(inputs_dir)
    if not ref_files:
        return ""

    input_lines: list[str] = []
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

    if not input_lines:
        return ""

    return (
        "\n\n## Additional Inputs (from coordination)\n\n"
        "These artifacts were produced by cross-section "
        "coordination or bridge agents.\n"
        "Read them if relevant to your task:\n"
        + "\n".join(input_lines)
    )


def _build_governance_and_files_context(
    paths: PathRegistry,
    sec: str,
    section: Section,
    codespace: Path,
) -> dict:
    """Build governance reference and related files block."""
    governance_ref = ""
    gov_packet = paths.governance_packet(sec)
    if gov_packet.exists():
        governance_ref = (
            f"\n   - Governance packet (problems, patterns, philosophy): "
            f"`{gov_packet}`"
        )

    file_list = []
    for rel_path in section.related_files:
        full_path = codespace / rel_path
        file_list.append(f"   - `{full_path}`")
    files_block = "\n".join(file_list) if file_list else "   (none)"

    return {
        "governance_ref": governance_ref,
        "files_block": files_block,
    }


class ContextBuilder:
    """Builds shared prompt context using injected services."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        cross_section: CrossSectionService,
    ) -> None:
        self._artifact_io = artifact_io
        self._cross_section = cross_section

    def _build_roal_block(self, inputs_dir: Path, sec: str) -> tuple[set[str], str]:
        """Parse the ROAL input index and build a risk inputs block.

        Returns the set of resolved ROAL paths (for deduplication) and the
        formatted risk inputs block string.
        """
        roal_index_path = inputs_dir / f"section-{sec}-roal-input-index.json"
        roal_index = self._artifact_io.read_json(roal_index_path)
        roal_paths: set[str] = set()
        risk_lines: list[str] = []
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

        risk_inputs_block = ""
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
        return roal_paths, risk_inputs_block

    def _build_input_refs_context(self, paths: PathRegistry, sec: str) -> dict:
        """Build additional inputs and risk inputs blocks from coordination."""
        inputs_dir = paths.input_refs_dir(sec)
        additional_inputs_block = ""
        risk_inputs_block = ""

        if not inputs_dir.exists():
            return {
                "risk_inputs_block": risk_inputs_block,
                "additional_inputs_block": additional_inputs_block,
            }

        roal_paths, risk_inputs_block = self._build_roal_block(inputs_dir, sec)
        additional_inputs_block = _build_ref_files_block(inputs_dir, roal_paths)

        return {
            "risk_inputs_block": risk_inputs_block,
            "additional_inputs_block": additional_inputs_block,
        }

    def build_prompt_context(
        self,
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
        sec = section.number
        summary = self._cross_section.extract_section_summary(section.path)

        ctx: dict = {
            "section_number": sec,
            "section_path": section.path,
            "codespace": codespace,
            "planspace": planspace,
            "artifacts": paths.artifacts,
            "summary": summary,
        }

        ctx.update(_build_decisions_context(paths, sec))
        ctx.update(_build_strategic_context(paths))
        ctx.update(_build_tools_and_todos_context(paths, sec))
        ctx.update(_build_alignment_context(paths, sec))
        ctx.update(_build_substrate_context(paths))
        ctx.update(_build_intent_context(paths, sec))
        ctx.update(self._build_input_refs_context(paths, sec))
        ctx.update(_build_governance_and_files_context(paths, sec, section, codespace))
        ctx.update(overrides)
        return ctx
