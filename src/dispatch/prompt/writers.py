"""Thin prompt-writer functions.

Each function: build context → add prompt-specific keys → load template →
render → write file → log artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from dispatch.prompt.context_assembler import (
    build_impl_context_extras,
    build_proposal_context_extras,
)
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.prompt_formatters import (
    PromptFormatters,
    format_existing_file_listing,
    scoped_context_block,
    signal_instructions,
)

from dispatch.service.context_sidecar import ContextSidecar
from orchestrator.types import Section
from dispatch.prompt.context_builder import ContextBuilder
from pipeline.template import load_template, render

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        ConfigService,
        CrossSectionService,
        LogService,
        PromptGuard,
        SectionAlignmentService,
        TaskRouterService,
    )


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

class Writers:
    """Prompt-writer operations that require service dependencies."""

    def __init__(
        self,
        task_router: TaskRouterService,
        prompt_guard: PromptGuard,
        logger: LogService,
        communicator: Communicator,
        section_alignment: SectionAlignmentService,
        artifact_io: ArtifactIOService,
        cross_section: CrossSectionService,
        config: ConfigService,
    ) -> None:
        self._task_router = task_router
        self._prompt_guard = prompt_guard
        self._logger = logger
        self._communicator = communicator
        self._section_alignment = section_alignment
        self._artifact_io = artifact_io
        self._cross_section = cross_section
        self._config = config

    def _write_prompt(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        *,
        template_name: str,
        prompt_filename: str,
        log_label: str,
        context_builder: Callable[[Section, Path, Path], dict],
        sidecar_agent: str | None = None,
    ) -> Path | None:
        """Render, validate, and write a prompt file.

        Parameters
        ----------
        section, planspace, codespace:
            Standard prompt-writer arguments.
        template_name:
            Path within the templates directory (e.g. ``"dispatch/section-setup.md"``).
        prompt_filename:
            Filename for the written prompt (relative to ``artifacts/``).
        log_label:
            Label passed to ``_log_artifact`` (e.g. ``"prompt:setup-3"``).
        context_builder:
            Callable ``(section, planspace, codespace, paths) -> dict`` that
            returns **all** extra context keys beyond the shared base.
        sidecar_agent:
            When set, materializes a context sidecar for this agent name
            (e.g. ``"integration-proposer.md"``) and appends it after
            rendering.  Uses manual ``validate_dynamic_content`` +
            ``write_text`` instead of ``write_validated_prompt``.
        """
        paths = PathRegistry(planspace)
        artifacts = paths.artifacts

        ctx = ContextBuilder(
            artifact_io=self._artifact_io,
            cross_section=self._cross_section,
        ).build_prompt_context(section, planspace, codespace)
        ctx.update(context_builder(section, planspace, codespace))

        # Materialize sidecar BEFORE rendering so it exists at prompt-write time
        sidecar_path = None
        if sidecar_agent is not None:
            sidecar_path = ContextSidecar(self._artifact_io).materialize_context_sidecar(
                str(self._task_router.resolve_agent_path(sidecar_agent)),
                planspace, section=section.number,
            )

        prompt_path = artifacts / prompt_filename
        tpl = load_template(template_name)
        rendered = render(tpl, ctx)

        if sidecar_agent is not None:
            violations = self._prompt_guard.validate_dynamic(rendered)
            if violations:
                self._logger.log(f"  ERROR: prompt {prompt_path.name} blocked — template "
                    f"violations: {violations}")
                return None

            prompt_path.write_text(rendered, encoding="utf-8")

            if sidecar_path:
                with prompt_path.open("a", encoding="utf-8") as f:
                    f.write(scoped_context_block(sidecar_path))
        else:
            if not self._prompt_guard.write_validated(rendered, prompt_path):
                self._logger.log(f"  ERROR: prompt {prompt_path.name} blocked — template violations")
                return None

        self._communicator.log_artifact(planspace, log_label)
        return prompt_path

    # -------------------------------------------------------------------
    # Prompt-file writers (write .md files, return Path)
    # -------------------------------------------------------------------

    def write_section_setup_prompt(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        global_proposal: Path,
        global_alignment: Path,
    ) -> Path:
        """Write the prompt for extracting section-level excerpts from globals."""

        def _build_context(
            section: Section,
            planspace: Path,
            codespace: Path,
        ) -> dict:
            paths = PathRegistry(planspace)
            sec = section.number
            a_name = f"setup-{sec}"
            m_name = f"{a_name}-monitor"

            return {
                "global_proposal": global_proposal,
                "global_alignment": global_alignment,
                "proposal_excerpt": paths.proposal_excerpt(sec),
                "alignment_excerpt": paths.alignment_excerpt(sec),
                "problem_frame_path": paths.problem_frame(sec),
                "signal_block": signal_instructions(
                    paths.setup_signal(sec),
                ),
                "mail_block": PromptFormatters(config=self._config).agent_mail_instructions(planspace, a_name, m_name),
            }

        sec = section.number
        return self._write_prompt(
            section, planspace, codespace,
            template_name="dispatch/section-setup.md",
            prompt_filename=f"setup-{sec}-prompt.md",
            log_label=f"prompt:setup-{sec}",
            context_builder=_build_context,
        )

    def write_integration_proposal_prompt(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        alignment_problems: str | None = None,
        incoming_notes: str | None = None,
    ) -> Path:
        """Write the prompt for creating an integration proposal."""

        def _build_context(
            section: Section,
            planspace: Path,
            codespace: Path,
        ) -> dict:
            paths = PathRegistry(planspace)
            sec = section.number
            a_name = f"intg-proposal-{sec}"
            m_name = f"{a_name}-monitor"

            proposal_excerpt = paths.proposal_excerpt(sec)
            alignment_excerpt = paths.alignment_excerpt(sec)
            integration_proposal = paths.proposal(sec)
            proposal_state_path = paths.proposal_state(sec)

            ctx = {
                "proposal_excerpt": proposal_excerpt,
                "alignment_excerpt": alignment_excerpt,
                "integration_proposal": integration_proposal,
                "proposal_state_path": proposal_state_path,
                "task_submission_path": str(
                    paths.task_request_signal("proposal", sec)),
                "allowed_tasks": (
                    "scan.explore, signals.impact_analysis, "
                    "proposal.integration, research.plan"
                ),
                "signal_block": signal_instructions(
                    paths.proposal_signal(sec),
                ),
                "mail_block": PromptFormatters(config=self._config).agent_mail_instructions(planspace, a_name, m_name),
            }

            ctx.update(
                build_proposal_context_extras(
                    section,
                    planspace,
                    alignment_problems,
                    incoming_notes,
                )
            )

            # Research context (dossier + addendum from research flow)
            research_addendum = paths.research_addendum(sec)
            research_dossier = paths.research_dossier(sec)
            research_ref = ""
            if research_addendum.exists():
                research_ref += (
                    f"\n   - Research addendum (domain knowledge): "
                    f"`{research_addendum}`"
                )
            if research_dossier.exists():
                research_ref += (
                    f"\n   - Research dossier (full findings): "
                    f"`{research_dossier}`"
                )
            ctx["research_ref"] = research_ref

            return ctx

        sec = section.number
        return self._write_prompt(
            section, planspace, codespace,
            template_name="dispatch/integration-proposal.md",
            prompt_filename=f"intg-proposal-{sec}-prompt.md",
            log_label=f"prompt:proposal-{sec}",
            context_builder=_build_context,
            sidecar_agent="integration-proposer.md",
        )

    def write_integration_alignment_prompt(
        self,
        section: Section, planspace: Path, codespace: Path,
    ) -> Path:
        """Write the prompt for reviewing the integration proposal."""

        def _build_context(
            section: Section,
            planspace: Path,
            codespace: Path,
        ) -> dict:
            paths = PathRegistry(planspace)
            sec = section.number

            # Intent surfaces output path (for intent-judge in full mode).
            # Conditional: only add the block when intent pack exists.
            intent_surfaces_block = ""
            intent_pack = paths.intent_section_dir(sec) / "problem.md"
            if intent_pack.exists():
                surfaces_path = paths.intent_surfaces_signal(sec)
                intent_surfaces_block = (
                    f"## Surfaces Signal Output\n\n"
                    f"If you discover intent surfaces during alignment checking, "
                    f"write them to:\n`{surfaces_path}`\n"
                )

            # Proposal-state artifact (machine-readable problem state)
            proposal_state_path = paths.proposal_state(sec)
            proposal_state_line = ""
            if proposal_state_path.exists():
                proposal_state_line = (
                    f"\n5. Proposal-state artifact (machine-readable problem state): "
                    f"`{proposal_state_path}`"
                )

            # Governance packet reference
            governance_packet_path = paths.governance_packet(sec)
            governance_packet_line = ""
            if governance_packet_path.exists():
                governance_packet_line = (
                    f"\n6. Governance packet (applicable problems/patterns/profile): "
                    f"`{governance_packet_path}`"
                )

            return {
                "proposal_excerpt": paths.proposal_excerpt(sec),
                "alignment_excerpt": paths.alignment_excerpt(sec),
                "integration_proposal": paths.proposal(sec),
                "proposal_state_line": proposal_state_line,
                "governance_packet_line": governance_packet_line,
                "intent_surfaces_block": intent_surfaces_block,
            }

        sec = section.number
        return self._write_prompt(
            section, planspace, codespace,
            template_name="dispatch/integration-alignment.md",
            prompt_filename=f"intg-align-{sec}-prompt.md",
            log_label=f"prompt:proposal-align-{sec}",
            context_builder=_build_context,
        )

    def write_strategic_impl_prompt(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        alignment_problems: str | None = None,
    ) -> Path:
        """Write the prompt for strategic implementation."""

        def _build_context(
            section: Section,
            planspace: Path,
            codespace: Path,
        ) -> dict:
            paths = PathRegistry(planspace)
            sec = section.number
            a_name = f"impl-{sec}"
            m_name = f"{a_name}-monitor"

            impl_extras = build_impl_context_extras(
                section, planspace, alignment_problems,
            )

            ctx = {
                "proposal_excerpt": paths.proposal_excerpt(sec),
                "alignment_excerpt": paths.alignment_excerpt(sec),
                "integration_proposal": paths.proposal(sec),
                "modified_report": paths.impl_modified(sec),
                "problems_block": impl_extras["problems_block"],
                "decisions_block": impl_extras["decisions_block"],
                "impl_corrections_ref": impl_extras["corrections_ref"],
                "codemap_ref": impl_extras["codemap_ref"],
                "todos_ref": impl_extras["todos_ref"],
                "impl_tools_ref": impl_extras["tools_ref"],
                "tooling_block": impl_extras["tooling_block"],
                "task_submission_path": str(
                    paths.task_request_signal("impl", sec)),
                "allowed_tasks": "scan.explore, scan.deep_analyze, implementation.strategic, staleness.alignment_check",
                "signal_block": signal_instructions(
                    paths.impl_signal(sec),
                ),
                "mail_block": PromptFormatters(config=self._config).agent_mail_instructions(planspace, a_name, m_name),
            }
            ctx.update(_build_strategic_optional_refs(sec, paths))
            return ctx

        sec = section.number
        return self._write_prompt(
            section, planspace, codespace,
            template_name="dispatch/strategic-implementation.md",
            prompt_filename=f"impl-{sec}-prompt.md",
            log_label=f"prompt:impl-{sec}",
            context_builder=_build_context,
            sidecar_agent="implementation-strategist.md",
        )

    def _build_alignment_optional_refs(
        self,
        sec: str, paths: PathRegistry,
    ) -> dict[str, str]:
        """Build conditional path reference lines for impl-alignment prompt."""
        refs: dict[str, str] = {}

        alignment_surface = paths.alignment_surface(sec)
        refs["surface_line"] = (
            f"\n6. Alignment surface (read first): `{alignment_surface}`"
            if alignment_surface.exists() else ""
        )

        codemap_path = paths.codemap()
        refs["codemap_line"] = (
            f"\n7. Project codemap (for context): `{codemap_path}`"
            if codemap_path.exists() else ""
        )

        codemap_corrections_path = paths.corrections()
        refs["impl_corrections_line"] = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
            if codemap_corrections_path.exists() else ""
        )

        microstrategy_path = paths.microstrategy(sec)
        refs["micro_line"] = (
            f"\n8. Microstrategy (tactical per-file plan): "
            f"`{microstrategy_path}`"
            if microstrategy_path.exists() else ""
        )

        todo_path = paths.todos(sec)
        if todo_path.exists():
            refs["todo_line"] = (
                f"\n9. TODO extractions (in-code microstrategies): `{todo_path}`"
            )
        else:
            refs["todo_line"] = ""
            todos_dir = paths.todos_dir()
            if todos_dir.is_dir() and any(todos_dir.iterdir()):
                self._logger.log(
                    f"Section {sec}: TODO file not found at "
                    f"{todo_path} but todos/ directory is non-empty"
                )

        todo_resolution_path = (
            paths.signals_dir() / f"section-{sec}-todo-resolution.json"
        )
        refs["todo_resolution_line"] = (
            f"\n10. TODO resolution summary: `{todo_resolution_path}`"
            if todo_resolution_path.exists() else ""
        )

        tools_path = paths.tools_available(sec)
        refs["tools_line"] = (
            f"\n11. Available tools from implementation: `{tools_path}`"
            if tools_path.exists() else ""
        )

        governance_packet_path = paths.governance_packet(sec)
        refs["governance_line"] = (
            f"\n12. Governance packet (applicable problems/patterns/profile): "
            f"`{governance_packet_path}`"
            if governance_packet_path.exists() else ""
        )

        impl_feedback_path = paths.impl_feedback_surfaces(sec)
        refs["impl_feedback_block"] = (
            "\n\n## Implementation Feedback Surfaces\n\n"
            "If during your alignment review you discover constraints, "
            "unexpected behaviors, or problem dimensions that the current "
            "problem definition does not cover, write them to:\n"
            f"`{impl_feedback_path}`\n\n"
            "Use the same surfaces schema as intent surfaces:\n"
            '```json\n{"problem_surfaces": [...], "philosophy_surfaces": [...]}'
            "\n```\n"
            "Only write surfaces for genuinely new problem dimensions, not "
            "for implementation quality issues.\n"
        )

        return refs

    def write_impl_alignment_prompt(
        self,
        section: Section, planspace: Path, codespace: Path,
    ) -> Path:
        """Write the prompt for verifying implementation alignment."""

        def _build_context(
            section: Section,
            planspace: Path,
            codespace: Path,
        ) -> dict:
            paths = PathRegistry(planspace)
            sec = section.number
            all_paths = set(section.related_files) | set(
                self._section_alignment.collect_modified_files(planspace, section, codespace)
            )

            ctx = {
                "proposal_excerpt": paths.proposal_excerpt(sec),
                "alignment_excerpt": paths.alignment_excerpt(sec),
                "integration_proposal": paths.proposal(sec),
                "files_block": format_existing_file_listing(codespace, all_paths),
            }
            ctx.update(self._build_alignment_optional_refs(sec, paths))
            return ctx

        sec = section.number
        return self._write_prompt(
            section, planspace, codespace,
            template_name="dispatch/implementation-alignment.md",
            prompt_filename=f"impl-align-{sec}-prompt.md",
            log_label=f"prompt:impl-align-{sec}",
            context_builder=_build_context,
        )


# ---------------------------------------------------------------------------
# Pure functions (no Services usage)
# ---------------------------------------------------------------------------

def _build_strategic_optional_refs(
    sec: str, paths: PathRegistry,
) -> dict[str, str]:
    """Build conditional path reference lines for strategic-impl prompt."""
    refs: dict[str, str] = {}

    microstrategy_path = paths.microstrategy(sec)
    refs["micro_ref"] = (
        f"\n6. Microstrategy (tactical per-file breakdown): "
        f"`{microstrategy_path}`"
        if microstrategy_path.exists() else ""
    )

    proposal_state_path = paths.proposal_state(sec)
    refs["proposal_state_ref"] = (
        f"\n   - Proposal-state (resolved vs unresolved): "
        f"`{proposal_state_path}`"
        if proposal_state_path.exists() else ""
    )

    reconciliation_path = paths.reconciliation_result(sec)
    refs["reconciliation_ref"] = (
        f"\n   - Reconciliation result (cross-section conflicts): "
        f"`{reconciliation_path}`"
        if reconciliation_path.exists() else ""
    )

    readiness_path = paths.execution_ready(sec)
    refs["readiness_ref"] = (
        f"\n   - Execution readiness (blocker summary): "
        f"`{readiness_path}`"
        if readiness_path.exists() else ""
    )

    research_addendum = paths.research_addendum(sec)
    research_dossier = paths.research_dossier(sec)
    research_impl_ref = ""
    if research_addendum.exists():
        research_impl_ref += (
            f"\n   - Research addendum (domain constraints): "
            f"`{research_addendum}`"
        )
    if research_dossier.exists():
        research_impl_ref += (
            f"\n   - Research dossier (background knowledge): "
            f"`{research_dossier}`"
        )
    refs["research_ref"] = research_impl_ref

    return refs
