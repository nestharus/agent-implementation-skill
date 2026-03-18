from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.path_registry import PathRegistry
from src.containers import ArtifactIOService
from src.intake.repository.governance_loader import (
    GovernanceLoader,
    bootstrap_governance_if_missing,
    extract_problems_from_spec,
    parse_philosophy_profiles,
    seed_governance_from_alignment,
)


def _loader() -> GovernanceLoader:
    return GovernanceLoader(artifact_io=ArtifactIOService())


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

    loader = _loader()
    problems = loader.parse_problem_index(codespace)
    patterns = loader.parse_pattern_index(codespace)
    profiles = parse_philosophy_profiles(codespace)
    region_map = loader.parse_region_profile_map(codespace)

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
    PathRegistry(planspace).ensure_artifacts_tree()

    result = _loader().build_governance_indexes(codespace, planspace)

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


def test_pattern_index_preserves_wrapped_bullets_and_numbered_templates(
    tmp_path: Path,
) -> None:
    """Representative fixture: real catalog shapes with continuation lines.

    Verifies that:
    - Wrapped (continuation) bullet items are joined, not truncated
    - Numbered template items are parsed as individual array entries
    - Regions and solution_surfaces are extracted from field_map
    - Conformance multiline text is joined
    """
    codespace = tmp_path / "codespace"
    (codespace / "governance" / "patterns").mkdir(parents=True, exist_ok=True)

    (codespace / "governance" / "patterns" / "index.md").write_text(
        "# Pattern Archive\n\n"
        "## PAT-0001: Corruption Preservation\n\n"
        "**Problem class**: Structured artifact read/write in a multi-agent\n"
        "system where any writer may produce malformed output.\n\n"
        "**Regions**: all artifact readers, JSON parsing, prompt output\n"
        "consumption\n\n"
        "**Solution surfaces**: Corruption preservation, fail-closed defaults,\n"
        "structured validation, malformed-file renaming.\n\n"
        "**Philosophy**: Fail-closed. Evidence preservation over silent\n"
        "discard.\n\n"
        "**Template**:\n"
        "1. Use `read_json(path)` or a typed loader built on it for\n"
        "   syntax-level parsing.\n"
        "2. If parse succeeds, validate schema shape and semantic invariants.\n"
        "3. On malformed JSON or schema mismatch, call `rename_malformed(path)`.\n"
        "4. Return `None` or a documented fail-closed default.\n"
        "5. Do not invent local corruption-preservation conventions unless the\n"
        "   pattern is explicitly updated.\n\n"
        "**Canonical instance**: `load_surface_registry()` in\n"
        "`section_loop/intent/surfaces.py`\n\n"
        "**Known instances**:\n"
        "- `src/scripts/lib/core/artifact_io.py` — `read_json()` /\n"
        "  `rename_malformed()` primitives\n"
        "- `src/scripts/section_loop/intent/surfaces.py` — surface registry +\n"
        "  research surface validation\n"
        "- `src/scripts/lib/research/orchestrator.py` — research plan / status\n"
        "  loaders\n\n"
        "**Conformance**: Any new structured artifact reader MUST follow this\n"
        "pattern. No silent `json.loads()` with bare except.\n\n"
        "---\n\n"
        "## PAT-0002: Prompt Safety\n\n"
        "**Problem class**: Prompt injection.\n"
        "**Philosophy**: Trust boundary.\n"
        "**Known instances**:\n"
        "- prompt_writer.py\n"
        "- plan_executor.py\n",
        encoding="utf-8",
    )

    patterns = _loader().parse_pattern_index(codespace)

    assert len(patterns) == 2

    pat1 = patterns[0]
    assert pat1["pattern_id"] == "PAT-0001"

    # Regions: multiline field joined
    assert pat1["regions"] == [
        "all artifact readers",
        "JSON parsing",
        "prompt output consumption",
    ]

    # Template: 5 numbered items, each preserved as individual entries
    assert len(pat1["template"]) == 5
    assert pat1["template"][0].startswith("Use `read_json(path)`")
    assert "syntax-level parsing." in pat1["template"][0]  # continuation joined
    assert pat1["template"][2] == (
        "On malformed JSON or schema mismatch, call `rename_malformed(path)`."
    )
    assert "explicitly updated." in pat1["template"][4]  # continuation joined

    # Known instances: 3 items, wrapped bullets joined
    assert len(pat1["known_instances"]) == 3
    assert "primitives" in pat1["known_instances"][0]  # continuation joined
    assert "research surface validation" in pat1["known_instances"][1]

    # Conformance: multiline joined
    assert "MUST follow" in pat1["conformance"]
    assert "bare except" in pat1["conformance"]

    # PAT-0002 also parsed
    assert patterns[1]["pattern_id"] == "PAT-0002"
    assert patterns[1]["known_instances"] == ["prompt_writer.py", "plan_executor.py"]


