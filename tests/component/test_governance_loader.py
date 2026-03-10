from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.governance.loader import (
    build_governance_indexes,
    parse_pattern_index,
    parse_philosophy_profiles,
    parse_problem_index,
    parse_region_profile_map,
)


def test_governance_loader_parses_markdown_indexes(tmp_path: Path) -> None:
    codespace = tmp_path / "codespace"
    (codespace / "governance" / "problems").mkdir(parents=True, exist_ok=True)
    (codespace / "governance" / "patterns").mkdir(parents=True, exist_ok=True)
    (codespace / "philosophy" / "profiles").mkdir(parents=True, exist_ok=True)

    (codespace / "governance" / "problems" / "index.md").write_text(
        "# Problem Archive\n\n"
        "## PRB-0001: Problem One\n\n"
        "**Status**: active\n"
        "**Provenance**: user-authored\n"
        "**Regions**: flow system, section loop\n"
        "**Solution surfaces**: traceability, prompts\n"
        "**Related patterns**: PAT-0001, PAT-0003\n",
        encoding="utf-8",
    )
    (codespace / "governance" / "patterns" / "index.md").write_text(
        "# Pattern Archive\n\n"
        "## Substrate Invariants\n\n"
        "- not a numbered pattern\n\n"
        "## PAT-0001: Corruption Preservation\n\n"
        "**Problem class**: malformed JSON\n"
        "**Philosophy**: fail closed\n"
        "**Canonical instance**: artifact_io.py\n"
        "**Known instances**:\n"
        "- artifact_io.py\n"
        "- orchestrator.py\n",
        encoding="utf-8",
    )
    (codespace / "philosophy" / "profiles" / "PHI-global.md").write_text(
        "# PHI-global: Global Philosophy Profile\n\n"
        "## Values (priority order)\n\n"
        "1. Accuracy over shortcuts\n"
        "2. Evidence preservation\n\n"
        "## Preferred Failure Mode\n\n"
        "Fail closed.\n\n"
        "## Risk Posture\n\n"
        "Conservative.\n\n"
        "## Anti-Patterns\n\n"
        "- Silent discard\n"
        "- Ad hoc paths\n",
        encoding="utf-8",
    )
    (codespace / "philosophy" / "region-profile-map.md").write_text(
        "# Region-Profile Map\n\n"
        "## Default\n\n"
        "All regions: `PHI-global`\n\n"
        "## Overrides\n\n"
        "- section-02: `PHI-special`\n",
        encoding="utf-8",
    )

    problems = parse_problem_index(codespace)
    patterns = parse_pattern_index(codespace)
    profiles = parse_philosophy_profiles(codespace)
    region_map = parse_region_profile_map(codespace)

    assert problems == [{
        "problem_id": "PRB-0001",
        "title": "Problem One",
        "status": "active",
        "provenance": "user-authored",
        "regions": ["flow system", "section loop"],
        "solution_surfaces": "traceability, prompts",
        "related_patterns": ["PAT-0001", "PAT-0003"],
    }]
    assert patterns == [{
        "pattern_id": "PAT-0001",
        "title": "Corruption Preservation",
        "problem_class": "malformed JSON",
        "regions": [],
        "solution_surfaces": "",
        "philosophy": "fail closed",
        "canonical_instance": "artifact_io.py",
        "known_instances": ["artifact_io.py", "orchestrator.py"],
        "template": [],
        "conformance": "",
    }]
    assert profiles == [{
        "profile_id": "PHI-global",
        "values": ["Accuracy over shortcuts", "Evidence preservation"],
        "failure_mode": "Fail closed.",
        "risk_posture": "Conservative.",
        "anti_patterns": ["Silent discard", "Ad hoc paths"],
    }]
    assert region_map == {
        "default": "PHI-global",
        "overrides": {"section-02": "PHI-special"},
    }


def test_build_governance_indexes_writes_empty_indexes_when_docs_missing(
    tmp_path: Path,
) -> None:
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()

    result = build_governance_indexes(codespace, planspace)

    assert result is True
    assert json.loads(
        (planspace / "artifacts" / "governance" / "problem-index.json").read_text(
            encoding="utf-8"
        )
    ) == []
    assert json.loads(
        (planspace / "artifacts" / "governance" / "pattern-index.json").read_text(
            encoding="utf-8"
        )
    ) == []
    assert json.loads(
        (planspace / "artifacts" / "governance" / "profile-index.json").read_text(
            encoding="utf-8"
        )
    ) == []
    assert json.loads(
        (
            planspace
            / "artifacts"
            / "governance"
            / "region-profile-map.json"
        ).read_text(encoding="utf-8")
    ) == {"default": "", "overrides": {}}
