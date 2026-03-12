from __future__ import annotations

from pathlib import Path

import pytest

from dispatch.prompt.writers import (
    write_integration_proposal_prompt,
    write_strategic_impl_prompt,
)
from orchestrator.types import Section

def _section(planspace: Path, number: str = "01") -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(
        f"# Section {number}\n\nResearch-aware prompt test section.\n",
        encoding="utf-8",
    )
    return Section(number=number, path=section_path, related_files=["src/main.py"])

def _write_common_section_artifacts(planspace: Path, number: str = "01") -> None:
    sections_dir = planspace / "artifacts" / "sections"
    proposals_dir = planspace / "artifacts" / "proposals"
    (sections_dir / f"section-{number}-proposal-excerpt.md").write_text(
        "proposal excerpt\n",
        encoding="utf-8",
    )
    (sections_dir / f"section-{number}-alignment-excerpt.md").write_text(
        "alignment excerpt\n",
        encoding="utf-8",
    )
    (proposals_dir / f"section-{number}-integration-proposal.md").write_text(
        "aligned proposal\n",
        encoding="utf-8",
    )

def _write_research_artifacts(planspace: Path, number: str = "01") -> tuple[Path, Path]:
    research_dir = (
        planspace / "artifacts" / "research" / "sections" / f"section-{number}"
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    addendum = research_dir / "proposal-addendum.md"
    dossier = research_dir / "dossier.md"
    addendum.write_text("domain-specific constraints\n", encoding="utf-8")
    dossier.write_text("background findings\n", encoding="utf-8")
    return addendum, dossier

@pytest.fixture(autouse=True)
def _prompt_writer_isolation(monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    monkeypatch.setattr(
        "dispatch.prompt.writers.materialize_context_sidecar",
        lambda *_args, **_kwargs: None,
    )

def test_write_integration_proposal_prompt_includes_research_refs_when_present(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _section(planspace)
    _write_common_section_artifacts(planspace)
    addendum, dossier = _write_research_artifacts(planspace)

    prompt_path = write_integration_proposal_prompt(section, planspace, codespace)
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Research addendum (domain knowledge)" in prompt
    assert str(addendum) in prompt
    assert "Research dossier (full findings)" in prompt
    assert str(dossier) in prompt
    assert (
        "Available task types for this role: scan.explore, signals.impact_analysis, "
        "proposal.integration, research.plan"
    ) in prompt

def test_write_integration_proposal_prompt_omits_research_refs_when_absent(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _section(planspace)
    _write_common_section_artifacts(planspace)

    prompt_path = write_integration_proposal_prompt(section, planspace, codespace)
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Research addendum (domain knowledge)" not in prompt
    assert "Research dossier (full findings)" not in prompt

def test_write_strategic_impl_prompt_includes_research_refs_when_present(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _section(planspace)
    _write_common_section_artifacts(planspace)
    addendum, dossier = _write_research_artifacts(planspace)

    prompt_path = write_strategic_impl_prompt(section, planspace, codespace)
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Research addendum (domain constraints)" in prompt
    assert str(addendum) in prompt
    assert "Research dossier (background knowledge)" in prompt
    assert str(dossier) in prompt
