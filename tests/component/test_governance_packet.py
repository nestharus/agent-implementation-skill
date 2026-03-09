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
    assert packet["problems"][0]["problem_id"] == "PRB-0009"
    assert packet["patterns"][0]["pattern_id"] == "PAT-0003"
    assert packet["profiles"][0]["profile_id"] == "PHI-global"
    assert packet["governing_profile"] == "PHI-global"


def test_build_section_governance_packet_handles_missing_indexes(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    codespace.mkdir()

    packet_path = build_section_governance_packet("02", planspace, codespace)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    assert packet == {
        "section": "02",
        "problems": [],
        "patterns": [],
        "profiles": [],
        "region_profile_map": {"default": "", "overrides": {}},
        "governing_profile": "",
    }
