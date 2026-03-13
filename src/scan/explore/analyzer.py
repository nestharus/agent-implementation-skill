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

_PATH_TOKEN_MAX_LENGTH = 80
_SOURCE_HASH_LENGTH = 10


def safe_name(source_file: str) -> str:
    """Compute the safe filename token for a source file path."""
    path_token = source_file.replace("/", "_").replace(".", "_")
    path_token = re.sub(r"[^a-zA-Z0-9_-]", "", path_token)[:_PATH_TOKEN_MAX_LENGTH]

    if "." in source_file:
        extension_token = source_file.rsplit(".", 1)[1]
    else:
        extension_token = "noext"

    source_hash = hashlib.sha1(  # noqa: S324
        source_file.encode(),
    ).hexdigest()[:_SOURCE_HASH_LENGTH]
    return f"{path_token}.{extension_token}.{source_hash}"


def _resolve_log_paths(
    section_log: Path,
    source_file: str,
) -> tuple[Path, Path, Path, Path]:
    name = safe_name(source_file)
    return (
        section_log / f"deep-{name}-prompt.md",
        section_log / f"deep-{name}-response.md",
        section_log / f"deep-{name}.stderr.log",
        section_log / f"deep-{name}-feedback.json",
    )


def _try_cached_response(
    file_card_cache: FileCardCache,
    content_key: str,
    section_file: Path,
    section_name: str,
    source_file: str,
    response_file: Path,
    feedback_file: Path,
    scan_log_dir: Path,
) -> bool | None:
    cached_response = file_card_cache.get(content_key)
    if cached_response is None:
        return None

    cached_feedback = file_card_cache.get_feedback(content_key)
    if cached_feedback is not None and not is_valid_cached_feedback(cached_feedback):
        print(
            f"  {section_name}: {source_file} cached feedback "
            "invalid — re-dispatching",
        )
        return None

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


def _build_prompt(
    section_file: Path,
    abs_source: Path,
    codemap_path: Path,
    corrections_path: Path,
    feedback_file: Path,
    source_file: str,
) -> str:
    corrections_ref = ""
    if corrections_path.is_file():
        corrections_ref = (
            f"\n4. Codemap corrections (authoritative fixes): "
            f"`{corrections_path}`"
        )

    return load_scan_template("deep_analysis.md").format(
        section_file=section_file,
        abs_source=abs_source,
        codemap_path=codemap_path,
        corrections_ref=corrections_ref,
        feedback_file=feedback_file,
        source_file=source_file,
    )


def _validate_and_write_prompt(
    prompt: str,
    prompt_file: Path,
    section_name: str,
    source_file: str,
    scan_log_dir: Path,
) -> bool:
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
    return True


def _dispatch_and_validate(
    model_policy: dict[str, str],
    codespace: Path,
    prompt_file: Path,
    response_file: Path,
    stderr_file: Path,
    section_name: str,
    source_file: str,
    scan_log_dir: Path,
) -> bool:
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

    return True


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
    prompt_file, response_file, stderr_file, feedback_file = _resolve_log_paths(
        section_log, source_file,
    )

    content_key = file_card_cache.content_hash(
        section_file,
        abs_source,
        corrections_path,
    )

    cached = _try_cached_response(
        file_card_cache, content_key,
        section_file, section_name, source_file,
        response_file, feedback_file, scan_log_dir,
    )
    if cached is not None:
        return cached

    prompt = _build_prompt(
        section_file, abs_source, codemap_path,
        corrections_path, feedback_file, source_file,
    )

    if not _validate_and_write_prompt(
        prompt, prompt_file, section_name, source_file, scan_log_dir,
    ):
        return False

    if not _dispatch_and_validate(
        model_policy, codespace, prompt_file,
        response_file, stderr_file,
        section_name, source_file, scan_log_dir,
    ):
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
