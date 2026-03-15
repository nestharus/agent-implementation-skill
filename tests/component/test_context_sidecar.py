"""Component tests for context sidecar resolution."""

from __future__ import annotations

import json

from containers import Services
from dispatch.service.context_sidecar import ContextSidecar, parse_context_field
from orchestrator.path_registry import PathRegistry


def test_parse_context_field_handles_missing_frontmatter_and_lists(tmp_path) -> None:
    missing = parse_context_field(str(tmp_path / "missing.md"))
    plain = tmp_path / "plain.md"
    plain.write_text("# No frontmatter\n", encoding="utf-8")
    block = tmp_path / "block.md"
    block.write_text(
        "---\n"
        "context:\n"
        "  - section_spec\n"
        "  - codemap\n"
        "title: Example\n"
        "---\n",
        encoding="utf-8",
    )
    inline = tmp_path / "inline.md"
    inline.write_text(
        "---\n"
        "context: [section_spec, 'model_policy']\n"
        "---\n",
        encoding="utf-8",
    )

    assert missing == []
    assert parse_context_field(str(plain)) == []
    assert parse_context_field(str(block)) == ["section_spec", "codemap"]
    assert parse_context_field(str(inline)) == ["section_spec", "model_policy"]


def test_resolve_context_skips_unknown_categories_and_returns_empty_for_missing(tmp_path) -> None:
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(
        "---\n"
        "context:\n"
        "  - section_spec\n"
        "  - unknown_key\n"
        "  - strategic_state\n"
        "---\n",
        encoding="utf-8",
    )

    result = ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path, section="03")

    assert result == {
        "section_spec": "",
        "strategic_state": "",
    }


def test_resolve_context_appends_codemap_corrections(tmp_path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts = tmp_path / "artifacts"
    signals = artifacts / "signals"
    (artifacts / "codemap.md").write_text("Base codemap", encoding="utf-8")
    (signals / "codemap-corrections.json").write_text(
        json.dumps({"section-01": ["src/app.py"]}),
        encoding="utf-8",
    )
    agent_file = tmp_path / "agent.md"
    agent_file.write_text("---\ncontext:\n  - codemap\n---\n", encoding="utf-8")

    result = ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path)

    assert "Base codemap" in result["codemap"]
    assert "Codemap Corrections (authoritative)" in result["codemap"]
    assert "section-01" in result["codemap"]


def test_resolve_context_related_files_prefers_json_sidecar(tmp_path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts = tmp_path / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    (sections / "section-07.md").write_text(
        "# Section\n\n## Related Files\n- fallback.py\n\n## Next\nbody\n",
        encoding="utf-8",
    )
    (signals / "related-files-07.json").write_text(
        json.dumps(["preferred.py"]),
        encoding="utf-8",
    )
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(
        "---\ncontext:\n  - related_files\n---\n",
        encoding="utf-8",
    )

    result = ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path, section="07")

    assert result["related_files"] == '["preferred.py"]'


def test_resolve_context_related_files_falls_back_to_markdown_block(tmp_path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts = tmp_path / "artifacts" / "sections"
    (artifacts / "section-09.md").write_text(
        "# Section\n\n## Related Files\n- one.py\n- two.py\n\n## Notes\nrest\n",
        encoding="utf-8",
    )
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(
        "---\ncontext:\n  - related_files\n---\n",
        encoding="utf-8",
    )

    result = ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path, section="09")

    assert result["related_files"] == "## Related Files\n- one.py\n- two.py"


def test_resolve_context_flow_context_requires_exactly_one_file(tmp_path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    flows = tmp_path / "artifacts" / "flows"
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(
        "---\ncontext:\n  - flow_context\n---\n",
        encoding="utf-8",
    )

    assert ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path)["flow_context"] == ""

    (flows / "task-1-context.json").write_text('{"task": 1}', encoding="utf-8")
    assert ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path)["flow_context"] == '{"task": 1}'

    (flows / "task-2-context.json").write_text('{"task": 2}', encoding="utf-8")
    assert ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path)["flow_context"] == ""


def test_resolve_context_reads_governance_packet(tmp_path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    governance_dir = tmp_path / "artifacts" / "governance"
    packet_path = governance_dir / "section-03-governance-packet.json"
    packet_path.write_text('{"section": "03", "profiles": []}', encoding="utf-8")
    agent_file = tmp_path / "agent.md"
    agent_file.write_text(
        "---\ncontext:\n  - governance\n---\n",
        encoding="utf-8",
    )

    result = ContextSidecar(artifact_io=Services.artifact_io()).resolve_context(str(agent_file), tmp_path, section="03")

    assert result["governance"] == '{"section": "03", "profiles": []}'


def test_materialize_context_sidecar_writes_pretty_json_with_trailing_newline(tmp_path) -> None:
    PathRegistry(tmp_path).ensure_artifacts_tree()
    artifacts = tmp_path / "artifacts" / "sections"
    (artifacts / "section-11.md").write_text("# Section 11\n", encoding="utf-8")
    agent_file = tmp_path / "section-agent.md"
    agent_file.write_text(
        "---\ncontext:\n  - section_spec\n---\n",
        encoding="utf-8",
    )

    sidecar = ContextSidecar(artifact_io=Services.artifact_io()).materialize_context_sidecar(str(agent_file), tmp_path, section="11")

    assert sidecar == PathRegistry(tmp_path).context_sidecar("section-agent")
    assert sidecar is not None
    assert sidecar.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(sidecar.read_text(encoding="utf-8")) == {
        "section_spec": "# Section 11\n",
    }


def test_materialize_context_sidecar_returns_none_without_declared_context(tmp_path) -> None:
    agent_file = tmp_path / "agent.md"
    agent_file.write_text("# No context\n", encoding="utf-8")

    assert ContextSidecar(artifact_io=Services.artifact_io()).materialize_context_sidecar(str(agent_file), tmp_path) is None
