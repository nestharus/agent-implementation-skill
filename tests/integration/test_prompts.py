"""Integration tests for prompts module.

Tests that generated prompts contain expected paths and content.
Uses real file I/O — no LLM mocks needed (prompts are string templates).
"""

from pathlib import Path

from dispatch.prompt.writers import (
    agent_mail_instructions,
    signal_instructions,
    write_integration_proposal_prompt,
    write_section_setup_prompt,
    write_strategic_impl_prompt,
)
from orchestrator.types import Section


class TestSignalInstructions:
    def test_contains_signal_path(self, tmp_path: Path) -> None:
        sig_path = tmp_path / "signal.json"
        instructions = signal_instructions(sig_path)
        assert str(sig_path) in instructions

    def test_lists_all_states(self, tmp_path: Path) -> None:
        instructions = signal_instructions(tmp_path / "sig.json")
        for state in (
            "UNDERSPECIFIED",
            "NEED_DECISION",
            "DEPENDENCY",
            "OUT_OF_SCOPE",
            "NEEDS_PARENT",
        ):
            assert state in instructions

    def test_contains_json_schema(self, tmp_path: Path) -> None:
        instructions = signal_instructions(tmp_path / "sig.json")
        assert '"state"' in instructions
        assert '"detail"' in instructions


class TestAgentMailInstructions:
    def test_contains_agent_name(self, planspace: Path) -> None:
        instructions = agent_mail_instructions(
            planspace, "impl-01", "impl-01-monitor",
        )
        assert "impl-01" in instructions
        assert "impl-01-monitor" in instructions

    def test_contains_db_sh_command(self, planspace: Path) -> None:
        instructions = agent_mail_instructions(
            planspace, "impl-01", "impl-01-monitor",
        )
        assert "db.sh" in instructions
        assert "send" in instructions

    def test_contains_loop_detection(self, planspace: Path) -> None:
        instructions = agent_mail_instructions(
            planspace, "impl-01", "impl-01-monitor",
        )
        assert "LOOP_DETECTED" in instructions


class TestWriteSectionSetupPrompt:
    def test_creates_prompt_file(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
        )
        # Create the section spec file (needed by extract_section_summary)
        section.path.write_text("# Section 01\n\nAuthentication logic\n")
        global_proposal = planspace / "artifacts" / "global-proposal.md"
        global_alignment = planspace / "artifacts" / "global-alignment.md"
        global_proposal.write_text("# Global Proposal\nAll sections...")
        global_alignment.write_text("# Global Alignment\nConstraints...")

        prompt_path = write_section_setup_prompt(
            section, planspace, codespace,
            global_proposal, global_alignment,
        )
        assert prompt_path.exists()
        content = prompt_path.read_text()

        # Prompt references the section spec
        assert str(section.path) in content
        # Prompt references global docs
        assert str(global_proposal) in content
        assert str(global_alignment) in content
        # Prompt specifies output paths for excerpts
        assert "proposal-excerpt.md" in content
        assert "alignment-excerpt.md" in content
        # Prompt has problem frame section
        assert "Problem Frame" in content

    def test_includes_decisions_when_present(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
        )
        section.path.write_text("# Section 01\n\nAuth\n")
        decisions = (planspace / "artifacts" / "decisions"
                     / "section-01.md")
        decisions.write_text("## Decision\nUse OAuth2\n")
        global_p = planspace / "artifacts" / "global-proposal.md"
        global_a = planspace / "artifacts" / "global-alignment.md"
        global_p.write_text("proposal")
        global_a.write_text("alignment")

        prompt_path = write_section_setup_prompt(
            section, planspace, codespace, global_p, global_a,
        )
        content = prompt_path.read_text()
        assert "Parent Decisions" in content
        assert str(decisions) in content


