"""Live-LLM scenario eval harness.

Dispatches real agents via dispatch_agent() and checks bounded outputs
(signal type, JSON structure, artifact presence) -- NOT LLM reasoning
quality.

This is a standalone dev/audit tool. It is NOT imported by tests/.

Usage:
    python3 -m evals.harness --list
    python3 -m evals.harness --run reexplorer
    python3 -m evals.harness --all
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Import dispatch infrastructure
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dispatch.service.model_policy import load_model_policy, resolve  # noqa: E402
from dispatch.engine.section_dispatcher import dispatch_agent  # noqa: E402


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

@dataclass
class Check:
    """A single bounded output check for a scenario."""

    description: str
    verify: Callable[[Path, Path, str], tuple[bool, str]]
    """verify(planspace, codespace, agent_output) -> (passed, detail)"""


@dataclass
class Scenario:
    """A self-contained live-LLM evaluation scenario."""

    name: str
    agent_file: str
    model_policy_key: str
    setup: Callable[[Path, Path], Path]
    """setup(planspace, codespace) -> prompt_path"""
    checks: list[Check] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """Result of running a single scenario."""

    scenario_name: str
    model_used: str
    passed: bool
    check_results: list[tuple[str, bool, str]]
    elapsed_seconds: float
    error: str | None = None


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

_SCENARIO_MODULES = [
    "evals.scenarios.reexplorer",
    "evals.scenarios.microstrategy",
    "evals.scenarios.intent_triager",
    "evals.scenarios.coordination_fixer",
    "evals.scenarios.proposal_state",
    "evals.scenarios.reconciliation",
    "evals.scenarios.readiness_gate",
    "evals.scenarios.risk_assessor",
    "evals.scenarios.execution_optimizer",
    "evals.scenarios.design_risk",
    "evals.scenarios.intake",
    "evals.scenarios.value_stack",
]


def _load_all_scenarios() -> tuple[dict[str, Scenario], list[str]]:
    """Import all scenario modules and collect their SCENARIOS lists.

    Returns (registry, import_failures).  PAT-0008: import failures are
    accumulated and reported — the harness does not silently narrow coverage.
    """
    registry: dict[str, Scenario] = {}
    import_failures: list[str] = []
    for mod_name in _SCENARIO_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc:
            import_failures.append(f"{mod_name}: {exc}")
            continue
        scenarios = getattr(mod, "SCENARIOS", [])
        for s in scenarios:
            if s.name in registry:
                print(f"[WARN] Duplicate scenario name: {s.name}")
            registry[s.name] = s
    return registry, import_failures


# ---------------------------------------------------------------------------
# Planspace bootstrapping
# ---------------------------------------------------------------------------

def _bootstrap_planspace(planspace: Path) -> None:
    """Create the minimum planspace structure for dispatch_agent.

    dispatch_agent needs:
    - artifacts/ directory (for prompts/outputs)
    - artifacts/signals/ directory (for signal files)
    - run.db does NOT need to exist (we skip agent_name to avoid DB deps)
    """
    (planspace / "artifacts" / "signals").mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "sections").mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "proposals").mkdir(parents=True, exist_ok=True)
    (planspace / "artifacts" / "coordination").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_scenario(scenario: Scenario) -> ScenarioResult:
    """Run a single scenario with fresh temp directories."""
    planspace = Path(tempfile.mkdtemp(prefix=f"eval-plan-{scenario.name}-"))
    codespace = Path(tempfile.mkdtemp(prefix=f"eval-code-{scenario.name}-"))

    try:
        _bootstrap_planspace(planspace)

        # Write a default model policy so load_model_policy works
        policy = load_model_policy(planspace)
        model = resolve(policy, scenario.model_policy_key)

        # Run scenario setup -- creates fixtures and returns prompt path
        prompt_path = scenario.setup(planspace, codespace)

        # Dispatch the real agent (no agent_name/parent to avoid DB deps)
        output_path = planspace / "artifacts" / f"eval-{scenario.name}-output.md"

        t0 = time.monotonic()
        try:
            agent_output = dispatch_agent(
                model,
                prompt_path,
                output_path,
                # Skip planspace/parent to avoid DB/mailbox requirements.
                # The agent still runs; we just skip pipeline integration.
                planspace=None,
                parent=None,
                codespace=codespace,
                agent_file=scenario.agent_file,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return ScenarioResult(
                scenario_name=scenario.name,
                model_used=model,
                passed=False,
                check_results=[],
                elapsed_seconds=elapsed,
                error=f"dispatch_agent raised: {exc}",
            )
        elapsed = time.monotonic() - t0

        # Run checks
        check_results: list[tuple[str, bool, str]] = []
        all_passed = True
        for check in scenario.checks:
            try:
                passed, detail = check.verify(planspace, codespace, agent_output)
            except Exception as exc:
                passed, detail = False, f"Check raised: {exc}"
            check_results.append((check.description, passed, detail))
            if not passed:
                all_passed = False

        return ScenarioResult(
            scenario_name=scenario.name,
            model_used=model,
            passed=all_passed,
            check_results=check_results,
            elapsed_seconds=elapsed,
        )

    finally:
        # Clean up temp dirs
        shutil.rmtree(planspace, ignore_errors=True)
        shutil.rmtree(codespace, ignore_errors=True)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _print_results(results: list[ScenarioResult]) -> None:
    """Print a summary table of scenario results."""
    print("\n" + "=" * 70)
    print("EVAL RESULTS")
    print("=" * 70)

    passed_count = sum(1 for r in results if r.passed)
    total = len(results)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n  [{status}] {r.scenario_name}  (model={r.model_used}, "
              f"{r.elapsed_seconds:.1f}s)")
        if r.error:
            print(f"    ERROR: {r.error}")
        for desc, ok, detail in r.check_results:
            mark = "+" if ok else "-"
            print(f"    [{mark}] {desc}")
            if detail and not ok:
                # Indent detail lines
                for line in detail.split("\n"):
                    print(f"        {line[:120]}")

    print(f"\n{'=' * 70}")
    print(f"  {passed_count}/{total} scenarios passed")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for the eval harness."""
    parser = argparse.ArgumentParser(
        description="Live-LLM scenario eval harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 -m evals.harness --list
              python3 -m evals.harness --run reexplorer_brownfield
              python3 -m evals.harness --run reexplorer
              python3 -m evals.harness --all
        """),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list", action="store_true",
        help="List all available scenarios",
    )
    group.add_argument(
        "--run", type=str, metavar="SCENARIO",
        help="Run a specific scenario (or prefix to match multiple)",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Run all scenarios",
    )
    args = parser.parse_args()

    registry, import_failures = _load_all_scenarios()

    # PAT-0008: fail closed when declared scenario modules cannot be loaded
    if import_failures:
        print("\n[FAIL] Scenario import failures (PAT-0008 fail-closed):")
        for failure in import_failures:
            print(f"  - {failure}")
        print(f"\n  {len(import_failures)} declared scenario module(s) "
              f"failed to import.\n")
        sys.exit(1)

    if args.list:
        print("\nAvailable scenarios:\n")
        for name, scenario in sorted(registry.items()):
            print(f"  {name}")
            print(f"    agent_file: {scenario.agent_file}")
            print(f"    model_policy_key: {scenario.model_policy_key}")
            print(f"    checks: {len(scenario.checks)}")
        print(f"\n  Total: {len(registry)} scenarios\n")
        return

    # Determine which scenarios to run
    if args.all:
        to_run = list(registry.values())
    else:
        # Exact match first, then prefix match
        if args.run in registry:
            to_run = [registry[args.run]]
        else:
            to_run = [
                s for name, s in sorted(registry.items())
                if name.startswith(args.run)
            ]
        if not to_run:
            print(f"No scenarios matching '{args.run}'. "
                  f"Use --list to see available scenarios.")
            sys.exit(1)

    print(f"\nRunning {len(to_run)} scenario(s)...\n")
    results: list[ScenarioResult] = []
    for scenario in to_run:
        print(f"  Running: {scenario.name} ...")
        result = _run_scenario(scenario)
        results.append(result)

    _print_results(results)

    # Exit with non-zero if any failed
    if not all(r.passed for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
