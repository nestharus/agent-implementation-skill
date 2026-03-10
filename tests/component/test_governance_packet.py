from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.governance.loader import build_governance_indexes
from src.scripts.lib.governance.packet import build_section_governance_packet


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

    packet_path = build_section_governance_packet("01", planspace, codespace)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    assert packet["section"] == "01"
    assert packet["candidate_problems"][0]["problem_id"] == "PRB-0009"
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
        "01", planspace, codespace,
        section_summary="governance packet builder for advisory context",
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    # Universal pattern (no regions) should always be included
    pattern_ids = [p["pattern_id"] for p in packet["candidate_patterns"]]
    assert "PAT-0001" in pattern_ids

    # PAT-0007 has regions=["research", "retriggerable workflows"] —
    # if the summary doesn't overlap, it should be filtered out or
    # marked as broad fallback (ambiguous)
    if "PAT-0007" in pattern_ids:
        # If it IS included, the basis must indicate broad fallback / ambiguity
        basis = packet.get("applicability_basis", {}).get("patterns", "")
        assert "broad_fallback" in basis or "ambiguous" in basis


def test_build_section_governance_packet_handles_missing_indexes(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    codespace.mkdir()

    packet_path = build_section_governance_packet("02", planspace, codespace)
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
