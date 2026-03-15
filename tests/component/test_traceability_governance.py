from __future__ import annotations

import json
from pathlib import Path

from containers import Services
from orchestrator.types import Section
from src.orchestrator.path_registry import PathRegistry
from src.implementation.service.traceability_writer import TraceabilityWriter


def _section(planspace: Path) -> Section:
    section_path = planspace / "artifacts" / "sections" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    return Section(number="01", path=section_path, related_files=["src/main.py"])


def testwrite_traceability_index_includes_governance_block(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    codespace = tmp_path / "codespace"
    section = _section(planspace)
    artifacts = planspace / "artifacts"
    (artifacts / "sections" / "section-01-proposal-excerpt.md").write_text(
        "proposal excerpt",
        encoding="utf-8",
    )
    (artifacts / "sections" / "section-01-alignment-excerpt.md").write_text(
        "alignment excerpt",
        encoding="utf-8",
    )
    (artifacts / "proposals" / "section-01-integration-proposal.md").write_text(
        "# Proposal\n",
        encoding="utf-8",
    )
    governance_packet = (
        artifacts / "governance" / "section-01-governance-packet.json"
    )
    governance_packet.write_text('{"section": "01"}\n', encoding="utf-8")

    writer = TraceabilityWriter(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
        logger=Services.logger(),
        section_alignment=Services.section_alignment(),
    )
    writer.write_traceability_index(planspace, section, ["src/main.py"])

    trace = json.loads(
        (artifacts / "trace" / "section-01.json").read_text(encoding="utf-8")
    )
    assert trace["governance"]["packet_path"] == str(governance_packet)
    assert trace["governance"]["packet_hash"]
    assert trace["governance"]["problem_ids"] == []
    assert trace["governance"]["pattern_ids"] == []
    assert trace["governance"]["profile_id"] == ""


def test_update_trace_governance_merges_without_duplicates(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    trace_path = planspace / "artifacts" / "trace" / "section-01.json"
    trace_path.write_text(
        json.dumps(
            {
                "section": "01",
                "governance": {
                    "packet_path": "packet.json",
                    "packet_hash": "abc",
                    "problem_ids": ["PRB-0001"],
                    "pattern_ids": ["PAT-0001"],
                    "profile_id": "",
                },
            }
        ),
        encoding="utf-8",
    )

    writer = TraceabilityWriter(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
        logger=Services.logger(),
        section_alignment=Services.section_alignment(),
    )
    updated = writer.update_trace_governance(
        planspace,
        "01",
        problem_ids=["PRB-0001", "PRB-0009"],
        pattern_ids=["PAT-0001", "PAT-0003"],
        profile_id="PHI-global",
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert updated is True
    assert trace["governance"]["problem_ids"] == ["PRB-0001", "PRB-0009"]
    assert trace["governance"]["pattern_ids"] == ["PAT-0001", "PAT-0003"]
    assert trace["governance"]["profile_id"] == "PHI-global"
