"""Per-file deep scan analysis helpers."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from scan.scan_context import ScanContext
from scan.service.phase_failure_logger import log_phase_failure
from scan.service.template_loader import load_scan_template
from scan.related.match_updater import update_match
from scan.codemap.cache import FileCardCache, is_valid_cached_feedback
from scan.scan_dispatcher import dispatch_agent

if TYPE_CHECKING:
    from containers import PromptGuard, TaskRouterService

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


@dataclass(frozen=True)
class AnalysisLogPaths:
    """Log file paths for a single deep-scan analysis run."""

    prompt: Path
    response: Path
    stderr: Path
    feedback: Path


def _resolve_log_paths(
    section_log: Path,
    source_file: str,
) -> AnalysisLogPaths:
    name = safe_name(source_file)
    return AnalysisLogPaths(
        prompt=section_log / f"deep-{name}-prompt.md",
        response=section_log / f"deep-{name}-response.md",
        stderr=section_log / f"deep-{name}.stderr.log",
        feedback=section_log / f"deep-{name}-feedback.json",
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


class Analyzer:
    """Per-file deep scan analysis.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
    ) -> None:
        self._prompt_guard = prompt_guard
        self._task_router = task_router

    def _validate_and_write_prompt(
        self,
        prompt: str,
        prompt_file: Path,
        section_name: str,
        source_file: str,
        scan_log_dir: Path,
    ) -> bool:
        violations = self._prompt_guard.validate_dynamic(prompt)
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
        self,
        ctx: ScanContext,
        prompt_file: Path,
        response_file: Path,
        stderr_file: Path,
        section_name: str,
        source_file: str,
    ) -> bool:
        result = dispatch_agent(
            model=ctx.model_policy["deep_analysis"],
            project=ctx.codespace,
            prompt_file=prompt_file,
            agent_file=self._task_router.agent_for("scan.deep_analyze"),
            stdout_file=response_file,
            stderr_file=stderr_file,
        )
        if result.returncode != 0:
            log_phase_failure(
                "deep-scan",
                f"{section_name}:{source_file}",
                f"deep analysis failed (see {stderr_file})",
                ctx.scan_log_dir,
            )
            return False

        if not response_file.is_file() or not response_file.read_text(encoding="utf-8").strip():
            log_phase_failure(
                "deep-scan",
                f"{section_name}:{source_file}",
                "agent produced empty output",
                ctx.scan_log_dir,
            )
            return False

        return True

    def analyze_file(
        self,
        section_file: Path,
        section_name: str,
        source_file: str,
        ctx: ScanContext,
        file_card_cache: FileCardCache,
    ) -> bool:
        """Run deep analysis on a single file."""
        abs_source = ctx.codespace / source_file
        if not abs_source.is_file():
            log_phase_failure(
                "deep-scan",
                f"{section_name}:{source_file}",
                "source file missing in codespace",
                ctx.scan_log_dir,
            )
            return False

        section_log = ctx.scan_log_dir / section_name
        section_log.mkdir(parents=True, exist_ok=True)
        log_paths = _resolve_log_paths(section_log, source_file)

        content_key = file_card_cache.content_hash(
            section_file,
            abs_source,
            ctx.corrections_path,
        )

        cached = _try_cached_response(
            file_card_cache, content_key,
            section_file, section_name, source_file,
            log_paths.response, log_paths.feedback, ctx.scan_log_dir,
        )
        if cached is not None:
            return cached

        prompt = _build_prompt(
            section_file, abs_source, ctx.codemap_path,
            ctx.corrections_path, log_paths.feedback, source_file,
        )

        if not self._validate_and_write_prompt(
            prompt, log_paths.prompt, section_name, source_file, ctx.scan_log_dir,
        ):
            return False

        if not self._dispatch_and_validate(
            ctx, log_paths.prompt,
            log_paths.response, log_paths.stderr,
            section_name, source_file,
        ):
            return False

        file_card_cache.store(
            content_key,
            log_paths.response,
            log_paths.feedback if log_paths.feedback.is_file() else None,
        )

        if not update_match(section_file, source_file, log_paths.response):
            log_phase_failure(
                "deep-update",
                f"{section_name}:{source_file}",
                "failed to update section file",
                ctx.scan_log_dir,
            )
            return False

        print(f"[DEEP] {section_name} x {Path(source_file).name}")
        return True


# ------------------------------------------------------------------
# Backward-compat free function wrapper
# ------------------------------------------------------------------


def _default_analyzer() -> Analyzer:
    from containers import Services
    return Analyzer(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )


def analyze_file(
    section_file: Path,
    section_name: str,
    source_file: str,
    ctx: ScanContext,
    file_card_cache: FileCardCache,
) -> bool:
    """Run deep analysis on a single file."""
    return _default_analyzer().analyze_file(
        section_file, section_name, source_file, ctx, file_card_cache,
    )