def test_bootstrap_governance_creates_scaffolding_for_greenfield(
    tmp_path: Path,
) -> None:
    """When codespace has no governance docs, bootstrap creates scaffolding."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    result = bootstrap_governance_if_missing(codespace)

    assert result is True
    assert (codespace / "governance" / "problems" / "index.md").exists()
    assert (codespace / "governance" / "patterns" / "index.md").exists()
    assert (codespace / "governance" / "risk-register.md").exists()
    assert (codespace / "system-synthesis.md").exists()

    # Verify content is parseable by the loader
    loader = _loader()
    problems = loader.parse_problem_index(codespace)
    patterns = loader.parse_pattern_index(codespace)
    assert problems == []
    assert patterns == []


def test_bootstrap_governance_skips_when_governance_exists(
    tmp_path: Path,
) -> None:
    """When codespace already has governance docs, bootstrap is a no-op."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    (codespace / "governance" / "problems").mkdir(parents=True)
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    (codespace / "governance" / "problems" / "index.md").write_text(
        "# Problem Archive\n\n## PRB-0001: Existing\n\n**Status**: active\n",
        encoding="utf-8",
    )

    result = bootstrap_governance_if_missing(codespace)

    assert result is False
    # Existing content is preserved
    assert "PRB-0001" in (
        codespace / "governance" / "problems" / "index.md"
    ).read_text(encoding="utf-8")


