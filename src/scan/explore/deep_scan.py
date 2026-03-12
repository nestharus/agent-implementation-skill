"""Deep scan: tier ranking, per-file analysis, summary application.

Translates ``run_deep_scan()`` and helpers from scan.sh.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.path_registry import PathRegistry
from scan.related.match_updater import deep_scan_related_files, update_match
from scan.related.discovery import list_section_files
from scan.related.section_iterator import scan_sections as _scan_sections

from scan.codemap.cache import FileCardCache
from scan.cli_dispatch import dispatch_agent, read_scan_model_policy
from scan.service.feedback import collect_and_route_feedback
_MAX_SCAN_PASSES = 2


def run_deep_scan(
    *,
    sections_dir: Path,
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    model_policy: dict[str, str] | None = None,
) -> bool:
    """Run deep scan over all sections.

    Runs up to ``_MAX_SCAN_PASSES`` passes: after each pass, feedback
    is collected and may add missing files to sections.  A follow-up
    pass scans only the newly-added files, closing the feedback loop
    without unbounded iteration.

    Returns ``True`` on full success, ``False`` if any failures occurred.
    """
    if model_policy is None:
        model_policy = read_scan_model_policy(artifacts_dir)

    print("=== Deep Scan: agent-driven analysis of confirmed related files ===")

    section_files = list_section_files(sections_dir)
    file_card_cache = FileCardCache(artifacts_dir / "file-cards")
    corrections_path = PathRegistry(artifacts_dir.parent).corrections()
    already_scanned: dict[str, set[str]] = {}
    any_failures = False

    for pass_num in range(1, _MAX_SCAN_PASSES + 1):
        if pass_num > 1:
            print(
                f"=== Deep Scan: follow-up pass {pass_num} "
                "(scanning newly-added files) ===",
            )

        phase_failed = _scan_sections(
            section_files=section_files,
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            file_card_cache=file_card_cache,
            corrections_path=corrections_path,
            model_policy=model_policy,
            already_scanned=already_scanned,
        )
        if phase_failed:
            any_failures = True

        # Collect feedback and route — may add files to sections
        has_feedback = collect_and_route_feedback(
            section_files=section_files,
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            model_policy=model_policy,
        )

        if not has_feedback or pass_num == _MAX_SCAN_PASSES:
            break

        # Check if feedback actually added new files worth scanning
        new_files_found = False
        for section_file in section_files:
            sec_name = section_file.stem
            current_related = set(deep_scan_related_files(section_file))
            prev_scanned = already_scanned.get(sec_name, set())
            if current_related - prev_scanned:
                new_files_found = True
                break

        if not new_files_found:
            break

    if any_failures:
        print("=== Deep Scan Complete (with failures) ===")
        return False

    print("=== Deep Scan Complete ===")
    return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
