from __future__ import annotations

from pathlib import Path

import pytest

from src.research.prompt.writer import (
    write_research_plan_prompt,
    write_research_synthesis_prompt,
    write_research_ticket_prompt,
    write_research_verify_prompt,
)


def _write_common_inputs(planspace: Path, section_number: str = "03") -> Path:
    artifacts = planspace / "artifacts"
    research_section = artifacts / "research" / "sections" / f"section-{section_number}"
    research_section.mkdir(parents=True, exist_ok=True)
    (artifacts / "sections").mkdir(parents=True, exist_ok=True)
    (artifacts / "proposals").mkdir(parents=True, exist_ok=True)
    (artifacts / "signals").mkdir(parents=True, exist_ok=True)

    (artifacts / "sections" / f"section-{section_number}.md").write_text(
        "# Section 03\n",
        encoding="utf-8",
    )
    (artifacts / "sections" / f"section-{section_number}-problem-frame.md").write_text(
        "# Problem Frame\n",
        encoding="utf-8",
    )
    (artifacts / "proposals" / f"section-{section_number}-proposal-state.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (artifacts / "codemap.md").write_text("# Codemap\n", encoding="utf-8")
    (artifacts / "signals" / "codemap-corrections.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (artifacts / "signals" / f"intent-surfaces-{section_number}.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (artifacts / "signals" / f"impl-feedback-surfaces-{section_number}.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (research_section / "dossier.md").write_text("prior findings\n", encoding="utf-8")
    (research_section / "proposal-addendum.md").write_text(
        "prior addendum\n",
        encoding="utf-8",
    )
    (research_section / "dossier-claims.json").write_text("{}\n", encoding="utf-8")
    trigger_path = research_section / "research-trigger.json"
    trigger_path.write_text('{"section":"03"}\n', encoding="utf-8")
    return trigger_path


def test_write_research_plan_prompt_includes_full_authority_surface(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    trigger_path = _write_common_inputs(planspace)
    codespace = tmp_path / "codespace"

    prompt_path = write_research_plan_prompt("03", planspace, codespace, trigger_path)

    assert prompt_path == planspace / "artifacts" / "research-plan-03-prompt.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert f"`{trigger_path}`" in content
    assert f"`{planspace / 'artifacts' / 'codemap.md'}`" in content
    assert f"`{planspace / 'artifacts' / 'signals' / 'codemap-corrections.json'}`" in content
    assert f"`{planspace / 'artifacts' / 'signals' / 'intent-surfaces-03.json'}`" in content
    assert f"`{planspace / 'artifacts' / 'signals' / 'impl-feedback-surfaces-03.json'}`" in content
    assert f"`{codespace}`" in content
    assert "Do not submit follow-on tasks directly" in content
    assert "not_researchable[].route" in content


def test_write_research_ticket_prompt_writes_spec_and_code_context(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    _write_common_inputs(planspace)
    codespace = tmp_path / "codespace"
    ticket = {
        "ticket_id": "T-02",
        "research_type": "code",
        "questions": ["How is the adapter wired?"],
    }

    prompt_path = write_research_ticket_prompt("03", planspace, codespace, ticket, 2)

    assert prompt_path == (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "tickets"
        / "ticket-02-prompt.md"
    )
    spec_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "tickets"
        / "ticket-02-spec.json"
    )
    assert spec_path.exists()
    content = prompt_path.read_text(encoding="utf-8")
    assert f"`{spec_path}`" in content
    assert "Use codemap, codemap corrections, and scan evidence" in content
    assert "flow context" in content


def test_write_research_synthesis_and_verify_prompts_reference_outputs(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    _write_common_inputs(planspace)

    synthesis_prompt = write_research_synthesis_prompt("03", planspace, 4)
    verify_prompt = write_research_verify_prompt("03", planspace)

    synthesis_content = synthesis_prompt.read_text(encoding="utf-8")
    assert f"`{planspace / 'artifacts' / 'research' / 'sections' / 'section-03' / 'dossier.md'}`" in synthesis_content
    assert f"`{planspace / 'artifacts' / 'research' / 'sections' / 'section-03' / 'dossier-claims.json'}`" in synthesis_content
    assert "gate aggregate manifest" in synthesis_content

    verify_content = verify_prompt.read_text(encoding="utf-8")
    assert "dossier-claims.json" in verify_content
    assert f"`{planspace / 'artifacts' / 'research' / 'sections' / 'section-03' / 'research-verify.json'}`" in verify_content


def test_write_research_plan_prompt_returns_none_when_prompt_guard_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    trigger_path = _write_common_inputs(planspace)

    def _block_prompt(_content: str, path: Path) -> bool:
        path.write_text("blocked\n", encoding="utf-8")
        return False

    monkeypatch.setattr(
        "src.research.prompt.writer.write_validated_prompt",
        _block_prompt,
    )

    prompt_path = write_research_plan_prompt("03", planspace, None, trigger_path)

    assert prompt_path is None
    assert (
        planspace / "artifacts" / "research-plan-03-prompt.md"
    ).read_text(encoding="utf-8") == "blocked\n"
