"""Integration tests for the Research -> Proposal boundary.

Research dossier artifacts produced by the research flow must appear in the
proposal prompt context when the proposal writer assembles prompts for a
section.  This file verifies the contract between research artifact paths
(PathRegistry.research_dossier / research_addendum) and the prompt-writer
logic that conditionally includes them in the ``{research_ref}`` template
variable.

Mock boundary: only Services.dispatcher, Services.prompt_guard, and
related cross-cutting services are stubbed (via conftest helpers).
File I/O and PathRegistry run for real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import ArtifactIOService, CrossSectionService, Services
from conftest import WritingGuard, NoOpCommunicator, StubPolicies, NoOpFlow
from dependency_injector import providers
from dispatch.prompt.context_builder import ContextBuilder
from dispatch.prompt.writers import Writers as PromptWriters
from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _NoopCrossSection(CrossSectionService):
    """Minimal cross-section stub returning empty summaries."""

    def persist_decision(self, *_a, **_kw):
        return None

    def extract_section_summary(self, path):
        return "Test summary."

    def write_consequence_note(self, *_a, **_kw):
        return None


def _make_section(planspace: Path, number: str = "01") -> Section:
    """Create a minimal section spec and return a Section object."""
    paths = PathRegistry(planspace)
    sec_path = paths.sections_dir() / f"section-{number}.md"
    sec_path.parent.mkdir(parents=True, exist_ok=True)
    sec_path.write_text(
        f"# Section {number}: Feature {number}\n\nImplement feature.\n",
        encoding="utf-8",
    )
    return Section(
        number=number,
        path=sec_path,
        related_files=["src/main.py"],
    )


def _seed_research_dossier(planspace: Path, number: str, content: str) -> Path:
    """Write a research dossier at the canonical PathRegistry location."""
    paths = PathRegistry(planspace)
    dossier_path = paths.research_dossier(number)
    dossier_path.parent.mkdir(parents=True, exist_ok=True)
    dossier_path.write_text(content, encoding="utf-8")
    return dossier_path


def _seed_research_addendum(planspace: Path, number: str, content: str) -> Path:
    """Write a research addendum at the canonical PathRegistry location."""
    paths = PathRegistry(planspace)
    addendum_path = paths.research_addendum(number)
    addendum_path.parent.mkdir(parents=True, exist_ok=True)
    addendum_path.write_text(content, encoding="utf-8")
    return addendum_path


# ---------------------------------------------------------------------------
# C5: Research -> Proposal context inclusion
# ---------------------------------------------------------------------------

class TestResearchDossierInProposalContext:
    """When research dossier artifacts exist, the proposal prompt writer
    includes references to them in the assembled prompt."""

    def test_dossier_appears_in_proposal_prompt(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """A research dossier at the PathRegistry location is referenced
        in the rendered integration-proposal prompt."""
        section = _make_section(planspace, "03")

        dossier_content = (
            "# Research Dossier\n\n"
            "## Findings\n\n"
            "The authentication module uses JWT tokens with HS256.\n"
        )
        dossier_path = _seed_research_dossier(planspace, "03", dossier_content)

        # Override services needed for prompt writing
        guard = WritingGuard()
        cross = _NoopCrossSection()
        Services.prompt_guard.override(providers.Object(guard))
        Services.cross_section.override(providers.Object(cross))
        try:
            writers = PromptWriters(
                task_router=Services.task_router(),
                prompt_guard=guard,
                logger=Services.logger(),
                communicator=NoOpCommunicator(),
                section_alignment=Services.section_alignment(),
                artifact_io=ArtifactIOService(),
                cross_section=cross,
                config=Services.config(),
            )

            prompt_path = writers.write_integration_proposal_prompt(
                section, planspace, codespace,
            )

            assert prompt_path is not None
            prompt_text = prompt_path.read_text(encoding="utf-8")
            # The rendered prompt must reference the dossier
            assert "dossier" in prompt_text.lower()
            assert str(dossier_path) in prompt_text
        finally:
            Services.prompt_guard.reset_override()
            Services.cross_section.reset_override()

    def test_addendum_appears_in_proposal_prompt(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """A research addendum at the PathRegistry location is referenced
        in the rendered integration-proposal prompt."""
        section = _make_section(planspace, "05")

        addendum_content = (
            "# Proposal Addendum\n\n"
            "Based on research findings, the migration should use CQRS.\n"
        )
        addendum_path = _seed_research_addendum(planspace, "05", addendum_content)

        guard = WritingGuard()
        cross = _NoopCrossSection()
        Services.prompt_guard.override(providers.Object(guard))
        Services.cross_section.override(providers.Object(cross))
        try:
            writers = PromptWriters(
                task_router=Services.task_router(),
                prompt_guard=guard,
                logger=Services.logger(),
                communicator=NoOpCommunicator(),
                section_alignment=Services.section_alignment(),
                artifact_io=ArtifactIOService(),
                cross_section=cross,
                config=Services.config(),
            )

            prompt_path = writers.write_integration_proposal_prompt(
                section, planspace, codespace,
            )

            assert prompt_path is not None
            prompt_text = prompt_path.read_text(encoding="utf-8")
            assert "addendum" in prompt_text.lower()
            assert str(addendum_path) in prompt_text
        finally:
            Services.prompt_guard.reset_override()
            Services.cross_section.reset_override()

    def test_both_dossier_and_addendum_appear(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """When both dossier and addendum exist, both are referenced."""
        section = _make_section(planspace, "07")

        dossier_path = _seed_research_dossier(
            planspace, "07", "# Dossier\n\nDomain research results.\n",
        )
        addendum_path = _seed_research_addendum(
            planspace, "07", "# Addendum\n\nConstraints from research.\n",
        )

        guard = WritingGuard()
        cross = _NoopCrossSection()
        Services.prompt_guard.override(providers.Object(guard))
        Services.cross_section.override(providers.Object(cross))
        try:
            writers = PromptWriters(
                task_router=Services.task_router(),
                prompt_guard=guard,
                logger=Services.logger(),
                communicator=NoOpCommunicator(),
                section_alignment=Services.section_alignment(),
                artifact_io=ArtifactIOService(),
                cross_section=cross,
                config=Services.config(),
            )

            prompt_path = writers.write_integration_proposal_prompt(
                section, planspace, codespace,
            )

            assert prompt_path is not None
            prompt_text = prompt_path.read_text(encoding="utf-8")
            assert str(dossier_path) in prompt_text
            assert str(addendum_path) in prompt_text
        finally:
            Services.prompt_guard.reset_override()
            Services.cross_section.reset_override()

    def test_no_research_artifacts_means_no_research_ref(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """When no research artifacts exist, the research_ref in the prompt
        is empty -- no spurious dossier/addendum references appear."""
        section = _make_section(planspace, "02")

        # Confirm the dossier and addendum paths do NOT exist
        paths = PathRegistry(planspace)
        assert not paths.research_dossier("02").exists()
        assert not paths.research_addendum("02").exists()

        guard = WritingGuard()
        cross = _NoopCrossSection()
        Services.prompt_guard.override(providers.Object(guard))
        Services.cross_section.override(providers.Object(cross))
        try:
            writers = PromptWriters(
                task_router=Services.task_router(),
                prompt_guard=guard,
                logger=Services.logger(),
                communicator=NoOpCommunicator(),
                section_alignment=Services.section_alignment(),
                artifact_io=ArtifactIOService(),
                cross_section=cross,
                config=Services.config(),
            )

            prompt_path = writers.write_integration_proposal_prompt(
                section, planspace, codespace,
            )

            assert prompt_path is not None
            prompt_text = prompt_path.read_text(encoding="utf-8")
            # No dossier or addendum references should be present
            assert "Research dossier" not in prompt_text
            assert "Research addendum" not in prompt_text
        finally:
            Services.prompt_guard.reset_override()
            Services.cross_section.reset_override()


class TestResearchDossierInSharedContextBuilder:
    """Lower-level test: the ContextBuilder itself does not inject
    research refs -- those come from the prompt writer's _build_context.
    Verify the boundary is correctly split."""

    def test_context_builder_does_not_add_research_ref(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """ContextBuilder.build_prompt_context does not include a
        research_ref key -- that responsibility belongs to the
        prompt writer's context_builder callback."""
        section = _make_section(planspace, "04")
        _seed_research_dossier(planspace, "04", "# Dossier\n\nContent.\n")

        cross = _NoopCrossSection()
        builder = ContextBuilder(
            artifact_io=ArtifactIOService(),
            cross_section=cross,
        )
        ctx = builder.build_prompt_context(section, planspace, codespace)

        # research_ref is added by the writer, not by ContextBuilder
        assert "research_ref" not in ctx
