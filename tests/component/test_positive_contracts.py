"""Positive contract tests (PAT-0015).

Lock recurring projection classes so manual sweeps are not the only
defence against drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "src"
GOV = Path(__file__).resolve().parent.parent.parent / "governance"
ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# 1. Doctrine projection — agent/template surfaces must use the
#    authoritative heading, not the stale "Zero Risk Tolerance" variant.
# ---------------------------------------------------------------------------

_DOCTRINE_SURFACES = [
    "proposal/agents/integration-proposer.md",
    "templates/dispatch/integration-proposal.md",
    "implementation/agents/implementation-strategist.md",
    "templates/dispatch/strategic-implementation.md",
    "coordination/agents/coordination-planner.md",
    "coordination/agents/bridge-agent.md",
    "risk/agents/risk-assessor.md",
    "scan/agents/substrate-shard-explorer.md",
    "scan/agents/substrate-pruner.md",
    "scan/agents/substrate-seeder.md",
]


@pytest.mark.parametrize("rel_path", _DOCTRINE_SURFACES)
def test_doctrine_heading_uses_authoritative_wording(rel_path: str) -> None:
    """Agent/template files must say 'Zero Tolerance for Fabrication',
    not the stale 'Zero Risk Tolerance'."""
    path = SRC / rel_path
    if not path.exists():
        pytest.skip(f"{path} does not exist")
    text = path.read_text(encoding="utf-8")
    assert "Zero Tolerance for Fabrication" in text, (
        f"{rel_path} still uses stale doctrine heading"
    )
    assert "Zero Risk Tolerance" not in text, (
        f"{rel_path} still contains 'Zero Risk Tolerance'"
    )


@pytest.mark.parametrize("rel_path", _DOCTRINE_SURFACES)
def test_doctrine_no_trivially_small_exception(rel_path: str) -> None:
    """Agent/template files must not carry the 'trivially small' shortcut
    permission that conflicts with the authoritative doctrine."""
    path = SRC / rel_path
    if not path.exists():
        pytest.skip(f"{path} does not exist")
    text = path.read_text(encoding="utf-8")
    assert "trivially small" not in text.lower(), (
        f"{rel_path} still contains 'trivially small' exception"
    )


# ---------------------------------------------------------------------------
# 2. Reconciliation family — PathRegistry accessors and consumer migration.
# ---------------------------------------------------------------------------

def test_reconciliation_requests_use_path_registry() -> None:
    """reconciliation/repository/queue.py must import PathRegistry,
    not construct paths manually."""
    queue_path = SRC / "reconciliation" / "repository" / "queue.py"
    text = queue_path.read_text(encoding="utf-8")
    assert "PathRegistry" in text, (
        "queue.py does not import PathRegistry"
    )
    assert '"reconciliation-requests"' not in text, (
        "queue.py still hard-codes 'reconciliation-requests' path segment"
    )


def test_reconciliation_summary_uses_path_registry() -> None:
    """cross_section_reconciler.py must use PathRegistry for the
    reconciliation summary path."""
    reconciler = SRC / "reconciliation" / "engine" / "cross_section_reconciler.py"
    text = reconciler.read_text(encoding="utf-8")
    assert '"reconciliation-summary.json"' not in text, (
        "cross_section_reconciler.py still hard-codes summary path"
    )


def test_load_reconciliation_result_accepts_planspace() -> None:
    """load_reconciliation_result must accept planspace (not mixed root)."""
    reconciler = SRC / "reconciliation" / "engine" / "cross_section_reconciler.py"
    text = reconciler.read_text(encoding="utf-8")
    # The mixed-root normalization line should be gone
    assert "section_dir.parent" not in text, (
        "load_reconciliation_result still normalizes mixed-root input"
    )


# ---------------------------------------------------------------------------
# 3. system-synthesis.md counts — must match live archives.
# ---------------------------------------------------------------------------

def test_system_synthesis_problem_count_matches_archive() -> None:
    """system-synthesis.md must report the correct problem count."""
    synthesis = ROOT / "system-synthesis.md"
    problems = GOV / "problems" / "index.md"
    if not synthesis.exists() or not problems.exists():
        pytest.skip("governance files not found")
    prob_count = len(re.findall(r"^## PRB-\d+", problems.read_text(encoding="utf-8"), re.MULTILINE))
    synth_text = synthesis.read_text(encoding="utf-8")
    match = re.search(r"(\d+)\s+problems", synth_text)
    assert match, "system-synthesis.md does not mention a problem count"
    assert int(match.group(1)) == prob_count, (
        f"system-synthesis.md says {match.group(1)} problems, "
        f"archive has {prob_count}"
    )


def test_system_synthesis_pattern_count_matches_catalog() -> None:
    """system-synthesis.md must report the correct pattern count."""
    synthesis = ROOT / "system-synthesis.md"
    patterns = GOV / "patterns" / "index.md"
    if not synthesis.exists() or not patterns.exists():
        pytest.skip("governance files not found")
    pat_count = len(re.findall(r"^## PAT-\d+", patterns.read_text(encoding="utf-8"), re.MULTILINE))
    synth_text = synthesis.read_text(encoding="utf-8")
    # Find all pattern count mentions and check the one near "pattern catalog"
    matches = re.findall(r"(\d+)\s+patterns", synth_text)
    assert matches, "system-synthesis.md does not mention a pattern count"
    assert any(int(m) == pat_count for m in matches), (
        f"system-synthesis.md says {matches} patterns, "
        f"catalog has {pat_count}"
    )
