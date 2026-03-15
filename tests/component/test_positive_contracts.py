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
    "implementation/agents/microstrategy-writer.md",
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


# ---------------------------------------------------------------------------
# 4. Governance archive reference integrity — known-instance paths must exist.
# ---------------------------------------------------------------------------

def _extract_known_instance_paths(text: str) -> list[str]:
    """Extract backtick-quoted src/ paths from pattern catalog text.

    Skips glob patterns (containing * or ?) since those are not literal files.
    """
    raw = re.findall(r"`(src/[^`]+\.py)`", text)
    return [p for p in raw if "*" not in p and "?" not in p]


def test_pattern_known_instance_paths_exist() -> None:
    """Every src/ path cited in governance/patterns/index.md must exist."""
    patterns = GOV / "patterns" / "index.md"
    if not patterns.exists():
        pytest.skip("patterns index not found")
    paths = _extract_known_instance_paths(patterns.read_text(encoding="utf-8"))
    assert paths, "no src/ paths found in pattern catalog"
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, (
        f"Pattern catalog references {len(missing)} dead path(s): {missing}"
    )


# ---------------------------------------------------------------------------
# 5. Services.* allowlist — production code must not use the container
#    outside sanctioned composition roots (PAT-0019).
# ---------------------------------------------------------------------------

# Sanctioned composition roots — these are allowed to use Services.*
_SANCTIONED_CONTAINER_SITES = {
    "containers.py",
    "scan/cli.py",
    "orchestrator/engine/pipeline_orchestrator.py",
    "orchestrator/engine/section_pipeline.py",
    "risk/engine/risk_assessor.py",
    "flow/engine/task_dispatcher.py",
    # scan-stage adapter/build helpers (explicitly scoped per PAT-0019)
    "scan/scan_dispatcher.py",
    "scan/explore/deep_scanner.py",
    # CLI / factory composition helpers
    "proposal/engine/proposal_phase.py",
    "scan/substrate/substrate_discoverer.py",
}

# Known PAT-0019 residue — quarantined, not sanctioned
_QUARANTINED_RESIDUE = {
    "staleness/service/section_alignment_checker.py",
    "staleness/service/global_alignment_rechecker.py",
    "dispatch/engine/section_dispatcher.py",
    "signals/service/section_communicator.py",
    "signals/service/message_poller.py",
    "signals/service/blocker_manager.py",
    "flow/service/task_request_ingestor.py",
}


def test_services_container_usage_is_bounded() -> None:
    """Production src/ files that import 'from containers import Services'
    must be either sanctioned composition roots or quarantined residue."""
    import_pattern = re.compile(r"from\s+containers\s+import\s+.*Services")
    violations = []
    for py_file in sorted(SRC.rglob("*.py")):
        rel = str(py_file.relative_to(SRC))
        if rel.startswith("scripts/"):
            continue
        text = py_file.read_text(encoding="utf-8")
        if not import_pattern.search(text):
            continue
        if rel in _SANCTIONED_CONTAINER_SITES or rel in _QUARANTINED_RESIDUE:
            continue
        violations.append(rel)
    assert not violations, (
        f"Unsanctioned Services.* usage in {len(violations)} file(s): {violations}"
    )


# ---------------------------------------------------------------------------
# 6. PAT-0005 centralization — no local model-policy fallback chains
#    at operational callsites.
# ---------------------------------------------------------------------------

# Operational files where policy.get("key", policy["other_key"]) is banned.
# Composition roots and test files are excluded.
_PAT0005_EXCLUDED_PREFIXES = ("scripts/", "containers.py")


def test_no_local_model_policy_fallback_chains() -> None:
    """Operational callsites must not use policy.get(key, policy[other_key])
    style fallback chains — use resolve(policy, key) instead (PAT-0005)."""
    fallback_pattern = re.compile(
        r"model_policy\.get\(\s*\"[^\"]+\"\s*,\s*"
        r"(?:ctx\.model_policy|policy|self\._policy)\[",
    )
    violations = []
    for py_file in sorted(SRC.rglob("*.py")):
        rel = str(py_file.relative_to(SRC))
        if any(rel.startswith(p) for p in _PAT0005_EXCLUDED_PREFIXES):
            continue
        text = py_file.read_text(encoding="utf-8")
        if fallback_pattern.search(text):
            violations.append(rel)
    assert not violations, (
        f"Local model-policy fallback chain in {len(violations)} file(s): "
        f"{violations} — use resolve(policy, key) instead"
    )


# ---------------------------------------------------------------------------
# 7. PAT-0015 rule 13 — derivation-based self-report truth locks.
# ---------------------------------------------------------------------------

def _derive_services_import_sites() -> set[str]:
    """Scan src/ for files that import 'from containers import ... Services'."""
    import_pattern = re.compile(r"from\s+containers\s+import\s+.*Services")
    sites: set[str] = set()
    for py_file in sorted(SRC.rglob("*.py")):
        rel = str(py_file.relative_to(SRC))
        if rel.startswith("scripts/"):
            continue
        text = py_file.read_text(encoding="utf-8")
        if import_pattern.search(text):
            sites.add(rel)
    return sites


def test_sanctioned_and_quarantined_sets_match_live_code() -> None:
    """Every file in the sanctioned/quarantined allowlists must actually
    import Services, and every importing file must be in one of the sets.
    This prevents dead entries from accumulating (PAT-0015 rule 13)."""
    live_sites = _derive_services_import_sites()
    published = _SANCTIONED_CONTAINER_SITES | _QUARANTINED_RESIDUE
    dead = published - live_sites
    assert not dead, (
        f"Allowlist contains {len(dead)} file(s) that no longer import "
        f"Services: {sorted(dead)}"
    )
    unlisted = live_sites - published
    assert not unlisted, (
        f"Unlisted Services import sites: {sorted(unlisted)}"
    )


def test_system_synthesis_di_boundary_is_truthful() -> None:
    """system-synthesis.md must not claim constructor fallbacks persist,
    and must reference PAT-0019/RISK-0008 as authoritative boundary
    sources (PAT-0015 rule 13)."""
    synthesis = ROOT / "system-synthesis.md"
    if not synthesis.exists():
        pytest.skip("system-synthesis.md not found")
    text = synthesis.read_text(encoding="utf-8")
    assert "constructor fallback" not in text.lower(), (
        "system-synthesis.md still claims constructor fallbacks persist"
    )
    assert "PAT-0019" in text, (
        "system-synthesis.md does not reference PAT-0019 for DI boundary"
    )
    assert "RISK-0008" in text, (
        "system-synthesis.md does not reference RISK-0008 for DI boundary"
    )


# Key philosophy constraint bands that must be present in PHI-global.
_PHI_GLOBAL_REQUIRED_CONSTRAINTS = [
    "problems, not features",
    "problem-state artifact",
    "fail-closed readiness",
    "tool creation",
    "testing philosophy",
    "roal mechanics",
]


def test_phi_global_preserves_governing_constraints() -> None:
    """PHI-global.md must carry the material governing constraints,
    not compress them away (PAT-0016 philosophy projection)."""
    phi_global = ROOT / "philosophy" / "profiles" / "PHI-global.md"
    if not phi_global.exists():
        pytest.skip("PHI-global.md not found")
    text = phi_global.read_text(encoding="utf-8").lower()
    missing = [
        c for c in _PHI_GLOBAL_REQUIRED_CONSTRAINTS
        if c.lower() not in text
    ]
    assert not missing, (
        f"PHI-global.md is missing {len(missing)} governing constraint(s): "
        f"{missing}"
    )
