"""Component tests for shared substrate prompt builders."""

from __future__ import annotations

from src.scan.substrate.prompt_builder import (
    write_pruner_prompt,
    write_seeder_prompt,
    write_shard_prompt,
)


def test_write_shard_prompt_includes_optional_context_paths(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    section_path = artifacts / "sections" / "section-01.md"
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("# Section 01\n", encoding="utf-8")
    (artifacts / "codemap.md").write_text("# Codemap\n", encoding="utf-8")
    proposal_excerpt = artifacts / "sections" / "section-01-proposal-excerpt.md"
    proposal_excerpt.write_text("proposal\n", encoding="utf-8")
    alignment_excerpt = artifacts / "sections" / "section-01-alignment-excerpt.md"
    alignment_excerpt.write_text("alignment\n", encoding="utf-8")
    problem_frame = artifacts / "sections" / "section-01-problem-frame.md"
    problem_frame.write_text("frame\n", encoding="utf-8")
    intent_dir = artifacts / "intent" / "sections" / "section-01"
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "problem.md").write_text("problem\n", encoding="utf-8")
    (intent_dir / "problem-alignment.md").write_text("rubric\n", encoding="utf-8")
    corrections = artifacts / "signals" / "codemap-corrections.json"
    corrections.parent.mkdir(parents=True, exist_ok=True)
    corrections.write_text("{}\n", encoding="utf-8")

    prompt_path = write_shard_prompt("01", section_path, planspace, codespace)
    content = prompt_path.read_text(encoding="utf-8")

    assert prompt_path.name == "shard-01.md"
    assert "Proposal excerpt" in content
    assert "Alignment excerpt" in content
    assert "Problem frame" in content
    assert "Codemap corrections" in content
    assert str(codespace) in content


def test_write_pruner_prompt_lists_targets_and_available_refs(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    (artifacts / "proposal.md").parent.mkdir(parents=True, exist_ok=True)
    (artifacts / "proposal.md").write_text("proposal\n", encoding="utf-8")
    (artifacts / "alignment.md").write_text("alignment\n", encoding="utf-8")
    (artifacts / "codemap.md").write_text("# Codemap\n", encoding="utf-8")
    philosophy = artifacts / "intent" / "global" / "philosophy.md"
    philosophy.parent.mkdir(parents=True, exist_ok=True)
    philosophy.write_text("philosophy\n", encoding="utf-8")

    prompt_path = write_pruner_prompt(planspace, codespace, ["01", "02", "03"])
    content = prompt_path.read_text(encoding="utf-8")

    assert prompt_path.name == "pruner.md"
    assert "Only these sections are in scope: 01, 02, 03" in content
    assert "Global proposal" in content
    assert "Global alignment" in content
    assert "Philosophy" in content


def test_write_seeder_prompt_points_to_outputs_and_optional_codemap(
    tmp_path,
) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    (artifacts / "codemap.md").parent.mkdir(parents=True, exist_ok=True)
    (artifacts / "codemap.md").write_text("# Codemap\n", encoding="utf-8")
    corrections = artifacts / "signals" / "codemap-corrections.json"
    corrections.parent.mkdir(parents=True, exist_ok=True)
    corrections.write_text("{}\n", encoding="utf-8")

    prompt_path = write_seeder_prompt(planspace, codespace)
    content = prompt_path.read_text(encoding="utf-8")

    assert prompt_path.name == "seeder.md"
    assert "Seed plan" in content
    assert "Codemap corrections" in content
    assert "seed-signal.json" in content
    assert str(codespace) in content
