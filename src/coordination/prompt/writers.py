"""Coordination prompt writers.

Each function: collect context → render template → validate → write file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.problem_types import Problem
from orchestrator.path_registry import PathRegistry
from pipeline.template import SRC_TEMPLATE_DIR, TASK_SUBMISSION_SEMANTICS, load_template, render
from dispatch.service.context_sidecar import ContextSidecar

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        LogService,
        PromptGuard,
        TaskRouterService,
    )


class Writers:
    """Coordination prompt writer methods."""

    def __init__(
        self,
        *,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        logger: LogService,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._logger = logger
        self._prompt_guard = prompt_guard
        self._task_router = task_router

    def write_fix_prompt(
        self,
        group: list[Problem], planspace: Path, codespace: Path,
        group_id: int,
    ) -> Path | None:
        """Write a prompt to fix a group of related problems.

        The prompt lists the grouped problems with section context, the
        affected files, and instructs the agent to fix ALL listed problems
        in a coordinated way.
        """
        paths = PathRegistry(planspace)
        prompt_path = paths.coordination_fix_prompt(group_id)
        modified_report = paths.coordination_fix_modified(group_id)

        problems_text = _format_problems(group)
        file_list = _format_file_list(group, codespace)
        section_specs, alignment_specs = _format_section_refs(group, paths)
        codemap_block = _format_codemap_block(paths)
        tools_block = self._format_tools_block(paths)

        task_submission_path = paths.coordination_task_request(group_id)

        template = load_template("coordination/coordinator-fix.md", SRC_TEMPLATE_DIR)
        rendered = render(template, {
            "group_id": str(group_id),
            "problems_text": problems_text,
            "file_list": file_list,
            "section_specs": section_specs,
            "alignment_specs": alignment_specs,
            "codemap_block": codemap_block,
            "tools_block": tools_block,
            "task_submission_path": str(task_submission_path),
            "task_submission_semantics": TASK_SUBMISSION_SEMANTICS,
            "modified_report": str(modified_report),
            "codespace": str(codespace),
        })
        violations = self._prompt_guard.validate_dynamic(rendered)
        if violations:
            self._logger.log(f"  ERROR: prompt {prompt_path.name} blocked — template "
                f"violations: {violations}")
            return None

        sidecar_path = ContextSidecar(self._artifact_io).materialize_context_sidecar(
            str(self._task_router.resolve_agent_path("coordination-fixer.md")),
            planspace,
        )

        prompt_path.write_text(rendered, encoding="utf-8")
        if sidecar_path:
            with prompt_path.open("a", encoding="utf-8") as f:
                f.write(
                    f"\n## Scoped Context\n"
                    f"Agent context sidecar with resolved inputs: "
                    f"`{sidecar_path}`\n"
                )
        self._communicator.log_artifact(planspace, f"prompt:coordinator-fix-{group_id}")
        return prompt_path

    def write_bridge_prompt(
        self,
        group: list[Problem],
        group_index: int,
        group_sections: list[str],
        planspace: Path,
        bridge_reason: str,
    ) -> Path | None:
        """Write a prompt for bridge resolution of cross-section overlap."""
        paths = PathRegistry(planspace)
        bridge_prompt = paths.coordination_bridge_prompt(group_index)
        contract_path = paths.coordination_contract_patch(group_index)
        contract_delta_path = paths.contracts_dir() / f"contract-delta-group-{group_index}.md"
        notes_dir = paths.notes_dir()
        sections_dir = paths.sections_dir()
        proposals_dir = paths.proposals_dir()

        group_files = sorted(
            {fp for p in group for fp in p.files},
        )

        section_refs = "\n".join(
            f"- Section {n}: "
            f"`{sections_dir / f'section-{n}-proposal-excerpt.md'}`"
            for n in group_sections
        )
        alignment_refs = "\n".join(
            f"- Section {n}: "
            f"`{sections_dir / f'section-{n}-alignment-excerpt.md'}`"
            for n in group_sections
        )
        proposal_refs = "\n".join(
            f"- `{proposals_dir / f'section-{n}-integration-proposal.md'}`"
            for n in group_sections
        )

        consequence_refs = []
        for section_num in group_sections:
            for note in sorted(notes_dir.glob(f"from-*-to-{section_num}.md")):
                consequence_refs.append(f"- `{note}`")
        consequence_block = ""
        if consequence_refs:
            consequence_block = "\n\n## Existing Consequence Notes\n" + "\n".join(
                consequence_refs,
            )

        note_output_refs = "\n".join(
            f"- `{notes_dir / f'from-bridge-{group_index}-to-{n}.md'}`"
            for n in group_sections
        )
        shared_files_list = "\n".join(f"- `{fp}`" for fp in group_files)
        template = load_template("coordination/bridge-resolve.md", SRC_TEMPLATE_DIR)
        rendered = render(template, {
            "group_index": str(group_index),
            "bridge_reason": bridge_reason,
            "section_refs": section_refs,
            "alignment_refs": alignment_refs,
            "proposal_refs": proposal_refs,
            "shared_files": shared_files_list,
            "consequence_block": consequence_block,
            "contract_path": str(contract_path),
            "contract_delta_path": str(contract_delta_path),
            "note_output_refs": note_output_refs,
        })
        if not self._prompt_guard.write_validated(rendered, bridge_prompt):
            return None
        self._communicator.log_artifact(planspace, f"prompt:bridge-resolve-{group_index}")
        return bridge_prompt

    def _format_tools_block(self, paths: PathRegistry) -> str:
        tool_digest_path = paths.tool_digest()
        tool_registry_path = paths.tool_registry()
        if tool_digest_path.exists():
            return (
                f"\n## Available Tools\n"
                f"See tool digest: `{tool_digest_path}`\n"
            )
        if not tool_registry_path.exists():
            return ""
        reg = self._artifact_io.read_json(tool_registry_path)
        if reg is not None:
            cross_tools = [
                t for t in (reg if isinstance(reg, list)
                            else reg.get("tools", []))
                if t.get("scope") == "cross-section"
            ]
            if cross_tools:
                tool_lines = "\n".join(
                    f"- `{t.get('path', '?')}` "
                    f"[{t.get('status', 'experimental')}]: "
                    f"{t.get('description', '')}"
                    for t in cross_tools
                )
                return f"\n## Available Cross-Section Tools\n{tool_lines}\n"
            return ""

        malformed_path = tool_registry_path.with_suffix(".malformed.json")
        return _compose_tools_block_text(malformed_path)


# ---------------------------------------------------------------------------
# Pure formatting helpers (no Services usage)
# ---------------------------------------------------------------------------

def _format_problems(group: list[Problem]) -> str:
    parts = []
    for i, p in enumerate(group):
        desc = (
            f"### Problem {i + 1} (Section {p.section}, "
            f"type: {p.type})\n"
            f"{p.description}"
        )
        parts.append(desc)
    return "\n\n".join(parts)


def _format_file_list(group: list[Problem], codespace: Path) -> str:
    all_files: list[str] = []
    seen: set[str] = set()
    for p in group:
        for f in p.files:
            if f not in seen:
                all_files.append(f)
                seen.add(f)
    return "\n".join(f"- `{codespace / f}`" for f in all_files)


def _format_section_refs(
    group: list[Problem], paths: PathRegistry,
) -> tuple[str, str]:
    section_nums = sorted({p.section for p in group})
    sec_dir = paths.sections_dir()
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
    return section_specs, alignment_specs


def _format_codemap_block(paths: PathRegistry) -> str:
    codemap_path = paths.codemap()
    corrections_path = paths.corrections()
    if not codemap_path.exists():
        return ""
    corrections_line = ""
    if corrections_path.exists():
        corrections_line = (
            f"- Codemap corrections (authoritative fixes): "
            f"`{corrections_path}`\n"
        )
    return (
        f"\n## Project Understanding\n"
        f"- Codemap: `{codemap_path}`\n"
        f"{corrections_line}"
        f"\nIf codemap corrections exist, treat them as authoritative "
        f"over codemap.md.\n"
    )


def _compose_tools_block_text(malformed_path: Path) -> str:
    """Return the warning text for a malformed tool registry."""
    return (
        f"\n## Tool Registry Warning\n"
        "Tool registry is malformed. "
        f"Malformed artifact preserved at "
        f"`{malformed_path}`.\n"
        f"Consider dispatching tool-registrar repair before "
        f"relying on tool context.\n"
    )
