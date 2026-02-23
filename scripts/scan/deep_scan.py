"""Deep scan: tier ranking, per-file analysis, summary application.

Translates ``run_deep_scan()`` and helpers from scan.sh.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from .cache import FileCardCache
from .dispatch import dispatch_agent
from .exploration import list_section_files
from .feedback import collect_and_route_feedback

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _load_template(name: str) -> str:
    return (_TEMPLATES / name).read_text()


# ------------------------------------------------------------------
# Tier file validation
# ------------------------------------------------------------------


def validate_tier_file(tier_file: Path) -> bool:
    """Validate tier file structure: valid JSON with required fields."""
    try:
        data = json.loads(tier_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    tiers = data.get("tiers")
    if not isinstance(tiers, dict):
        return False

    scan_now = data.get("scan_now")
    if not isinstance(scan_now, list) or not scan_now:
        return False

    for t in scan_now:
        if t not in tiers:
            return False

    return True


# ------------------------------------------------------------------
# Extract related files from section
# ------------------------------------------------------------------


def deep_scan_related_files(section_file: Path) -> list[str]:
    """Parse ``### <path>`` entries under ``## Related Files``."""
    lines = section_file.read_text().splitlines()
    in_related = False
    files: list[str] = []
    for line in lines:
        if line.strip() == "## Related Files":
            in_related = True
            continue
        if in_related and line.startswith("## "):
            in_related = False
            continue
        if in_related and line.startswith("### "):
            path = line[4:].strip()
            if path:
                files.append(path)
    return files


# ------------------------------------------------------------------
# Safe name computation (matches bash logic exactly)
# ------------------------------------------------------------------


def _safe_name(source_file: str) -> str:
    """Compute the safe filename token for a source file path.

    Matches the bash logic: tr '/.' '__', filter to alnum/underscore/dash,
    truncate to 80 chars, append extension token and sha1 prefix.
    """
    # path_token: replace / and . with _, keep only alnum + _ + -, truncate
    path_token = source_file.replace("/", "_").replace(".", "_")
    path_token = re.sub(r"[^a-zA-Z0-9_-]", "", path_token)[:80]

    # extension_token
    if "." in source_file:
        extension_token = source_file.rsplit(".", 1)[1]
    else:
        extension_token = "noext"

    # sha1 prefix (10 chars)
    source_hash = hashlib.sha1(  # noqa: S324
        source_file.encode(),
    ).hexdigest()[:10]

    return f"{path_token}.{extension_token}.{source_hash}"


# ------------------------------------------------------------------
# deep_already_annotated
# ------------------------------------------------------------------


def deep_already_annotated(
    section_file: Path,
    source_file: str,
    scan_log_dir: Path,
) -> bool:
    """Check if deep-scan response already exists for this pair."""
    section_name = section_file.stem
    section_log_dir = scan_log_dir / section_name
    sname = _safe_name(source_file)
    response = section_log_dir / f"deep-{sname}-response.md"
    return response.is_file() and response.stat().st_size > 0


# ------------------------------------------------------------------
# update_match — annotate section file from feedback
# ------------------------------------------------------------------


def update_match(
    section_file: Path,
    source_file: str,
    details_file: Path,
) -> bool:
    """Annotate section file with summary lines from feedback JSON.

    Returns ``True`` on success, ``False`` on failure.
    """
    feedback_name = details_file.name.replace("-response.md", "-feedback.json")
    feedback_file = details_file.parent / feedback_name

    if not feedback_file.exists():
        return True  # No feedback = no annotation needed

    try:
        feedback = json.loads(feedback_file.read_text())
    except (json.JSONDecodeError, OSError):
        return True

    lines = feedback.get("summary_lines")
    if not isinstance(lines, list) or not lines:
        return True

    # Filter to strings, cap at 3
    lines = [str(l) for l in lines if isinstance(l, str) and l.strip()][:3]
    if not lines:
        return True

    section = section_file.read_text()
    marker = f"### {source_file}"
    idx = section.find(marker)
    if idx == -1:
        return True

    summary = "\n".join(f"> {l}" for l in lines)

    rest = section[idx + len(marker) :]
    match = re.search(r"\n(?=###\s|##\s[^#])", rest)
    insert_pos = idx + len(marker) + (match.start() if match else len(rest))

    new_section = (
        section[:insert_pos].rstrip() + "\n" + summary + "\n" + section[insert_pos:]
    )
    section_file.write_text(new_section)
    return True


# ------------------------------------------------------------------
# Tier ranking
# ------------------------------------------------------------------


