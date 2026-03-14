"""Shared helpers for scan feedback validation and routing."""

from __future__ import annotations

import re
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from signals.types import SIGNAL_OUT_OF_SCOPE


def _is_valid_updater_signal(signal_path: Path) -> bool:
    """Check if an updater signal file contains valid JSON with status."""
    data = Services.artifact_io().read_json(signal_path)
    if data is not None:
        return isinstance(data.get("status"), str)
    print(
        f"[FEEDBACK][WARN] Malformed updater signal in validity "
        f"check: {signal_path}",
    )
    return False


def _validate_feedback_schema(
    data: dict,
    fb_file: Path,
    sec_name: str,
    scan_log_dir: Path,
) -> bool:
    """Validate required fields in deep-scan feedback JSON."""
    missing: list[str] = []
    if not isinstance(data.get("relevant"), bool):
        missing.append("relevant (must be bool)")
    if not isinstance(data.get("source_file"), str):
        missing.append("source_file (must be str)")

    if missing:
        detail = ", ".join(missing)
        print(
            f"[DEEP SCAN] WARNING: Feedback missing required fields: "
            f"{detail} — {fb_file} (section: {sec_name})",
        )
        _append_to_log(
            scan_log_dir / "failures.log",
            f"- Missing required fields ({detail}): "
            f"`{fb_file}` (section: {sec_name})",
        )
        return False

    for field in ("missing_files", SIGNAL_OUT_OF_SCOPE):
        val = data.get(field)
        if val is not None and not isinstance(val, list):
            print(
                f"[DEEP SCAN] WARNING: Feedback field '{field}' must be "
                f"list, got {type(val).__name__} — {fb_file}",
            )
            data[field] = []

    return True


def _extract_section_number(section_name: str) -> str:
    match = re.search(r"\d+", section_name)
    return match.group(0) if match else ""


def _append_to_log(log_path: Path, message: str) -> None:
    with log_path.open("a") as handle:
        handle.write(message + "\n")


def _route_scope_deltas(
    *,
    section_files: list[Path],
    artifacts_dir: Path,
    scan_log_dir: Path,
) -> None:
    """Route out-of-scope findings into scope-delta artifacts."""
    print("--- Deep Scan: routing out-of-scope findings ---")

    paths = PathRegistry(artifacts_dir.parent)
    scope_deltas_dir = paths.scope_deltas_dir()

    for section_file in section_files:
        sec_name = section_file.stem
        sec_log_dir = scan_log_dir / sec_name
        sec_num = _extract_section_number(sec_name)

        all_oos: list[str] = []
        for fb_file in sorted(sec_log_dir.glob("deep-*-feedback.json")):
            data = Services.artifact_io().read_json(fb_file)
            if data is None:
                print(
                    f"[SCOPE][WARN] Malformed feedback JSON in "
                    f"scope-delta routing: {fb_file}",
                )
                continue
            for item in data.get(SIGNAL_OUT_OF_SCOPE, []):
                if isinstance(item, str) and item.strip():
                    all_oos.append(item.strip())

        if not all_oos:
            continue

        delta_path = paths.scope_delta_section(sec_num)

        if delta_path.is_file():
            existing = Services.artifact_io().read_json(delta_path)
            if existing is not None:
                if existing.get("adjudicated"):
                    print(
                        f"[SCOPE] section-{sec_num}: scope delta "
                        "already adjudicated — skipping",
                    )
                    continue
            else:
                preserved = Services.artifact_io().rename_malformed(delta_path)
                if preserved:
                    print(
                        f"[SCOPE][WARN] section-{sec_num}: malformed "
                        f"scope-delta JSON preserved as "
                        f"{preserved.name}",
                    )

        delta = {
            "delta_id": f"delta-{sec_num}-scan-deep",
            "section": sec_num,
            "origin": "scan-deep",
            "items": all_oos,
            "adjudicated": False,
        }
        Services.artifact_io().write_json(delta_path, delta)
        print(
            f"[SCOPE] section-{sec_num}: {len(all_oos)} out-of-scope "
            "items routed to scope-deltas",
        )
