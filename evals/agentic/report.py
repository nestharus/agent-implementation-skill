"""Reporting helpers for agentic eval runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .judge import JudgeOutput
from .structural_checks import CheckResult


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_name: str
    category: str
    system: str
    cost_tier: str
    structural_passed: bool
    semantic_passed: bool
    overall_passed: bool
    structural_results: list[CheckResult]
    judge_output: JudgeOutput | None
    elapsed_seconds: float
    error: str | None = None
    temp_dir: Path | None = None


def generate_json_report(result: ScenarioResult, run_dir: Path) -> Path:
    """Write a per-scenario JSON report."""
    report_path = run_dir / "scenario-result.json"
    payload = asdict(result)
    if result.temp_dir is not None:
        payload["temp_dir"] = str(result.temp_dir)
    report_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return report_path


def generate_markdown_report(results: list[ScenarioResult], output_path: Path) -> None:
    """Write a markdown summary for an eval run."""
    passed = sum(1 for result in results if result.overall_passed)
    total = len(results)
    categories = ", ".join(sorted({result.category for result in results})) if results else "None"
    lines = [
        "# Agentic Eval Report",
        "",
        f"**Date**: {datetime.now(timezone.utc).isoformat()}",
        f"**Scenarios**: {passed} passed / {total} total",
        f"**Categories**: {categories}",
        "",
        "| Scenario | Category | System | Structural | Semantic | Overall | Time | Cost |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for result in results:
        lines.append(
            "| {scenario} | {category} | {system} | {structural} | {semantic} | {overall} | {elapsed:.1f}s | {cost} |".format(
                scenario=result.scenario_id,
                category=result.category,
                system=result.system,
                structural="PASS" if result.structural_passed else "FAIL",
                semantic="PASS" if result.semantic_passed else "FAIL",
                overall="PASS" if result.overall_passed else "FAIL",
                elapsed=result.elapsed_seconds,
                cost=result.cost_tier.capitalize(),
            )
        )

    failures = [result for result in results if not result.overall_passed]
    if failures:
        lines.extend(["", "## Failures"])
        for result in failures:
            lines.append(f"### {result.scenario_id}")
            if result.error:
                lines.append(f"- ERROR: {result.error}")
            if result.judge_output is not None and result.judge_output.assertions:
                for assertion in result.judge_output.assertions:
                    lines.append(f"- {assertion.id}: {assertion.verdict} - {assertion.reason}")
            elif not result.structural_passed:
                for check in result.structural_results:
                    if check.verdict != "PASS":
                        lines.append(f"- {check.target}: FAIL - {check.detail}")
            else:
                lines.append("- No detailed failure assertions recorded.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
