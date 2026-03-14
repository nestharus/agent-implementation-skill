"""Per-section iteration helpers for deep scan."""

from __future__ import annotations

import sys
from pathlib import Path

from containers import Services
from scan.explore.analyzer import analyze_file
from scan.related.match_updater import deep_scan_related_files
from scan.scan_context import ScanContext
from scan.service.phase_failure_logger import log_phase_failure
from scan.explore.tier_ranker import run_tier_ranking
from scan.codemap.cache import FileCardCache


def _get_scan_files(tier_file: Path) -> tuple[list[str], str]:
    """Read tier file and return (files_to_scan, tier_label)."""
    data = Services.artifact_io().read_json(tier_file)
    if data is None:
        print(
            f"[TIER][WARN] Malformed tier file: {tier_file} "
            f"— preserving as .malformed.json",
            file=sys.stderr,
        )
        return [], ""

    tiers = data.get("tiers", {})
    scan_now = data.get("scan_now", [])
    seen: set[str] = set()
    files: list[str] = []
    for tier_name in scan_now:
        for source_file in tiers.get(tier_name, []):
            if source_file not in seen:
                seen.add(source_file)
                files.append(source_file)

    label = "+".join(scan_now) if scan_now else "unknown"
    return files, label


def scan_sections(
    section_files: list[Path],
    ctx: ScanContext,
    artifacts_dir: Path,
    file_card_cache: FileCardCache,
    already_scanned: dict[str, set[str]],
) -> bool:
    """Run one pass of per-section tier ranking + per-file analysis."""
    phase_failed = False

    for section_file in section_files:
        section_name = section_file.stem
        section_log = ctx.scan_log_dir / section_name
        section_log.mkdir(parents=True, exist_ok=True)

        related_files = deep_scan_related_files(section_file)
        if not related_files:
            continue

        tier_file = run_tier_ranking(
            section_file,
            section_name,
            related_files,
            ctx.codespace,
            artifacts_dir,
            ctx.scan_log_dir,
            ctx.model_policy,
        )

        scan_files: list[str] = []
        if tier_file is not None and tier_file.is_file():
            scan_files, tier_label = _get_scan_files(tier_file)
            if scan_files:
                print(
                    f"[TIER] {section_name}: scanning {len(scan_files)} files "
                    f"({tier_label}) of {len(related_files)} total",
                )

        if not scan_files:
            print(
                f"[DEEP] {section_name}: no tier ranking available "
                "— skipping deep scan (fail-closed)",
            )
            phase_failed = True
            log_phase_failure(
                "deep-scan",
                section_name,
                "tier ranking unavailable — deep scan skipped",
                ctx.scan_log_dir,
            )
            continue

        done = already_scanned.setdefault(section_name, set())
        for source_file in scan_files:
            if not source_file.strip() or source_file in done:
                continue

            ok = analyze_file(
                section_file,
                section_name,
                source_file,
                ctx,
                file_card_cache,
            )
            done.add(source_file)
            if not ok:
                phase_failed = True

    return phase_failed