def test_bootstrap_then_build_indexes_produces_valid_planspace(
    tmp_path: Path,
) -> None:
    """Bootstrap + build_governance_indexes produces valid JSON indexes."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    bootstrap_governance_if_missing(codespace)
    result = _loader().build_governance_indexes(codespace, planspace)

    assert result is True
    gov_dir = planspace / "artifacts" / "governance"
    assert json.loads(
        (gov_dir / "problem-index.json").read_text(encoding="utf-8")
    ) == []
    assert json.loads(
        (gov_dir / "pattern-index.json").read_text(encoding="utf-8")
    ) == []
    status = json.loads(
        (gov_dir / "index-status.json").read_text(encoding="utf-8")
    )
    assert status["ok"] is True


def test_related_files_signal_paths_are_distinct(tmp_path: Path) -> None:
    """Contract test: scan-stage and substrate-stage related-files signals
    use distinct, registry-owned path shapes."""
    registry = PathRegistry(tmp_path)

    scan_signal = registry.scan_related_files_update_signal("section-03")
    substrate_dir = registry.related_files_update_dir()

    # Scan-stage: flat signal file in signals/
    assert scan_signal.parent == registry.signals_dir()
    assert scan_signal.name == "section-03-related-files-update.json"

    # Substrate-stage: nested in signals/related-files-update/
    assert substrate_dir == registry.signals_dir() / "related-files-update"
    assert substrate_dir != scan_signal.parent

    # The two families are distinct — no path collision
    substrate_signal = substrate_dir / "section-03.json"
    assert scan_signal != substrate_signal


# ---------------------------------------------------------------------------
# Alignment-based governance seeding tests
# ---------------------------------------------------------------------------

_SAMPLE_ALIGNMENT = (
    "# Global Alignment\n\n"
    "## Shape Constraints\n\n"
    "- All API endpoints must validate input schemas before processing\n"
    "- Database access must go through the repository layer\n\n"
    "## Cross-Cutting Conventions\n\n"
    "- Use structured logging with correlation IDs\n"
    "- Errors must propagate with context, never silently swallowed\n\n"
    "## Anti-Patterns to Avoid\n\n"
    "- Direct SQL queries outside repository classes\n"
    "- Catching broad exceptions without re-raising or logging\n"
)


def _setup_scaffolded_codespace(codespace: Path) -> None:
    """Create a codespace with scaffold governance docs."""
    bootstrap_governance_if_missing(codespace)


def _setup_planspace_with_alignment(planspace: Path, text: str = _SAMPLE_ALIGNMENT) -> None:
    """Create planspace with an alignment.md."""
    PathRegistry(planspace).ensure_artifacts_tree()
    (planspace / "artifacts" / "alignment.md").write_text(text, encoding="utf-8")


def test_governance_seeded_from_alignment(tmp_path: Path) -> None:
    """When alignment.md exists and governance docs are scaffolds, seeding
    populates them with parseable records."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()

    _setup_scaffolded_codespace(codespace)
    _setup_planspace_with_alignment(planspace)

    result = seed_governance_from_alignment(codespace, planspace)

    assert result is True

    # Constraints should have been seeded
    constraints_text = (codespace / "governance" / "constraints" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "## CON-0001:" in constraints_text
    assert "Shape Constraints" in constraints_text

    # Patterns should have been seeded
    patterns_text = (codespace / "governance" / "patterns" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "## PAT-0001:" in patterns_text
    assert "Cross-Cutting Conventions" in patterns_text

    # Problems should have been seeded
    problems_text = (codespace / "governance" / "problems" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "## PRB-0001:" in problems_text
    assert "Anti-Patterns to Avoid" in problems_text

    # Verify the records are parseable by the governance loader
    loader = _loader()
    constraints = loader.parse_constraint_index(codespace)
    assert len(constraints) >= 1
    assert constraints[0]["constraint_id"] == "CON-0001"
    assert constraints[0]["provenance"] == "alignment-seed"

    patterns = loader.parse_pattern_index(codespace)
    assert len(patterns) >= 1
    assert patterns[0]["pattern_id"] == "PAT-0001"

    problems = loader.parse_problem_index(codespace)
    assert len(problems) >= 1
    assert problems[0]["problem_id"] == "PRB-0001"
    assert problems[0]["provenance"] == "alignment-seed"


def test_governance_not_reseeded(tmp_path: Path) -> None:
    """When governance docs already have real content, seeding is skipped."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()

    _setup_scaffolded_codespace(codespace)
    _setup_planspace_with_alignment(planspace)

    # First seeding works
    assert seed_governance_from_alignment(codespace, planspace) is True

    # Second call is skipped — docs now have real records, not scaffolds
    assert seed_governance_from_alignment(codespace, planspace) is False

    # Content is unchanged from first seeding
    constraints_text = (codespace / "governance" / "constraints" / "index.md").read_text(
        encoding="utf-8"
    )
    assert constraints_text.count("## CON-0001:") == 1


def test_governance_seeding_skipped_without_alignment(tmp_path: Path) -> None:
    """When alignment.md does not exist, seeding is skipped."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _setup_scaffolded_codespace(codespace)

    result = seed_governance_from_alignment(codespace, planspace)
    assert result is False


def test_build_governance_indexes_seeds_from_alignment(tmp_path: Path) -> None:
    """build_governance_indexes integrates seeding: scaffolded codespace
    with alignment.md produces populated JSON indexes."""
    codespace = tmp_path / "codespace"
    planspace = tmp_path / "planspace"
    codespace.mkdir()
    planspace.mkdir()
    _setup_planspace_with_alignment(planspace)

    loader = _loader()
    result = loader.build_governance_indexes(codespace, planspace)

    assert result is True

    # JSON indexes should be populated, not empty
    gov_dir = planspace / "artifacts" / "governance"
    constraint_index = json.loads(
        (gov_dir / "constraint-index.json").read_text(encoding="utf-8")
    )
    assert len(constraint_index) >= 1
    assert constraint_index[0]["constraint_id"] == "CON-0001"

    pattern_index = json.loads(
        (gov_dir / "pattern-index.json").read_text(encoding="utf-8")
    )
    assert len(pattern_index) >= 1

    problem_index = json.loads(
        (gov_dir / "problem-index.json").read_text(encoding="utf-8")
    )
    assert len(problem_index) >= 1


# ---------------------------------------------------------------------------
# Spec-direct problem extraction tests
# ---------------------------------------------------------------------------


def test_extract_problems_from_spec_finds_problem_signals(tmp_path: Path) -> None:
    """extract_problems_from_spec extracts sections with problem-signal language."""
    spec_path = tmp_path / "spec.md"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    spec_path.write_text(
        "# Project Spec\n\n"
        "## Requirements\n\n"
        "The system must handle concurrent requests.\n\n"
        "## Known Risks and Concerns\n\n"
        "- Race condition risk in the queue processor\n"
        "- Concern about memory usage under high load\n\n"
        "## Architecture\n\n"
        "Use a microservice approach with message queues.\n",
        encoding="utf-8",
    )

    records = extract_problems_from_spec(spec_path, codespace)

    # "Requirements" has "must" -> extracted
    # "Known Risks and Concerns" has "risk" and "concern" -> extracted
    # "Architecture" has no problem signals -> skipped
    assert len(records) >= 2
    assert all(r["provenance"] == "doc-derived" for r in records)
    assert all(r["confidence"] == "medium" for r in records)
    assert any("risk" in r["title"].lower() or "risk" in r["body"].lower() for r in records)


def test_extract_problems_from_spec_empty_for_clean_spec(tmp_path: Path) -> None:
    """A spec with no problem-signal language produces no records."""
    spec_path = tmp_path / "spec.md"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    spec_path.write_text(
        "# Project Spec\n\n"
        "## Overview\n\n"
        "A simple hello world application.\n\n"
        "## Features\n\n"
        "- Greet the user by name\n"
        "- Support multiple languages\n",
        encoding="utf-8",
    )

    records = extract_problems_from_spec(spec_path, codespace)
    assert records == []


def test_extract_problems_from_spec_missing_file(tmp_path: Path) -> None:
    """Missing spec file returns empty list."""
    records = extract_problems_from_spec(
        tmp_path / "nonexistent.md", tmp_path / "codespace",
    )
    assert records == []


def test_extract_problems_from_spec_with_constraints(tmp_path: Path) -> None:
    """Constraint headings are picked up as problem records."""
    spec_path = tmp_path / "spec.md"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    spec_path.write_text(
        "# Spec\n\n"
        "## Constraints\n\n"
        "- Must not exceed 100ms response time\n"
        "- Must validate all user input\n\n"
        "## Implementation Notes\n\n"
        "Use Python 3.12.\n",
        encoding="utf-8",
    )

    records = extract_problems_from_spec(spec_path, codespace)
    assert len(records) >= 1
    assert any("constraint" in r["title"].lower() for r in records)