def _run_tier_ranking(
    *,
    section_file: Path,
    section_name: str,
    related_files: list[str],
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
) -> Path | None:
    """Dispatch GLM (escalating to Opus) for tier ranking.

    Returns the tier file path if successful, ``None`` on failure.
    """
    tier_file = artifacts_dir / "sections" / f"{section_name}-file-tiers.json"

    # Validate existing tier file
    if tier_file.is_file():
        if not validate_tier_file(tier_file):
            print(
                f"[TIER] {section_name}: existing tier file invalid "
                "(missing scan_now or bad schema) — regenerating",
            )
            tier_file.unlink()
        else:
            return tier_file

    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    tier_prompt = section_log / "tier-prompt.md"
    tier_output = section_log / "tier-output.md"

    file_list_text = "\n".join(f"- {rf}" for rf in related_files if rf.strip())

    prompt = _load_template("tier_ranking.md").format(
        section_file=section_file,
        file_list_text=file_list_text,
        tier_file=tier_file,
    )
    tier_prompt.write_text(prompt)

    # Try GLM first, escalate to Opus
    result = dispatch_agent(
        model="glm",
        project=codespace,
        prompt_file=tier_prompt,
        stdout_file=tier_output,
    )

    if result.returncode == 0:
        print(f"[TIER] {section_name}: file tiers ranked")
    else:
        print(
            f"[TIER] {section_name}: tier ranking failed with GLM "
            "— escalating to Opus",
        )
        result = dispatch_agent(
            model="claude-opus",
            project=codespace,
            prompt_file=tier_prompt,
            stdout_file=tier_output,
        )
        if result.returncode == 0:
            print(
                f"[TIER] {section_name}: file tiers ranked "
                "(via Opus escalation)",
            )
        else:
            print(
                f"[TIER] {section_name}: tier ranking failed after "
                "escalation — fail-closed",
            )
            # Write failure artifact
            signals_dir = artifacts_dir / "signals"
            signals_dir.mkdir(parents=True, exist_ok=True)
            fail_data = {
                "section": section_name,
                "related_files_count": len(related_files),
                "error_output": str(tier_output),
                "suggested_action": "manual_review_or_parent_escalation",
            }
            fail_path = signals_dir / f"{section_name}-tier-ranking-failed.json"
            fail_path.write_text(json.dumps(fail_data, indent=2))

    # Post-generation validation
    if tier_file.is_file() and not validate_tier_file(tier_file):
        print(f"[TIER] {section_name}: generated tier file invalid — fail-closed")
        signals_dir = artifacts_dir / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        fail_data = {
            "section": section_name,
            "error": "invalid_tier_file_schema",
            "detail": "Tier file missing scan_now or has invalid structure",
            "tier_file_path": str(tier_file),
            "suggested_action": "manual_review_or_parent_escalation",
        }
        fail_path = signals_dir / f"{section_name}-tier-ranking-invalid.json"
        fail_path.write_text(json.dumps(fail_data, indent=2))
        tier_file.unlink()

    return tier_file if tier_file.is_file() else None


def _get_scan_files(tier_file: Path) -> tuple[list[str], str]:
    """Read tier file and return (files_to_scan, tier_label).

    Returns ([], "") if no files to scan.
    """
    try:
        data = json.loads(tier_file.read_text())
    except (json.JSONDecodeError, OSError):
        return [], ""

    tiers = data.get("tiers", {})
    scan_now = data.get("scan_now", [])
    seen: set[str] = set()
    files: list[str] = []
    for tier_name in scan_now:
        for f in tiers.get(tier_name, []):
            if f not in seen:
                seen.add(f)
                files.append(f)

    label = "+".join(scan_now) if scan_now else "unknown"
    return files, label


# ------------------------------------------------------------------
# Per-file deep analysis
# ------------------------------------------------------------------


def _analyze_file(
    *,
    section_file: Path,
    section_name: str,
    source_file: str,
    codespace: Path,
    codemap_path: Path,
    scan_log_dir: Path,
    file_card_cache: FileCardCache,
) -> bool:
    """Run deep analysis on a single file.

    Returns ``True`` on success, ``False`` on failure.
    """
    abs_source = codespace / source_file

    if not abs_source.is_file():
        _log_phase_failure(
            scan_log_dir,
            "deep-scan",
            f"{section_name}:{source_file}",
            "source file missing in codespace",
        )
        return False

    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    sname = _safe_name(source_file)
    prompt_file = section_log / f"deep-{sname}-prompt.md"
    response_file = section_log / f"deep-{sname}-response.md"
    stderr_file = section_log / f"deep-{sname}.stderr.log"
    feedback_file = section_log / f"deep-{sname}-feedback.json"

    # Cache check
    content_key = file_card_cache.content_hash(section_file, abs_source)
    cached_response = file_card_cache.get(content_key)

    if cached_response is not None:
        print(f"  {section_name}: {source_file} (cached)")
        # Populate response and feedback from cache
        import shutil

        shutil.copy2(cached_response, response_file)
        cached_fb = file_card_cache.get_feedback(content_key)
        if cached_fb is not None:
            shutil.copy2(cached_fb, feedback_file)

        if not update_match(section_file, source_file, response_file):
            _log_phase_failure(
                scan_log_dir,
                "deep-update",
                f"{section_name}:{source_file}",
                "failed to update section file (cached)",
            )
            return False

        print(f"[DEEP] {section_name} x {Path(source_file).name} (cached)")
        return True

    # Dispatch analysis agent
    prompt = _load_template("deep_analysis.md").format(
        section_file=section_file,
        abs_source=abs_source,
        codemap_path=codemap_path,
        feedback_file=feedback_file,
        source_file=source_file,
    )
    prompt_file.write_text(prompt)

    result = dispatch_agent(
        model="glm",
        project=codespace,
        prompt_file=prompt_file,
        stdout_file=response_file,
        stderr_file=stderr_file,
    )

    if result.returncode != 0:
        _log_phase_failure(
            scan_log_dir,
            "deep-scan",
            f"{section_name}:{source_file}",
            f"deep analysis failed (see {stderr_file})",
        )
        return False

    if not response_file.is_file() or not response_file.read_text().strip():
        _log_phase_failure(
            scan_log_dir,
            "deep-scan",
            f"{section_name}:{source_file}",
            "agent produced empty output",
        )
        return False

    # Cache the result
    file_card_cache.store(
        content_key,
        response_file,
        feedback_file if feedback_file.is_file() else None,
    )

    if not update_match(section_file, source_file, response_file):
        _log_phase_failure(
            scan_log_dir,
            "deep-update",
            f"{section_name}:{source_file}",
            "failed to update section file",
        )
        return False

    print(f"[DEEP] {section_name} x {Path(source_file).name}")
    return True


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------


