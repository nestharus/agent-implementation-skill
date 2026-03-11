from __future__ import annotations

from pathlib import Path

from orchestrator.types import Section
from src.dispatch.prompt_context_assembler import (
    build_impl_context_extras,
    build_proposal_context_extras,
)


def _section(planspace: Path, number: str = "01") -> Section:
    path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Section\n", encoding="utf-8")
    return Section(number=number, path=path)


def test_build_proposal_context_extras_writes_problem_and_note_artifacts(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    section = _section(planspace)
    proposal_path = (
        planspace / "artifacts" / "proposals" / "section-01-integration-proposal.md"
    )
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text("# Existing\n", encoding="utf-8")

    extras = build_proposal_context_extras(
        section,
        planspace,
        "fix the unresolved contract",
        "note from section 02",
        base_context={"governance_ref": "gov-packet"},
    )

    problems_path = planspace / "artifacts" / "intg-proposal-01-problems.md"
    notes_path = planspace / "artifacts" / "intg-proposal-01-notes.md"

    assert problems_path.read_text(encoding="utf-8") == "fix the unresolved contract"
    assert notes_path.read_text(encoding="utf-8") == "note from section 02"
    assert str(problems_path) in extras["problems_block"]
    assert str(proposal_path) in extras["existing_note"]
    assert str(notes_path) in extras["notes_block"]
    assert extras["governance_ref"] == "gov-packet"


def test_build_proposal_context_extras_returns_empty_blocks_without_inputs(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    section = _section(planspace)

    extras = build_proposal_context_extras(section, planspace, None, None)

    assert extras == {
        "problems_block": "",
        "existing_note": "",
        "notes_block": "",
        "governance_ref": "",
    }


def test_build_impl_context_extras_collects_optional_refs_and_tooling(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    section = _section(planspace)
    artifacts = planspace / "artifacts"

    decisions_path = artifacts / "decisions" / "section-01.md"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text("## Decision\n", encoding="utf-8")
    corrections_path = artifacts / "signals" / "codemap-corrections.json"
    corrections_path.parent.mkdir(parents=True, exist_ok=True)
    corrections_path.write_text("{}\n", encoding="utf-8")
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text("# Codemap\n", encoding="utf-8")
    todos_path = artifacts / "todos" / "section-01-todos.md"
    todos_path.parent.mkdir(parents=True, exist_ok=True)
    todos_path.write_text("# TODOs\n", encoding="utf-8")
    tools_path = artifacts / "sections" / "section-01-tools-available.md"
    tools_path.parent.mkdir(parents=True, exist_ok=True)
    tools_path.write_text("# Tools\n", encoding="utf-8")

    extras = build_impl_context_extras(
        section,
        planspace,
        "repair the implementation drift",
        base_context={"governance_ref": "gov-packet"},
    )

    problems_path = artifacts / "impl-01-problems.md"

    assert problems_path.read_text(encoding="utf-8") == "repair the implementation drift"
    assert str(problems_path) in extras["problems_block"]
    assert str(decisions_path) in extras["decisions_block"]
    assert str(corrections_path) in extras["corrections_ref"]
    assert str(codemap_path) in extras["codemap_ref"]
    assert str(todos_path) in extras["todos_ref"]
    assert str(tools_path) in extras["tools_ref"]
    assert "tool-registry.json" in extras["tooling_block"]
    assert "section-01-tool-friction.json" in extras["tooling_block"]
    assert extras["governance_ref"] == "gov-packet"
