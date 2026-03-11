"""Shared helpers for scan related-files discovery and validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from signals.repository.artifact_io import read_json, write_json
from staleness.helpers.hashing import content_hash, file_hash
from orchestrator.path_registry import PathRegistry
from scan.service.scan_dispatch import DEFAULT_SCAN_MODELS
from scan.service.section_notes import log_phase_failure
from scan.service.template_loader import load_scan_template
from dispatch.service.prompt_safety import validate_dynamic_content
from scan.codemap.cache import strip_scan_summaries
from scan.cli_dispatch import dispatch_agent


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

    from scan.related.cli_handler import block_insert_position, find_entry_span

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
        before = section[:entry_start].rstrip("\n") + "\n"
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


def _path_exists_in_codespace(codespace: Path, rel_path: str) -> bool:
    root = codespace.resolve()
    candidate = (codespace / rel_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate.is_file()

def _missing_existing_related_files(
    section_text: str,
    codespace: Path,
) -> list[str]:
    from scan.related.cli_handler import extract_related_files

    missing: list[str] = []
    seen: set[str] = set()
    for rel_path in extract_related_files(section_text):
        path = rel_path.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        if not _path_exists_in_codespace(codespace, path):
            missing.append(path)
    return missing

def _normalize_validation_signal(
    signal_file: Path,
    *,
    codespace: Path,
    missing_existing: list[str],
    allow_applied: bool = False,
) -> dict[str, Any] | None:
    if not signal_file.is_file():
        return None

    data = read_json(signal_file)
    if not isinstance(data, dict):
        return None

    status = str(data.get("status", "")).strip().lower()
    if status == "ok":  # backward-compat alias
        status = "current"

    if status == "current":
        additions = data.get("additions", [])
        removals = data.get("removals", [])
        if additions not in (None, []) or removals not in (None, []):
            return None
        if missing_existing:
            return None
        return {
            "status": "current",
            "additions": [],
            "removals": [],
            "reason": str(data.get("reason", "")).strip(),
        }

    if allow_applied and status == "applied":
        return {
            "status": "applied",
            "additions": [],
            "removals": [],
            "reason": str(data.get("reason", "")).strip(),
        }

    if status != "stale":
        return None

    additions_raw = data.get("additions", [])
    removals_raw = data.get("removals", [])
    if not isinstance(additions_raw, list) or not isinstance(removals_raw, list):
        return None

    additions = sorted({
        item.strip()
        for item in additions_raw
        if isinstance(item, str) and item.strip()
    })
    removals = sorted(
        set(
            item.strip()
            for item in removals_raw
            if isinstance(item, str) and item.strip()
        ) | set(missing_existing)
    )

    if not additions and not removals:
        return None

    if any(not _path_exists_in_codespace(codespace, path) for path in additions):
        return None

    return {
        "status": "stale",
        "additions": additions,
        "removals": removals,
        "reason": str(data.get("reason", "")).strip(),
    }


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

    signal_file = PathRegistry(
        artifacts_dir.parent
    ).scan_related_files_update_signal(section_name)
    missing_existing = _missing_existing_related_files(section_text_raw, codespace)

    prev_hash = ""
    if codemap_hash_file.is_file():
        prev_hash = codemap_hash_file.read_text().strip()

    cached_signal = _normalize_validation_signal(
        signal_file,
        codespace=codespace,
        missing_existing=missing_existing,
        allow_applied=True,
    )

    if combined_hash == prev_hash and prev_hash and cached_signal is not None:
        print(
            f"[EXPLORE] {section_name}: Related Files exist, "
            "codemap+section unchanged — skipping",
        )
        return

    if combined_hash == prev_hash and prev_hash:
        print(
            f"[EXPLORE][WARN] {section_name}: cached related-files hash has "
            "no reusable validation signal — revalidating",
        )

    print(
        f"[EXPLORE] {section_name}: validating Related Files "
        "against updated codemap/section",
    )

    validate_prompt = section_log / "validate-prompt.md"
    validate_output = section_log / "validate-output.md"

    corrections_ref = ""
    if corrections_file.is_file():
        corrections_ref = (
            f"3. Codemap corrections (authoritative fixes): "
            f"`{corrections_file}`"
        )

    missing_existing_section = ""
    if missing_existing:
        items = "\n".join(f"- {path}" for path in missing_existing)
        missing_existing_section = (
            "## Deterministic Missing Existing Entries\n"
            "These currently listed Related Files entries do not exist in the "
            "codespace:\n"
            f"{items}\n\n"
            "Treat each missing path as positive evidence that the current list "
            "is stale.\n"
        )
    else:
        missing_existing_section = (
            "## Deterministic Missing Existing Entries\n"
            "All currently listed Related Files entries currently exist.\n"
        )

    prompt = load_scan_template("validate_related_files.md").format(
        section_file=section_file,
        codemap_path=codemap_path,
        corrections_ref=corrections_ref,
        update_signal=signal_file,
        missing_existing_section=missing_existing_section,
    )
    violations = validate_dynamic_content(prompt)
    if violations:
        print(
            f"[EXPLORE] {section_name}: validate prompt blocked — "
            f"safety violations: {violations}",
        )
        return
    validate_prompt.write_text(prompt)

    signal_file.unlink(missing_ok=True)

    result = dispatch_agent(
        model=model_policy["validation"],
        project=codespace,
        prompt_file=validate_prompt,
        agent_file="scan-related-files-adjudicator.md",
        stdout_file=validate_output,
    )

    normalized = _normalize_validation_signal(
        signal_file,
        codespace=codespace,
        missing_existing=missing_existing,
    )

    escalation_model = model_policy.get("exploration", model_policy["validation"])
    if (
        result.returncode == 0
        and normalized is None
        and escalation_model != model_policy["validation"]
    ):
        print(
            f"[EXPLORE] {section_name}: validator produced no valid signal — "
            f"escalating to {escalation_model}",
        )
        signal_file.unlink(missing_ok=True)
        result = dispatch_agent(
            model=escalation_model,
            project=codespace,
            prompt_file=validate_prompt,
            agent_file="scan-related-files-adjudicator.md",
            stdout_file=validate_output,
        )
        normalized = _normalize_validation_signal(
            signal_file,
            codespace=codespace,
            missing_existing=missing_existing,
        )

    if result.returncode != 0:
        print(
            f"[EXPLORE] {section_name}: validation failed "
            "— keeping existing list",
        )
        return

    if normalized is None:
        print(
            f"[EXPLORE][WARN] {section_name}: validation exited 0 but produced "
            "no valid related-files signal — forcing revalidation on next run",
        )
        return

    write_json(signal_file, normalized)

    status = normalized["status"]
    if status == "stale":
        print(f"[EXPLORE] {section_name}: applying related-files updates")
        if not apply_related_files_update(section_file, signal_file):
            print(
                f"[EXPLORE] {section_name}: auto-apply failed — "
                "forcing revalidation on next run",
            )
            return
        normalized["status"] = "applied"
        write_json(signal_file, normalized)

        section_text_updated = section_file.read_text() if section_file.is_file() else ""
        section_hash = content_hash(strip_scan_summaries(section_text_updated))
        combined = f"{codemap_hash}:{corrections_hash}:{section_hash}"
        combined_hash = content_hash(combined)

    print(f"[EXPLORE] {section_name}: validation complete")
    codemap_hash_file.write_text(combined_hash)


__all__ = [
    "apply_related_files_update",
    "list_section_files",
    "validate_existing_related_files",
]
