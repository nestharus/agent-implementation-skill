from __future__ import annotations

from pathlib import Path

import pytest

from src.scripts.lib.research.prompt_writer import write_research_plan_prompt


def test_write_research_plan_prompt_writes_prompt_with_context_and_outputs(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    research_section = artifacts / "research" / "sections" / "section-03"

    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    research_section.mkdir(parents=True, exist_ok=True)

    (sections / "section-03.md").write_text("# Section 03\n", encoding="utf-8")
    (sections / "section-03-problem-frame.md").write_text(
        "# Problem Frame\n",
        encoding="utf-8",
    )
    (proposals / "section-03-proposal-state.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (artifacts / "codemap.md").write_text("# Codemap\n", encoding="utf-8")
    trigger_path = research_section / "research-trigger.json"
    trigger_path.write_text('{"section":"03"}\n', encoding="utf-8")

    prompt_path = write_research_plan_prompt("03", planspace, None, trigger_path)

    assert prompt_path == artifacts / "research-plan-03-prompt.md"
    assert prompt_path is not None
    content = prompt_path.read_text(encoding="utf-8")
    assert f"`{trigger_path}`" in content
    assert f"`{sections / 'section-03.md'}`" in content
    assert f"`{artifacts / 'research' / 'sections' / 'section-03' / 'research-plan.json'}`" in content
    assert f"`{artifacts / 'research' / 'sections' / 'section-03' / 'research-status.json'}`" in content
    assert (
        "Allowed task types: `research_domain_ticket`, "
        "`research_synthesis`, `research_verify`"
    ) in content
    assert "## Codespace" not in content


def test_write_research_plan_prompt_returns_none_when_prompt_safety_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    trigger_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "research-trigger.json"
    )
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_path.write_text("{}\n", encoding="utf-8")

    def _block_prompt(_content: str, path: Path) -> bool:
        path.write_text("blocked\n", encoding="utf-8")
        return False

    monkeypatch.setattr(
        "src.scripts.lib.research.prompt_writer.write_validated_prompt",
        _block_prompt,
    )

    prompt_path = write_research_plan_prompt("03", planspace, None, trigger_path)

    assert prompt_path is None
    assert (
        planspace / "artifacts" / "research-plan-03-prompt.md"
    ).read_text(encoding="utf-8") == "blocked\n"
