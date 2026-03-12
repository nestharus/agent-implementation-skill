"""Per-file deep scan analysis helpers."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from scan.service.phase_failure_logger import log_phase_failure
from scan.service.template_loader import load_scan_template
from scan.related.match_updater import update_match
from scan.codemap.cache import FileCardCache, is_valid_cached_feedback
from scan.scan_dispatcher import dispatch_agent
from containers import Services


def safe_name(source_file: str) -> str:
    """Compute the safe filename token for a source file path."""
    path_token = source_file.replace("/", "_").replace(".", "_")
    path_token = re.sub(r"[^a-zA-Z0-9_-]", "", path_token)[:80]

    if "." in source_file:
        extension_token = source_file.rsplit(".", 1)[1]
    else:
        extension_token = "noext"

    source_hash = hashlib.sha1(  # noqa: S324
        source_file.encode(),
    ).hexdigest()[:10]
    return f"{path_token}.{extension_token}.{source_hash}"


def analyze_file(
    section_file: Path,
    section_name: str,
    source_file: str,
    codespace: Path,
    codemap_path: Path,
    corrections_path: Path,
    scan_log_dir: Path,
    file_card_cache: FileCardCache,
    model_policy: dict[str, str],
) -> bool:
    """Run deep analysis on a single file."""
    abs_source = codespace / source_file
    if not abs_source.is_file():
        log_phase_failure(
            "deep-scan",
            f"{section_name}:{source_file}",
            "source file missing in codespace",
            scan_log_dir,
        )
        return False

    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    name = safe_name(source_file)
    prompt_file = section_log / f"deep-{name}-prompt.md"
    response_file = section_log / f"deep-{name}-response.md"
    stderr_file = section_log / f"deep-{name}.stderr.log"
    feedback_file = section_log / f"deep-{name}-feedback.json"

    content_key = file_card_cache.content_hash(
        section_file,
        abs_source,
        corrections_path,
    )
    cached_response = file_card_cache.get(content_key)

    if cached_response is not None:
        cached_feedback = file_card_cache.get_feedback(content_key)
        if cached_feedback is not None and not is_valid_cached_feedback(cached_feedback):
            print(
                f"  {section_name}: {source_file} cached feedback "
                "invalid — re-dispatching",
            )
            cached_response = None
        else:
            print(f"  {section_name}: {source_file} (cached)")
            shutil.copy2(cached_response, response_file)
            if cached_feedback is not None:
                shutil.copy2(cached_feedback, feedback_file)

            if not update_match(section_file, source_file, response_file):
                log_phase_failure(
                    "deep-update",
                    f"{section_name}:{source_file}",
                    "failed to update section file (cached)",
                    scan_log_dir,
                )
                return False

            print(f"[DEEP] {section_name} x {Path(source_file).name} (cached)")
            return True

    corrections_ref = ""
    if corrections_path.is_file():
        corrections_ref = (
            f"\n4. Codemap corrections (authoritative fixes): "
            f"`{corrections_path}`"
        )

    prompt = load_scan_template("deep_analysis.md").format(
        section_file=section_file,
        abs_source=abs_source,
        codemap_path=codemap_path,
        corrections_ref=corrections_ref,
        feedback_file=feedback_file,
        source_file=source_file,
    )
    violations = Services.prompt_guard().validate_dynamic(prompt)
    if violations:
        log_phase_failure(
            "deep-scan",
            f"{section_name}:{source_file}",
            f"prompt blocked — safety violations: {violations}",
            scan_log_dir,
        )
        return False
    prompt_file.write_text(prompt, encoding="utf-8")

    result = dispatch_agent(
        model=model_policy["deep_analysis"],
        project=codespace,
        prompt_file=prompt_file,
        agent_file=Services.task_router().agent_for("scan.deep_analyze"),
        stdout_file=response_file,
        stderr_file=stderr_file,
    )
    if result.returncode != 0:
        log_phase_failure(
            "deep-scan",
            f"{section_name}:{source_file}",
            f"deep analysis failed (see {stderr_file})",
            scan_log_dir,
        )
        return False

    if not response_file.is_file() or not response_file.read_text(encoding="utf-8").strip():
        log_phase_failure(
            "deep-scan",
            f"{section_name}:{source_file}",
            "agent produced empty output",
            scan_log_dir,
        )
        return False

    file_card_cache.store(
        content_key,
        response_file,
        feedback_file if feedback_file.is_file() else None,
    )

    if not update_match(section_file, source_file, response_file):
        log_phase_failure(
            "deep-update",
            f"{section_name}:{source_file}",
            "failed to update section file",
            scan_log_dir,
        )
        return False

    print(f"[DEEP] {section_name} x {Path(source_file).name}")
    return True