def run_deep_scan(
    *,
    sections_dir: Path,
    codemap_path: Path,
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
) -> bool:
    """Run deep scan over all sections.

    Returns ``True`` on full success, ``False`` if any failures occurred.
    """
    # Skip for greenfield projects
    mode_file = artifacts_dir / "project-mode.txt"
    if mode_file.is_file() and mode_file.read_text().strip() == "greenfield":
        print("=== Deep Scan: skipped (greenfield project) ===")
        return True

    print("=== Deep Scan: agent-driven analysis of confirmed related files ===")

    phase_failed = False
    section_files = list_section_files(sections_dir)
    file_card_cache = FileCardCache(artifacts_dir / "file-cards")

    for section_file in section_files:
        section_name = section_file.stem

        section_log = scan_log_dir / section_name
        section_log.mkdir(parents=True, exist_ok=True)

        # Skip greenfield sections
        sec_num = _extract_section_number(section_name)
        sec_mode_file = (
            artifacts_dir / "sections" / f"section-{sec_num}-mode.txt"
        )
        if sec_mode_file.is_file() and sec_mode_file.read_text().strip() == "greenfield":
            print(f"  {section_name}: skipped (greenfield section)")
            research_dir = artifacts_dir / "research"
            research_dir.mkdir(parents=True, exist_ok=True)
            research_file = research_dir / f"section-{sec_num}.md"
            if not research_file.is_file():
                research_file.write_text(
                    f"# Research: Section {sec_num} (Greenfield)\n\n"
                    "This section was classified as greenfield. "
                    "No existing code to analyze.\n"
                    "Research questions and design decisions should be "
                    "captured here.\n",
                )
            continue

        related_files = deep_scan_related_files(section_file)
        if not related_files:
            continue

        # Tier ranking
        tier_file = _run_tier_ranking(
            section_file=section_file,
            section_name=section_name,
            related_files=related_files,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
        )

        # Get scoped files from tier ranking
        scan_files: list[str] = []
        if tier_file is not None and tier_file.is_file():
            scan_files, tier_label = _get_scan_files(tier_file)
            if scan_files:
                total = len(related_files)
                scoped = len(scan_files)
                print(
                    f"[TIER] {section_name}: scanning {scoped} files "
                    f"({tier_label}) of {total} total",
                )

        if not scan_files:
            print(
                f"[DEEP] {section_name}: no tier ranking available "
                "— skipping deep scan (fail-closed)",
            )
            continue

        # Per-file analysis
        for source_file in scan_files:
            if not source_file.strip():
                continue

            if deep_already_annotated(section_file, source_file, scan_log_dir):
                continue

            ok = _analyze_file(
                section_file=section_file,
                section_name=section_name,
                source_file=source_file,
                codespace=codespace,
                codemap_path=codemap_path,
                scan_log_dir=scan_log_dir,
                file_card_cache=file_card_cache,
            )
            if not ok:
                phase_failed = True

    # Post-scan: collect feedback and route
    has_feedback = collect_and_route_feedback(
        section_files=section_files,
        codemap_path=codemap_path,
        codespace=codespace,
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
    )

    if phase_failed:
        print("=== Deep Scan Complete (with failures) ===")
        return False

    print("=== Deep Scan Complete ===")
    return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_section_number(section_name: str) -> str:
    """Extract the numeric part from a section name like 'section-01'."""
    m = re.search(r"\d+", section_name)
    return m.group(0) if m else ""


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
