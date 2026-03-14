from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.path_registry import PathRegistry
from src.intake.repository.governance_loader import build_governance_indexes
from src.intake.service.governance_packet_builder import build_section_governance_packet


def test_build_section_governance_packet_uses_indexes_and_default_profile(
    tmp_path: Path,
) -> None:
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    (codespace / "governance" / "problems").mkdir(parents=True, exist_ok=True)
    (codespace / "governance" / "patterns").mkdir(parents=True, exist_ok=True)
    (codespace / "philosophy" / "profiles").mkdir(parents=True, exist_ok=True)

    (codespace / "governance" / "problems" / "index.md").write_text(
        "## PRB-0009: Problem Traceability\n\n"
        "**Status**: latent\n"
        "**Regions**: governance layer\n",
        encoding="utf-8",
    )
    (codespace / "governance" / "patterns" / "index.md").write_text(
        "## PAT-0003: Path Registry\n\n"
        "**Problem class**: path sprawl\n"
        "**Philosophy**: single source of truth\n"
        "**Canonical instance**: path_registry.py\n",
        encoding="utf-8",
    )
    (codespace / "philosophy" / "profiles" / "PHI-global.md").write_text(
        "## Values (priority order)\n\n1. Accuracy\n\n"
        "## Preferred Failure Mode\n\nFail closed.\n\n"
        "## Risk Posture\n\nConservative.\n\n"
        "## Anti-Patterns\n\n- Silent discard\n",
        encoding="utf-8",
    )
    (codespace / "philosophy" / "region-profile-map.md").write_text(
        "## Default\n\nAll regions: `PHI-global`\n",
        encoding="utf-8",
    )

    build_governance_indexes(codespace, planspace)

    # Pass a section summary that keyword-matches "governance" in the
    # problem's regions, so _filter_by_regions() can match
    packet_path = build_section_governance_packet(
        "01", planspace,
        section_summary="governance layer traceability",
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    assert packet["section"] == "01"
    # Problem matches via keyword overlap with section summary
    assert packet["candidate_problems"][0]["problem_id"] == "PRB-0009"
    # Pattern has no regions — should be included as ambiguous
    assert packet["candidate_patterns"][0]["pattern_id"] == "PAT-0003"
    assert packet["profiles"][0]["profile_id"] == "PHI-global"
    assert packet["governing_profile"] == "PHI-global"
    assert "archive_refs" in packet
    assert "governance_questions" in packet
    assert "applicability_state" in packet


def test_build_section_governance_packet_filters_patterns_by_regions(
    tmp_path: Path,
) -> None:
    """V3/V4 regression: patterns with regions should be filtered, not universal."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    (codespace / "governance" / "problems").mkdir(parents=True, exist_ok=True)
    (codespace / "governance" / "patterns").mkdir(parents=True, exist_ok=True)
    (codespace / "philosophy" / "profiles").mkdir(parents=True, exist_ok=True)

    (codespace / "governance" / "problems" / "index.md").write_text(
        "## PRB-0001: Test Problem\n\n"
        "**Status**: active\n"
        "**Regions**: governance layer\n",
        encoding="utf-8",
    )
    # Two patterns: one universal (no regions), one scoped to 'research'
    (codespace / "governance" / "patterns" / "index.md").write_text(
        "## PAT-0001: Universal Pattern\n\n"
        "**Problem class**: all\n"
        "**Philosophy**: always applies\n"
        "**Canonical instance**: everywhere.py\n\n---\n\n"
        "## PAT-0007: Scoped Pattern\n\n"
        "**Problem class**: status tracking\n"
        "**Regions**: research, retriggerable workflows\n"
        "**Solution surfaces**: Research orchestration status.\n"
        "**Philosophy**: precision over coarseness\n"
        "**Canonical instance**: orchestrator.py\n",
        encoding="utf-8",
    )
    (codespace / "philosophy" / "profiles" / "PHI-global.md").write_text(
        "## Values (priority order)\n\n1. Accuracy\n\n"
        "## Preferred Failure Mode\n\nFail closed.\n\n"
        "## Risk Posture\n\nConservative.\n\n"
        "## Anti-Patterns\n\n- Silent discard\n",
        encoding="utf-8",
    )
    (codespace / "philosophy" / "region-profile-map.md").write_text(
        "## Default\n\nAll regions: `PHI-global`\n",
        encoding="utf-8",
    )

    build_governance_indexes(codespace, planspace)

    # Section summary about "governance" — should not match "research" pattern
    packet_path = build_section_governance_packet(
        "01", planspace,
        section_summary="governance packet builder for advisory context",
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    # PAT-0001 (no regions) should be included but marked as ambiguous
    # per PAT-0011: missing applicability metadata = ambiguity, not universal
    pattern_ids = [p["pattern_id"] for p in packet["candidate_patterns"]]
    assert "PAT-0001" in pattern_ids

    basis = packet.get("applicability_basis", {}).get("patterns", "")
    # Basis must reflect that PAT-0001 has no_regions (ambiguous)
    assert "no_regions" in basis
    # Packet must flag ambiguity
    assert packet["applicability_state"] == "ambiguous_applicability"
    assert len(packet["governance_questions"]) > 0

    # PAT-0007 has regions=["research", "retriggerable workflows"] —
    # summary is about "governance", so it should be filtered out
    assert "PAT-0007" not in pattern_ids


def test_build_section_governance_packet_handles_missing_indexes(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    codespace.mkdir()

    packet_path = build_section_governance_packet("02", planspace)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    assert packet["section"] == "02"
    assert packet["candidate_problems"] == []
    assert packet["candidate_patterns"] == []
    assert packet["profiles"] == []
    assert packet["region_profile_map"] == {"default": "", "overrides": {}}
    assert packet["governing_profile"] == ""
    assert packet["governance_questions"] == []
    assert packet["applicability_state"] == "no_applicable_governance"
    assert "archive_refs" in packet
