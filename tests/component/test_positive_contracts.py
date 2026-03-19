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
    """EVERY present-tense pattern-count mention in system-synthesis.md must
    agree with the live catalog count (PAT-0015 rule 15)."""
    synthesis = ROOT / "system-synthesis.md"
    patterns = GOV / "patterns" / "index.md"
    if not synthesis.exists() or not patterns.exists():
        pytest.skip("governance files not found")
    pat_count = len(re.findall(r"^## PAT-\d+", patterns.read_text(encoding="utf-8"), re.MULTILINE))
    synth_text = synthesis.read_text(encoding="utf-8")
    matches = re.findall(r"(\d+)\s+patterns", synth_text)
    assert matches, "system-synthesis.md does not mention a pattern count"
    wrong = [m for m in matches if int(m) != pat_count]
    assert not wrong, (
        f"system-synthesis.md has {len(wrong)} stale pattern-count "
        f"mention(s): {wrong} (live catalog has {pat_count})"
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
    # Top-level workflow runner (composition root)
    "pipeline/runner.py",
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


# Bootstrap-adjacent composition roots where literal-default fallbacks
# are accepted (not operational callsites).
_PAT0005_LITERAL_EXCLUDED = {
    *_PAT0005_EXCLUDED_PREFIXES,
}


def test_no_literal_model_policy_fallback() -> None:
    """Operational callsites must not use policy.get("key", "literal_model")
    as a model fallback — use resolve(policy, key) instead (PAT-0005).
    Catches the literal-default form that the policy-key chain test misses."""
    literal_pattern = re.compile(
        r"(?:model_policy|policy|self\._policy)\.get\(\s*\"[^\"]+\"\s*,"
        r"\s*\"(?:gpt|claude|glm|o1|o3)[^\"]*\"\s*\)",
    )
    violations = []
    for py_file in sorted(SRC.rglob("*.py")):
        rel = str(py_file.relative_to(SRC))
        if any(rel.startswith(p) for p in _PAT0005_LITERAL_EXCLUDED):
            continue
        text = py_file.read_text(encoding="utf-8")
        if literal_pattern.search(text):
            violations.append(rel)
    assert not violations, (
        f"Literal model-policy fallback in {len(violations)} file(s): "
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


# ---------------------------------------------------------------------------
# 8. Live inventory derivation — derive counts from filesystem and compare
#    against summary surfaces (PAT-0016 truth lock).
# ---------------------------------------------------------------------------

def _derive_agent_file_count() -> int:
    """Count total agent files under src/*/agents/*.md."""
    return len(list(SRC.glob("*/agents/*.md")))


def _derive_routed_agent_count() -> int:
    """Count unique agent filenames referenced by router.route() calls."""
    agents: set[str] = set()
    for routes_file in sorted(SRC.glob("*/routes.py")):
        text = routes_file.read_text(encoding="utf-8")
        agents.update(re.findall(r'agent="([^"]+)"', text))
    return len(agents)


def _derive_task_route_count() -> int:
    """Count total router.route() calls across all route files."""
    total = 0
    for routes_file in sorted(SRC.glob("*/routes.py")):
        text = routes_file.read_text(encoding="utf-8")
        total += len(re.findall(r"router\.route\(", text))
    return total


def _derive_namespace_count() -> int:
    """Count unique namespaces (directories containing routes.py)."""
    return len(list(SRC.glob("*/routes.py")))


def test_system_synthesis_agent_count_matches_live() -> None:
    """system-synthesis.md agent count must match the derived inventory."""
    synthesis = ROOT / "system-synthesis.md"
    if not synthesis.exists():
        pytest.skip("system-synthesis.md not found")
    text = synthesis.read_text(encoding="utf-8")
    live_total = _derive_agent_file_count()
    live_routed = _derive_routed_agent_count()
    # Expect qualified language like "58 total agent files (51 routed)"
    match = re.search(r"(\d+)\s+total agent files\s*\((\d+)\s+routed\)", text)
    assert match, (
        "system-synthesis.md does not use qualified agent inventory "
        "(expected 'N total agent files (M routed)')"
    )
    assert int(match.group(1)) == live_total, (
        f"system-synthesis.md says {match.group(1)} total agent files, "
        f"live count is {live_total}"
    )
    assert int(match.group(2)) == live_routed, (
        f"system-synthesis.md says {match.group(2)} routed, "
        f"live count is {live_routed}"
    )


def test_system_synthesis_task_count_matches_live() -> None:
    """system-synthesis.md routed task count must match derived inventory."""
    synthesis = ROOT / "system-synthesis.md"
    if not synthesis.exists():
        pytest.skip("system-synthesis.md not found")
    text = synthesis.read_text(encoding="utf-8")
    live_tasks = _derive_task_route_count()
    match = re.search(r"(\d+)\s+routed tasks", text)
    assert match, "system-synthesis.md does not mention a routed task count"
    assert int(match.group(1)) == live_tasks, (
        f"system-synthesis.md says {match.group(1)} routed tasks, "
        f"live count is {live_tasks}"
    )


def test_system_synthesis_namespace_count_matches_live() -> None:
    """system-synthesis.md namespace count must match derived inventory."""
    synthesis = ROOT / "system-synthesis.md"
    if not synthesis.exists():
        pytest.skip("system-synthesis.md not found")
    text = synthesis.read_text(encoding="utf-8")
    live_ns = _derive_namespace_count()
    match = re.search(r"(\d+)\s+(?:system )?namespaces", text)
    assert match, "system-synthesis.md does not mention a namespace count"
    assert int(match.group(1)) == live_ns, (
        f"system-synthesis.md says {match.group(1)} namespaces, "
        f"live count is {live_ns}"
    )


# ---------------------------------------------------------------------------
# 9. history.md current-state footer — must match derived inventories.
# ---------------------------------------------------------------------------

def _extract_history_footer(text: str) -> str | None:
    """Extract the **Current state** footer line from history.md."""
    match = re.search(r"\*\*Current state\*\*:.*", text)
    return match.group(0) if match else None


def test_history_footer_agent_count_matches_live() -> None:
    """governance/audit/history.md footer must report correct agent file count."""
    history = GOV / "audit" / "history.md"
    if not history.exists():
        pytest.skip("history.md not found")
    footer = _extract_history_footer(history.read_text(encoding="utf-8"))
    assert footer, "history.md does not have a **Current state** footer"
    live_total = _derive_agent_file_count()
    live_routed = _derive_routed_agent_count()
    match = re.search(r"(\d+)\s+agent files\s*\((\d+)\s+routed\)", footer)
    assert match, (
        "history.md footer does not use qualified agent inventory "
        f"(expected 'N agent files (M routed)'): {footer}"
    )
    assert int(match.group(1)) == live_total, (
        f"history.md footer says {match.group(1)} agent files, live is {live_total}"
    )
    assert int(match.group(2)) == live_routed, (
        f"history.md footer says {match.group(2)} routed, live is {live_routed}"
    )


def test_history_footer_task_count_matches_live() -> None:
    """governance/audit/history.md footer must report correct task type count."""
    history = GOV / "audit" / "history.md"
    if not history.exists():
        pytest.skip("history.md not found")
    footer = _extract_history_footer(history.read_text(encoding="utf-8"))
    assert footer, "history.md does not have a **Current state** footer"
    live_tasks = _derive_task_route_count()
    match = re.search(r"(\d+)\s+task types", footer)
    assert match, f"history.md footer does not mention task type count: {footer}"
    assert int(match.group(1)) == live_tasks, (
        f"history.md footer says {match.group(1)} task types, live is {live_tasks}"
    )


def test_history_footer_namespace_count_matches_live() -> None:
    """governance/audit/history.md footer must report correct namespace count."""
    history = GOV / "audit" / "history.md"
    if not history.exists():
        pytest.skip("history.md not found")
    footer = _extract_history_footer(history.read_text(encoding="utf-8"))
    assert footer, "history.md does not have a **Current state** footer"
    live_ns = _derive_namespace_count()
    match = re.search(r"(\d+)\s+namespaces", footer)
    assert match, f"history.md footer does not mention namespace count: {footer}"
    assert int(match.group(1)) == live_ns, (
        f"history.md footer says {match.group(1)} namespaces, live is {live_ns}"
    )


# ---------------------------------------------------------------------------
# 10. Bootstrap substrate-status family — must use PathRegistry accessor.
# ---------------------------------------------------------------------------

def test_bootstrap_substrate_status_uses_path_registry() -> None:
    """substrate_state_reader.py must use PathRegistry.substrate_status()
    accessor, not construct paths manually."""
    reader = SRC / "scan" / "substrate" / "substrate_state_reader.py"
    if not reader.exists():
        pytest.skip("substrate_state_reader.py not found")
    text = reader.read_text(encoding="utf-8")
    assert "PathRegistry" in text, (
        "substrate_state_reader.py does not import PathRegistry"
    )
    assert "substrate_status()" in text, (
        "substrate_state_reader.py does not use PathRegistry.substrate_status()"
    )
    assert '"status.json"' not in text, (
        "substrate_state_reader.py still hard-codes 'status.json' path segment"
    )


# ---------------------------------------------------------------------------
# 11. PAT-0019 truth lock — derive live Services-import inventory and
#     compare against the published known-instance path set.
# ---------------------------------------------------------------------------

def _derive_pat0019_known_instance_paths() -> set[str]:
    """Extract backtick-quoted src/ paths from the PAT-0019 section of the
    pattern catalog and return them as a set of rel-to-src paths."""
    patterns = GOV / "patterns" / "index.md"
    if not patterns.exists():
        return set()
    text = patterns.read_text(encoding="utf-8")
    # Locate the PAT-0019 section
    start = text.find("## PAT-0019")
    if start == -1:
        return set()
    end = text.find("\n## PAT-00", start + 1)
    # If no next pattern, take everything until Health Notes or end
    if end == -1:
        end = text.find("\n## Health Notes", start + 1)
    if end == -1:
        end = len(text)
    section_text = text[start:end]
    raw = re.findall(r"`(src/[^`]+\.py)`", section_text)
    return {p.replace("src/", "", 1) for p in raw if "*" not in p and "?" not in p}


def test_pat0019_known_instances_match_live_services_imports() -> None:
    """PAT-0019 known-instance paths that import Services must actually
    import Services in live code, and every live Services-importing file
    must appear somewhere in the PAT-0019 known instances or in the
    sanctioned/quarantined sets (PAT-0019 truth lock)."""
    catalog_paths = _derive_pat0019_known_instance_paths()
    assert catalog_paths, "no PAT-0019 known-instance paths found"
    live_sites = _derive_services_import_sites()
    published = _SANCTIONED_CONTAINER_SITES | _QUARANTINED_RESIDUE
    # Every live import site must appear in at least one of:
    # the sanctioned set, the quarantined set, or the PAT-0019 catalog
    all_known = published | catalog_paths
    unlisted = live_sites - all_known
    assert not unlisted, (
        f"Live Services-import sites missing from PAT-0019 known instances "
        f"and allowlists: {sorted(unlisted)}"
    )


# ---------------------------------------------------------------------------
# 12. CP-3 — live task-driven bootstrap positive contracts.
#     Replaces retired assessor/orchestrator tests.
# ---------------------------------------------------------------------------

def test_runner_seeds_bootstrap_classify_entry() -> None:
    """pipeline/runner.py must submit the bootstrap.classify_entry seed task
    to kick off the task-driven bootstrap chain."""
    runner = SRC / "pipeline" / "runner.py"
    text = runner.read_text(encoding="utf-8")
    assert "bootstrap.classify_entry" in text, (
        "runner.py does not submit bootstrap.classify_entry seed task"
    )


_EXPECTED_BOOTSTRAP_TASK_TYPES = [
    "classify_entry",
    "extract_problems",
    "explore_problems",
    "extract_values",
    "explore_values",
    "confirm_understanding",
    "assess_reliability",
    "decompose",
    "align_proposal",
    "expand_proposal",
    "explore_factors",
    "build_codemap",
    "explore_sections",
    "discover_substrate",
]


def test_bootstrap_routes_complete() -> None:
    """bootstrap/routes.py must register all 14 expected task types."""
    routes = SRC / "bootstrap" / "routes.py"
    text = routes.read_text(encoding="utf-8")
    missing = [
        t for t in _EXPECTED_BOOTSTRAP_TASK_TYPES
        if f'"{t}"' not in text
    ]
    assert not missing, (
        f"bootstrap/routes.py is missing {len(missing)} task type(s): {missing}"
    )
    # Confirm the total count matches exactly (no undocumented extras)
    route_calls = re.findall(r'router\.route\(', text)
    assert len(route_calls) == len(_EXPECTED_BOOTSTRAP_TASK_TYPES), (
        f"bootstrap/routes.py has {len(route_calls)} route() calls, "
        f"expected {len(_EXPECTED_BOOTSTRAP_TASK_TYPES)}"
    )


_EXPECTED_FOLLOW_ON_KEYS = [
    "bootstrap.classify_entry",
    "bootstrap.extract_problems",
    "bootstrap.extract_values",
    "bootstrap.explore_problems",
    "bootstrap.explore_values",
    "bootstrap.confirm_understanding",
    "bootstrap.decompose",
    "bootstrap.expand_proposal",
    "bootstrap.explore_factors",
    "bootstrap.build_codemap",
    "bootstrap.explore_sections",
]


def test_reconciler_bootstrap_follow_on_chain() -> None:
    """reconciler.py _GLOBAL_FOLLOW_ON must contain all expected
    bootstrap chain keys so no follow-on step is silently dropped."""
    reconciler = SRC / "flow" / "engine" / "reconciler.py"
    text = reconciler.read_text(encoding="utf-8")
    missing = [
        k for k in _EXPECTED_FOLLOW_ON_KEYS
        if f'"{k}"' not in text
    ]
    assert not missing, (
        f"reconciler.py _GLOBAL_FOLLOW_ON is missing {len(missing)} key(s): {missing}"
    )


def test_reconciler_expansion_circuit_breaker_uses_bootstrap_namespace() -> None:
    """The expansion circuit breaker must count bootstrap.expand_proposal
    tasks, not the retired global.expand_proposal namespace."""
    reconciler = SRC / "flow" / "engine" / "reconciler.py"
    text = reconciler.read_text(encoding="utf-8")
    assert "bootstrap.expand_proposal" in text, (
        "reconciler.py does not reference bootstrap.expand_proposal"
    )
    # The SQL query in _count_expansion_loops must NOT use the retired namespace
    assert "global.expand_proposal" not in text, (
        "reconciler.py still references retired global.expand_proposal namespace "
        "in the circuit breaker query"
    )


def test_reconciler_discover_substrate_initializes_sections() -> None:
    """The discover_substrate handler must call
    _initialize_section_states_from_artifacts to transition from bootstrap
    into per-section execution."""
    reconciler = SRC / "flow" / "engine" / "reconciler.py"
    text = reconciler.read_text(encoding="utf-8")
    # Verify the discover_substrate handler exists and calls initializer
    assert re.search(
        r"discover_substrate.*\n.*_initialize_section_states_from_artifacts",
        text,
    ), (
        "reconciler.py discover_substrate handler does not call "
        "_initialize_section_states_from_artifacts"
    )


def _derive_namespace_names() -> set[str]:
    """Derive the set of namespace names from routes.py files."""
    return {
        p.parent.name
        for p in sorted(SRC.glob("*/routes.py"))
    }


def test_system_synthesis_namespace_breakdown_complete() -> None:
    """system-synthesis.md must enumerate EVERY namespace found by the
    route discovery system, not just report a count."""
    synthesis = ROOT / "system-synthesis.md"
    if not synthesis.exists():
        pytest.skip("system-synthesis.md not found")
    text = synthesis.read_text(encoding="utf-8")
    live_namespaces = _derive_namespace_names()
    missing = [
        ns for ns in sorted(live_namespaces)
        if f"**{ns}**" not in text
    ]
    assert not missing, (
        f"system-synthesis.md namespace breakdown is missing {len(missing)} "
        f"namespace(s): {missing}"
    )
