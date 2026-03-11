"""End-to-end runner for agentic eval scenarios."""

from __future__ import annotations

import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .collectors import collect_outputs
from .judge import build_judge_input, build_judge_prompt, invoke_judge
from .report import ScenarioResult, generate_json_report
from .scenario_loader import ScenarioSpec
from .seed_engine import cleanup_scenario, seed_scenario
from .structural_checks import run_structural_checks
from .trigger_adapters import run_trigger


class ScenarioTimeoutError(TimeoutError):
    """Raised when a scenario exceeds its configured timeout."""


@dataclass
class RunConfig:
    project_root: Path
    keep_failed: bool = False
    keep_all: bool = False
    report_path: Path | None = None
    timeout_cheap: int = 180
    timeout_moderate: int = 600
    timeout_expensive: int = 2400


def _timeout_seconds(cost_tier: str, config: RunConfig) -> int:
    mapping = {
        "cheap": config.timeout_cheap,
        "moderate": config.timeout_moderate,
        "expensive": config.timeout_expensive,
    }
    return mapping.get(cost_tier, config.timeout_moderate)


@contextmanager
def _scenario_timeout(seconds: int):
    def _handle_timeout(signum, frame):  # noqa: ARG001
        raise ScenarioTimeoutError(f"Scenario timed out after {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def run_scenario(spec: ScenarioSpec, config: RunConfig) -> ScenarioResult:
    """Run a single scenario end-to-end."""
    state = seed_scenario(spec, config.project_root)
    run_dir = state.root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    overall = False
    structural_passed = False
    semantic_passed = False
    structural_results = []
    judge_output = None
    error: str | None = None
    t0 = time.monotonic()

    try:
        with _scenario_timeout(_timeout_seconds(spec.cost_tier, config)):
            run_trigger(spec.trigger, state.planspace, state.codespace, config.project_root)
            collected = collect_outputs(spec.collect, state.planspace)
            structural_results = run_structural_checks(
                spec.checks.structural,
                collected,
                state.planspace,
            )
            structural_passed = all(
                result.verdict == "PASS"
                for result in structural_results
                if result.required
            )
            if structural_passed and (
                spec.checks.semantic or spec.checks.absence or spec.checks.signals
            ):
                judge_input = build_judge_input(spec, collected, structural_results)
                prompt_path = build_judge_prompt(judge_input, run_dir)
                judge_output = invoke_judge(prompt_path, config.project_root)
            semantic_passed = judge_output.overall_verdict == "PASS" if judge_output else True
            overall = structural_passed and semantic_passed
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    elapsed = time.monotonic() - t0

    result = ScenarioResult(
        scenario_id=spec.id,
        scenario_name=spec.name,
        category=spec.category,
        system=spec.system,
        cost_tier=spec.cost_tier,
        structural_passed=structural_passed,
        semantic_passed=semantic_passed,
        overall_passed=overall,
        structural_results=structural_results,
        judge_output=judge_output,
        elapsed_seconds=elapsed,
        error=error,
        temp_dir=state.root if (config.keep_all or ((not overall) and config.keep_failed)) else None,
    )
    generate_json_report(result, run_dir)

    if not (config.keep_all or ((not overall) and config.keep_failed)):
        cleanup_scenario(state)

    return result


def run_all(specs: list[ScenarioSpec], config: RunConfig) -> list[ScenarioResult]:
    """Run all scenarios sequentially."""
    results: list[ScenarioResult] = []
    for spec in specs:
        print(f"  Running: {spec.id} ...")
        result = run_scenario(spec, config)
        results.append(result)
        status = "PASS" if result.overall_passed else "FAIL"
        print(f"  [{status}] {spec.id} ({result.elapsed_seconds:.1f}s)")
    return results
