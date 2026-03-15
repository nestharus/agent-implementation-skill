"""Argparse entry point for the scan package.

Usage::

    python -m scan <quick|deep|both> <planspace> <codespace>

Matches the public CLI contract of the original ``scan.sh``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orchestrator.path_registry import PathRegistry
from scan.related.related_file_resolver import list_section_files

from containers import Services
from scan.codemap.codemap_builder import CodemapBuilder
from scan.explore.deep_scanner import run_deep_scan
from scan.explore.section_explorer import SectionExplorer
from scan.related.related_file_resolver import RelatedFileResolver
from scan.scan_dispatcher import read_scan_model_policy


def validate_preflight(
    codespace: Path,
    sections_dir: Path,
) -> bool:
    """Pre-flight checks: codespace accessible, sections present."""
    if not codespace.is_dir():
        print(
            f"[ERROR] Missing or inaccessible codespace: {codespace}",
            file=sys.stderr,
        )
        return False

    if not sections_dir.is_dir():
        print(
            f"[ERROR] Missing sections directory: {sections_dir}",
            file=sys.stderr,
        )
        return False

    section_count = len(list_section_files(sections_dir))
    if section_count == 0:
        print(
            f"[ERROR] No section files found in: {sections_dir}",
            file=sys.stderr,
        )
        return False

    return True


def run_quick_scan(
    *,
    codemap_path: Path,
    codespace: Path,
    sections_dir: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    fingerprint_path: Path,
    model_policy: dict[str, str],
) -> bool:
    """Run quick scan: codemap exploration + per-section file identification."""
    print("=== Quick Scan: codemap exploration + per-section file identification ===")

    prompt_guard = Services.prompt_guard()
    task_router = Services.task_router()
    artifact_io = Services.artifact_io()

    codemap_builder = CodemapBuilder(
        prompt_guard=prompt_guard,
        task_router=task_router,
        artifact_io=artifact_io,
    )
    if not codemap_builder.run_codemap_build(
        codemap_path=codemap_path,
        codespace=codespace,
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
        fingerprint_path=fingerprint_path,
        model_policy=model_policy,
    ):
        return False

    explorer = SectionExplorer(
        prompt_guard=prompt_guard,
        task_router=task_router,
        related_file_resolver=RelatedFileResolver(
            artifact_io=artifact_io,
            hasher=Services.hasher(),
            prompt_guard=prompt_guard,
            task_router=task_router,
        ),
    )
    explorer.run_section_exploration(
        sections_dir=sections_dir,
        codemap_path=codemap_path,
        codespace=codespace,
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
        model_policy=model_policy,
    )

    print("=== Quick Scan Complete ===")
    return True


def main(argv: list[str] | None = None) -> int:
    """Entry point.  Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        prog="scan",
        description="Stage 3 scan entrypoint and phase coordinator.",
    )
    parser.add_argument(
        "command",
        choices=["quick", "deep", "both"],
        help="Scan mode: quick (codemap + exploration), "
        "deep (tier ranking + per-file analysis), both.",
    )
    parser.add_argument("planspace", type=Path, help="Planspace directory.")
    parser.add_argument("codespace", type=Path, help="Codespace directory.")

    args = parser.parse_args(argv)
    planspace: Path = args.planspace
    codespace: Path = args.codespace
    registry = PathRegistry(planspace)

    artifacts_dir = registry.artifacts
    sections_dir = registry.sections_dir()
    codemap_path = registry.codemap()
    scan_log_dir = registry.scan_logs_dir()
    fingerprint_path = registry.codemap_fingerprint()


    if not validate_preflight(codespace, sections_dir):
        return 1

    model_policy = read_scan_model_policy(artifacts_dir)
    cmd = args.command

    if cmd == "quick":
        ok = run_quick_scan(
            codemap_path=codemap_path,
            codespace=codespace,
            sections_dir=sections_dir,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            fingerprint_path=fingerprint_path,
            model_policy=model_policy,
        )
        return 0 if ok else 1

    if cmd == "deep":
        ok = run_deep_scan(
            sections_dir=sections_dir,
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            model_policy=model_policy,
        )
        return 0 if ok else 1

    if cmd == "both":
        ok = run_quick_scan(
            codemap_path=codemap_path,
            codespace=codespace,
            sections_dir=sections_dir,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            fingerprint_path=fingerprint_path,
            model_policy=model_policy,
        )
        if not ok:
            return 1
        ok = run_deep_scan(
            sections_dir=sections_dir,
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            model_policy=model_policy,
        )
        return 0 if ok else 1

    return 1