class TestSectionsAreConcernsInvariant:
    """P3 regression: integration + strategic impl prompts must assert
    that sections are problem regions / concerns, not file bundles."""

    def _make_section(self, planspace: Path) -> Section:
        sec = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
        )
        sec.path.write_text("# Section 01\n\nAuth concern.\n")
        return sec

    def test_integration_proposal_has_concern_invariant(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        prompt_path = write_integration_proposal_prompt(
            section, planspace, codespace,
        )
        content = prompt_path.read_text()
        assert "concern" in content.lower()
        assert "not a file bundle" in content.lower()

    def test_strategic_impl_has_concern_invariant(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        # Strategic impl needs a proposal excerpt to exist
        excerpt = (planspace / "artifacts" / "sections"
                   / "section-01-proposal-excerpt.md")
        excerpt.write_text("# Excerpt\nProposal summary.\n")
        prompt_path = write_strategic_impl_prompt(
            section, planspace, codespace,
        )
        content = prompt_path.read_text()
        assert "concern" in content.lower()
        assert "not a file bundle" in content.lower()


class TestTodoExtractionInPrompt:
    """P3/R19: integration proposal prompt includes TODO path when present."""

    def _make_section(self, planspace: Path) -> Section:
        sec = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        )
        sec.path.write_text("# Section 01\n\nAuth concern.\n")
        return sec

    def test_includes_todo_path_when_file_exists(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        # Create the TODO extraction file
        todos_path = (planspace / "artifacts" / "todos"
                      / "section-01-todos.md")
        todos_path.parent.mkdir(parents=True, exist_ok=True)
        todos_path.write_text("# TODOs\n\n- Fix auth module\n")

        prompt_path = write_integration_proposal_prompt(
            section, planspace, codespace,
        )
        content = prompt_path.read_text()

        assert str(todos_path) in content
        assert "TODO extraction" in content

    def test_excludes_todo_ref_when_no_file(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = self._make_section(planspace)
        # Do NOT create a TODO extraction file

        prompt_path = write_integration_proposal_prompt(
            section, planspace, codespace,
        )
        content = prompt_path.read_text()

        # Should not contain the TODO extraction reference line
        assert "TODO extraction (in-code microstrategies)" not in content

    def test_microstrategy_reconciliation_instruction(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Prompt should tell agent to reconcile with TODOs."""
        section = self._make_section(planspace)
        todos_path = (planspace / "artifacts" / "todos"
                      / "section-01-todos.md")
        todos_path.parent.mkdir(parents=True, exist_ok=True)
        todos_path.write_text("# TODOs\n\n- Refactor handler\n")

        prompt_path = write_integration_proposal_prompt(
            section, planspace, codespace,
        )
        content = prompt_path.read_text()

        assert "canonical in-scope microstrategy surface" in content
        assert "reconcile" in content.lower()


class TestStrategicImplementationRiskInputs:
    def test_strategic_impl_prompt_renders_risk_boundary_and_inputs(
        self, planspace: Path, codespace: Path,
    ) -> None:
        section = Section(
            number="01",
            path=planspace / "artifacts" / "sections" / "section-01.md",
            related_files=["src/main.py"],
        )
        section.path.write_text("# Section 01\n\nAuth concern.\n")
        excerpt = (
            planspace / "artifacts" / "sections" / "section-01-proposal-excerpt.md"
        )
        excerpt.write_text("# Excerpt\nProposal summary.\n")

        inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        risk_payload = inputs_dir / "section-01-risk-accepted-steps.json"
        risk_payload.write_text('{"accepted_steps": ["edit-02"]}\n')
        (inputs_dir / "section-01-roal-input-index.json").write_text(
            (
                "[\n"
                f'  {{"kind": "accepted_frontier", "path": "{risk_payload}"}}\n'
                "]\n"
            ),
        )
        coordination_payload = inputs_dir / "section-01-bridge-note.md"
        coordination_payload.write_text("# Bridge\n\nShared seam details.\n")
        (inputs_dir / "bridge-note.ref").write_text(str(coordination_payload))

        prompt_path = write_strategic_impl_prompt(section, planspace, codespace)
        content = prompt_path.read_text()

        assert "### Risk Boundary" in content
        assert "Risk Inputs (from ROAL)" in content
        assert str(risk_payload) in content
        assert "Additional Inputs (from coordination)" in content
        assert str(coordination_payload) in content
        assert "fabricated" in content
        assert "bypassed" in content and "safeguards" in content
