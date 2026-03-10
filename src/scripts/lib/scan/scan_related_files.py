"""Shared helpers for scan related-files discovery and validation."""

from __future__ import annotations

import re
from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.core.hash_service import content_hash, file_hash
from lib.scan.scan_dispatch import DEFAULT_SCAN_MODELS
from lib.scan.scan_phase_logger import log_phase_failure
from lib.scan.scan_template_loader import load_scan_template
from prompt_safety import validate_dynamic_content
from scan.cache import strip_scan_summaries
from scan.dispatch import dispatch_agent


def list_section_files(sections_dir: Path) -> list[Path]:
    """Return sorted list of ``section-N.md`` files."""
    files = [
        f
        for f in sections_dir.iterdir()
        if f.is_file()
        and re.match(r"section-\d+\.md$", f.name)
    ]
    return sorted(files)


def apply_related_files_update(section_file: Path, signal_file: Path) -> bool:
    """Apply additions/removals from a related-files update signal."""
    if not signal_file.exists():
        return False

    signal = read_json(signal_file)
    if signal is None:
        print(
            f"[RELATED FILES][WARN] Malformed update signal: "
            f"{signal_file}",
        )
        return False

    if signal.get("status") != "stale":
        return False

    from scan.related_files import block_insert_position, find_entry_span

    section = section_file.read_text()
    removals = signal.get("removals", [])
    additions = signal.get("additions", [])

    if not removals and not additions:
        return False

    for rm_path in removals:
        span = find_entry_span(section, rm_path)
        if span is None:
            continue
        entry_start, entry_end = span
        before = section[:entry_start].rstrip("\n")
        after = section[entry_end:]
        section = before + after

    for add_path in additions:
        if find_entry_span(section, add_path) is not None:
            continue
        insert_pos = block_insert_position(section)
        if insert_pos is None:
            continue
        entry = (
            f"\n\n### {add_path}\n"
            "Added by validation — confirm relevance during deep scan."
        )
        section = section[:insert_pos] + entry + section[insert_pos:]

    section_file.write_text(section)
    n_rm = len(removals)
    n_add = len(additions)
    print(f"applied: {n_rm} removals, {n_add} additions")
    return True


def _sha256_file(path: Path) -> str:
    """Return hex sha256 of file contents, or empty string on error."""
    return file_hash(path)


def validate_existing_related_files(
    *,
    section_file: Path,
    section_name: str,
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    corrections_file: Path,
    model_policy: dict[str, str],
) -> None:
    """Check if codemap OR section changed; if so, dispatch validation."""
    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    codemap_hash_file = section_log / "codemap-hash.txt"

    codemap_hash = _sha256_file(codemap_path) if codemap_path.is_file() else ""
    corrections_hash = (
        _sha256_file(corrections_file) if corrections_file.is_file() else ""
    )
    section_text_raw = section_file.read_text() if section_file.is_file() else ""
    section_hash = content_hash(strip_scan_summaries(section_text_raw))
    combined = f"{codemap_hash}:{corrections_hash}:{section_hash}"
    combined_hash = content_hash(combined)

    prev_hash = ""
    if codemap_hash_file.is_file():
        prev_hash = codemap_hash_file.read_text().strip()

    if combined_hash == prev_hash and prev_hash:
        print(
            f"[EXPLORE] {section_name}: Related Files exist, "
            "codemap+section unchanged — skipping",
        )
        codemap_hash_file.write_text(combined_hash)
        return

    print(
        f"[EXPLORE] {section_name}: validating Related Files "
        "against updated codemap/section",
    )

    validate_prompt = section_log / "validate-prompt.md"
    validate_output = section_log / "validate-output.md"
    update_signal = (
        artifacts_dir / "signals" / f"{section_name}-related-files-update.json"
    )

    corrections_ref = ""
    if corrections_file.is_file():
        corrections_ref = (
            f"3. Codemap corrections (authoritative fixes): "
            f"`{corrections_file}`"
        )

    prompt = load_scan_template("validate_related_files.md").format(
        section_file=section_file,
        codemap_path=codemap_path,
        corrections_ref=corrections_ref,
        update_signal=update_signal,
    )
    violations = validate_dynamic_content(prompt)
    if violations:
        print(
            f"[EXPLORE] {section_name}: validate prompt blocked — "
            f"safety violations: {violations}",
        )
        return
    validate_prompt.write_text(prompt)

    result = dispatch_agent(
        model=model_policy.get("validation", DEFAULT_SCAN_MODELS["validation"]),
        project=codespace,
        prompt_file=validate_prompt,
        agent_file="scan-related-files-adjudicator.md",
        stdout_file=validate_output,
    )

    if result.returncode == 0:
        print(f"[EXPLORE] {section_name}: validation complete")

        signal_file = (
            artifacts_dir
            / "signals"
            / f"{section_name}-related-files-update.json"
        )
        write_hash = True
        if signal_file.is_file():
            data = read_json(signal_file)
            if data is None:
                print(
                    f"[EXPLORE][WARN] {section_name}: malformed "
                    "related-files update signal — "
                    f"forcing revalidation on next run",
                )
                write_hash = False
                status = ""
            else:
                status = data.get("status", "")

            if status == "stale":
                print(
                    f"[EXPLORE] {section_name}: "
                    "applying related-files updates",
                )
                if apply_related_files_update(section_file, signal_file):
                    data["status"] = "applied"
                    write_json(signal_file, data)
                    print(
                        f"[EXPLORE] {section_name}: list updated",
                    )
                    section_text_updated = (
                        section_file.read_text()
                        if section_file.is_file()
                        else ""
                    )
                    section_hash = content_hash(
                        strip_scan_summaries(section_text_updated),
                    )
                    combined = (
                        f"{codemap_hash}:{corrections_hash}:{section_hash}"
                    )
                    combined_hash = content_hash(combined)
                else:
                    print(
                        f"[EXPLORE] {section_name}: auto-apply "
                        "failed — keeping existing list",
                    )
                    write_hash = False
            elif status and status not in ("ok", "applied"):
                print(
                    f"[EXPLORE][WARN] {section_name}: unexpected "
                    f"validation signal status '{status}' — "
                    f"forcing revalidation on next run",
                )
                write_hash = False

        if write_hash:
            codemap_hash_file.write_text(combined_hash)
    else:
        print(
            f"[EXPLORE] {section_name}: validation failed "
            "— keeping existing list",
        )


__all__ = [
    "apply_related_files_update",
    "list_section_files",
    "validate_existing_related_files",
]
