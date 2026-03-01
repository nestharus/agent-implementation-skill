"""Section exploration + validation + patching.

Translates ``run_section_exploration()`` and helpers from scan.sh.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from .cache import strip_scan_summaries
from .dispatch import dispatch_agent, read_scan_model_policy

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _load_template(name: str) -> str:
    return (_TEMPLATES / name).read_text()


def list_section_files(sections_dir: Path) -> list[Path]:
    """Return sorted list of ``section-N.md`` files."""
    files = [
        f
        for f in sections_dir.iterdir()
        if f.is_file()
        and re.match(r"section-\d+\.md$", f.name)
    ]
    return sorted(files)


def apply_related_files_update(
    section_file: Path, signal_file: Path,
) -> bool:
    """Apply additions/removals from a related-files update signal.

    Returns ``True`` if the update was applied, ``False`` otherwise.
    """
    if not signal_file.exists():
        return False

    try:
        signal = json.loads(signal_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[RELATED FILES][WARN] Malformed update signal: "
            f"{signal_file} ({exc})",
        )
        # V3/R58: Preserve corrupted signal for diagnosis.
        try:
            signal_file.rename(
                signal_file.with_suffix(".malformed.json"))
        except OSError:
            pass  # Best-effort preserve
        return False

    if signal.get("status") != "stale":
        return False

    from .related_files import block_insert_position, find_entry_span

    section = section_file.read_text()
    removals = signal.get("removals", [])
    additions = signal.get("additions", [])

    if not removals and not additions:
        return False

    # Process removals: block-scoped — only within Related Files block
    for rm_path in removals:
        span = find_entry_span(section, rm_path)
        if span is None:
            continue
        entry_start, entry_end = span
        before = section[:entry_start].rstrip("\n")
        after = section[entry_end:]
        section = before + after

    # Process additions: append at end of Related Files block
    for add_path in additions:
        if find_entry_span(section, add_path) is not None:
            continue  # already present
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


def run_section_exploration(
    *,
    sections_dir: Path,
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    model_policy: dict[str, str] | None = None,
) -> None:
    """Dispatch agents per section to identify related files."""
    if model_policy is None:
        model_policy = read_scan_model_policy(artifacts_dir)
    section_files = list_section_files(sections_dir)
    corrections_file = artifacts_dir / "signals" / "codemap-corrections.json"

    for section_file in section_files:
        section_name = section_file.stem  # e.g. "section-01"

        # If section already has Related Files, run validation pass
        section_text = section_file.read_text()
        if "## Related Files" in section_text:
            _validate_existing_related_files(
                section_file=section_file,
                section_name=section_name,
                codemap_path=codemap_path,
                codespace=codespace,
                artifacts_dir=artifacts_dir,
                scan_log_dir=scan_log_dir,
                corrections_file=corrections_file,
                model_policy=model_policy,
            )
            continue

        # Fresh exploration
        _explore_section(
            section_file=section_file,
            section_name=section_name,
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            corrections_file=corrections_file,
            model_policy=model_policy,
        )


# ------------------------------------------------------------------
# Validation path (section already has Related Files)
# ------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return hex sha256 of file contents, or empty string on error."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _validate_existing_related_files(
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

    # Build combined hash
    codemap_hash = _sha256_file(codemap_path) if codemap_path.is_file() else ""
    corrections_hash = (
        _sha256_file(corrections_file) if corrections_file.is_file() else ""
    )
    section_text_raw = section_file.read_text() if section_file.is_file() else ""
    section_hash = hashlib.sha256(
        strip_scan_summaries(section_text_raw).encode(),
    ).hexdigest()
    combined = f"{codemap_hash}:{corrections_hash}:{section_hash}"
    combined_hash = hashlib.sha256(combined.encode()).hexdigest()

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

    # Dispatch validation
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

    prompt = _load_template("validate_related_files.md").format(
        section_file=section_file,
        codemap_path=codemap_path,
        corrections_ref=corrections_ref,
        update_signal=update_signal,
    )
    validate_prompt.write_text(prompt)

    result = dispatch_agent(
        model=model_policy.get("validation", "claude-opus"),
        project=codespace,
        prompt_file=validate_prompt,
        agent_file="scan-related-files-adjudicator.md",
        stdout_file=validate_output,
    )

    if result.returncode == 0:
        print(f"[EXPLORE] {section_name}: validation complete")

        # Apply validation results if stale
        signal_file = (
            artifacts_dir
            / "signals"
            / f"{section_name}-related-files-update.json"
        )
        write_hash = True
        if signal_file.is_file():
            try:
                data = json.loads(signal_file.read_text())
                status = data.get("status", "")
            except (json.JSONDecodeError, OSError) as exc:
                # Fail-closed: malformed signal must NOT write skip-hash
                print(
                    f"[EXPLORE][WARN] {section_name}: malformed "
                    f"related-files update signal ({exc}) — "
                    f"forcing revalidation on next run",
                )
                # Preserve corrupted file for diagnosis
                malformed_path = signal_file.with_suffix(
                    ".malformed.json")
                try:
                    signal_file.rename(malformed_path)
                except OSError:
                    pass
                write_hash = False
                status = ""

            if status == "stale":
                print(
                    f"[EXPLORE] {section_name}: "
                    "applying related-files updates",
                )
                if apply_related_files_update(section_file, signal_file):
                    # Mark signal as applied so re-runs don't re-apply
                    data["status"] = "applied"
                    signal_file.write_text(
                        json.dumps(data, indent=2),
                    )
                    print(
                        f"[EXPLORE] {section_name}: list updated",
                    )
                    # Recompute hash from updated section file
                    section_text_updated = (
                        section_file.read_text()
                        if section_file.is_file()
                        else ""
                    )
                    section_hash = hashlib.sha256(
                        strip_scan_summaries(section_text_updated).encode(),
                    ).hexdigest()
                    combined = (
                        f"{codemap_hash}:{corrections_hash}:{section_hash}"
                    )
                    combined_hash = hashlib.sha256(
                        combined.encode(),
                    ).hexdigest()
                else:
                    print(
                        f"[EXPLORE] {section_name}: auto-apply "
                        "failed — keeping existing list",
                    )
                    write_hash = False
            elif status and status not in ("ok", "applied"):
                # Unknown status — treat as invalid
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


# ------------------------------------------------------------------
# Fresh exploration path
# ------------------------------------------------------------------


def _explore_section(
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
    """Dispatch agent to identify related files for a new section."""
    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    prompt_file = section_log / "explore-prompt.md"
    response_file = section_log / "explore-response.md"
    stderr_file = section_log / "explore.stderr.log"

    corrections_signal = (
        artifacts_dir / "signals" / "codemap-corrections.json"
    )

    prompt = _load_template("explore_section.md").format(
        codemap_path=codemap_path,
        section_file=section_file,
        corrections_signal=corrections_signal,
    )
    prompt_file.write_text(prompt)

    result = dispatch_agent(
        model=model_policy.get("exploration", "claude-opus"),
        project=codespace,
        prompt_file=prompt_file,
        agent_file="scan-related-files-explorer.md",
        stdout_file=response_file,
        stderr_file=stderr_file,
    )

    if result.returncode != 0:
        _log_phase_failure(
            scan_log_dir,
            "quick-explore",
            section_name,
            f"exploration agent failed (see {stderr_file})",
        )
        return

    # Append only the Related Files block to section file
    if response_file.is_file():
        response_text = response_file.read_text()
        if "## Related Files" in response_text:
            # Extract only the ## Related Files block
            rf_idx = response_text.index("## Related Files")
            rf_block = response_text[rf_idx:]
            # Trim at next ## heading that isn't a ### sub-heading
            lines = rf_block.split("\n")
            end_idx = len(lines)
            for i, line in enumerate(lines[1:], start=1):
                if line.startswith("## ") and not line.startswith("### "):
                    end_idx = i
                    break
            rf_block = "\n".join(lines[:end_idx]).rstrip()

            with section_file.open("a") as f:
                f.write("\n")
                f.write(rf_block)
            print(f"[EXPLORE] {section_name} — related files identified")
        else:
            _log_phase_failure(
                scan_log_dir,
                "quick-explore",
                section_name,
                "agent output missing Related Files block",
            )


def _log_phase_failure(
    scan_log_dir: Path,
    phase: str,
    context: str,
    message: str,
) -> None:
    from datetime import datetime, timezone

    failure_log = scan_log_dir / "failures.log"
    ts = datetime.now(tz=timezone.utc).isoformat()
    line = f"{ts} phase={phase} context={context} message={message}\n"
    with failure_log.open("a") as f:
        f.write(line)
    print(
        f"[FAIL] phase={phase} context={context} message={message}",
        file=sys.stderr,
    )
