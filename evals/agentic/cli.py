"""Command-line interface for agentic workflow evals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import generate_markdown_report
from .runner import RunConfig, run_all
from .scenario_loader import load_scenarios

_COST_ORDER = {"cheap": 0, "moderate": 1, "expensive": 2}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="System-level agentic workflow evals")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--scenario", type=str, help="Run one scenario by id")
    parser.add_argument("--category", type=str, help="Run one category")
    parser.add_argument("--wave", type=int, help="Run one wave")
    parser.add_argument("--keep-failed", action="store_true", help="Keep temp dirs for failed scenarios")
    parser.add_argument("--keep-all", action="store_true", help="Keep temp dirs for all scenarios")
    parser.add_argument(
        "--max-cost-tier",
        choices=tuple(_COST_ORDER),
        help="Run only scenarios at or below the selected cost tier",
    )
    parser.add_argument("--report", type=Path, help="Markdown report output path")
    return parser.parse_args()


def _apply_cost_filter(specs, max_cost_tier: str | None):
    if max_cost_tier is None:
        return specs
    ceiling = _COST_ORDER[max_cost_tier]
    return [spec for spec in specs if _COST_ORDER.get(spec.cost_tier, ceiling + 1) <= ceiling]


def main() -> None:
    """CLI entry point."""
    args = _parse_args()
    project_root = _project_root()
    specs = load_scenarios(
        project_root,
        scenario_id=args.scenario,
        category=args.category,
        wave=args.wave,
    )
    specs = _apply_cost_filter(specs, args.max_cost_tier)

    if args.list:
        for spec in specs:
            print(
                f"{spec.id}\tcategory={spec.category}\tsystem={spec.system}\t"
                f"wave={spec.wave}\tcost={spec.cost_tier}"
            )
        return

    if args.scenario and not specs:
        raise SystemExit(f"Unknown scenario: {args.scenario}")

    config = RunConfig(
        project_root=project_root,
        keep_failed=args.keep_failed,
        keep_all=args.keep_all,
        report_path=args.report,
    )
    results = run_all(specs, config)
    report_path = args.report or (project_root / ".tmp" / "agentic-evals-report.md")
    generate_markdown_report(results, report_path)
    print(f"\nReport: {report_path}")
    if any(not result.overall_passed for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
