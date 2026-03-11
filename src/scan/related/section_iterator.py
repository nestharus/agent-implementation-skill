"""Per-section iteration helpers for deep scan."""

from __future__ import annotations

import sys
from pathlib import Path

from signals.repository.artifact_io import read_json
from scan.explore.analyzer import analyze_file
from scan.related.match_updater import deep_scan_related_files
from scan.service.section_notes import log_phase_failure
from scan.explore.tier_ranking import run_tier_ranking
from scan.codemap.cache import FileCardCache


def _get_scan_files(tier_file: Path) -> tuple[list[str], str]:
    """Read tier file and return (files_to_scan, tier_label)."""
    data = read_json(tier_file)
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
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    file_card_cache: FileCardCache,
    corrections_path: Path,
    model_policy: dict[str, str],
    already_scanned: dict[str, set[str]],
) -> bool:
    """Run one pass of per-section tier ranking + per-file analysis."""
    phase_failed = False

    for section_file in section_files:
        section_name = section_file.stem
        section_log = scan_log_dir / section_name
        section_log.mkdir(parents=True, exist_ok=True)

        related_files = deep_scan_related_files(section_file)
        if not related_files:
            continue

        tier_file = run_tier_ranking(
            section_file,
            section_name,
            related_files,
            codespace,
            artifacts_dir,
            scan_log_dir,
            model_policy,
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
                scan_log_dir,
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
                codespace,
                codemap_path,
                corrections_path,
                scan_log_dir,
                file_card_cache,
                model_policy,
            )
            done.add(source_file)
            if not ok:
                phase_failed = True

    return phase_failed
