"""Codemap build, verify, and freshness check.

Translates ``run_codemap_build()`` and its helpers from scan.sh.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from scan.service.phase_failure_logger import log_phase_failure
from scan.service.template_loader import load_scan_template

from scan.scan_dispatcher import dispatch_agent, read_scan_model_policy
from .fingerprint import NON_GIT_SENTINEL, compute_codespace_fingerprint

if TYPE_CHECKING:
    from containers import ArtifactIOService, PromptGuard, TaskRouterService


class CodemapBuilder:
    """Codemap build, verify, and freshness check.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._prompt_guard = prompt_guard
        self._task_router = task_router
        self._artifact_io = artifact_io

    # ------------------------------------------------------------------
    # Helpers for run_codemap_build
    # ------------------------------------------------------------------

    def _try_reuse_existing(
        self,
        *,
        codemap_path: Path,
        codespace: Path,
        artifacts_dir: Path,
        scan_log_dir: Path,
        fingerprint_path: Path,
        model_policy: dict[str, str],
    ) -> bool | None:
        """Check whether an existing codemap can be reused.

        Returns ``True`` if reuse is valid, ``False`` if a rebuild is needed,
        or ``None`` if no existing codemap is available.
        """
        if not (codemap_path.is_file() and codemap_path.stat().st_size > 0):
            return None

        current_fp = compute_codespace_fingerprint(codespace)

        if fingerprint_path.is_file():
            stored_fp = fingerprint_path.read_text().strip()

            if current_fp == stored_fp and current_fp != NON_GIT_SENTINEL:
                print(
                    f"[CODEMAP] Fingerprint unchanged — reusing existing "
                    f"artifact: {codemap_path}",
                )
                return True

            # Fingerprint changed or non-git — dispatch freshness verifier
            if current_fp == NON_GIT_SENTINEL:
                print(
                    "[CODEMAP] Non-git workspace — dispatching verifier "
                    "for heuristic freshness check",
                )
            else:
                print(
                    "[CODEMAP] Codespace fingerprint changed — "
                    "dispatching verifier",
                )
        else:
            # No stored fingerprint — cannot assume codemap is fresh.
            # Dispatch verifier to decide reuse vs rebuild.
            stored_fp = ""
            print(
                "[CODEMAP] No stored fingerprint — dispatching verifier "
                "to check codemap freshness",
            )

        if self._run_freshness_check(
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            fingerprint_path=fingerprint_path,
            current_fp=current_fp,
            stored_fp=stored_fp,
            model_policy=model_policy,
        ):
            return True
        return False

    # Template filename used when ``skeleton_only=True``.
    _SKELETON_TEMPLATE = "codemap_skeleton_build.md"

    def _prepare_build_prompt(
        self,
        *,
        codemap_path: Path,
        artifacts_dir: Path,
        scan_log_dir: Path,
        skeleton_only: bool = False,
    ) -> Path | None:
        """Load the codemap-build template, validate it, and write the prompt file.

        When *skeleton_only* is ``True`` the skeleton template is used
        instead of the full codemap-build template.  This produces a
        coarse top-level module listing rather than a deep codemap.  The
        skeleton template is expected at
        ``templates/scan/codemap_skeleton_build.md`` (added by sub-piece 4a).

        Returns the prompt file path on success, or ``None`` if the prompt
        was blocked by safety validation.
        """
        codemap_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_file = scan_log_dir / "codemap-prompt.md"

        template_name = (
            self._SKELETON_TEMPLATE if skeleton_only else "codemap_build.md"
        )
        _paths = PathRegistry(artifacts_dir.parent)
        prompt = load_scan_template(template_name).format(
            project_mode_path=_paths.project_mode_txt(),
            project_mode_signal=_paths.project_mode_json(),
        )
        violations = self._prompt_guard.validate_dynamic(prompt)
        if violations:
            log_phase_failure(
                "quick-codemap",
                codemap_path.name,
                f"prompt blocked — safety violations: {violations}",
                scan_log_dir,
            )
            return None
        prompt_file.write_text(prompt)
        return prompt_file

    def _dispatch_build_agent(
        self,
        *,
        codemap_path: Path,
        codespace: Path,
        scan_log_dir: Path,
        prompt_file: Path,
        model_policy: dict[str, str],
    ) -> subprocess.CompletedProcess[str] | None:
        """Dispatch the codemap build agent and validate output.

        Returns the completed process on success, or ``None`` on failure
        (return-code non-zero or empty output).
        """
        stderr_file = scan_log_dir / "codemap.stderr.log"

        result = dispatch_agent(
            model=model_policy["codemap_build"],
            project=codespace,
            prompt_file=prompt_file,
            agent_file=self._task_router.agent_for("scan.codemap_build"),
            stdout_file=codemap_path,
            stderr_file=stderr_file,
        )

        if result.returncode != 0:
            log_phase_failure(
                "quick-codemap",
                codemap_path.name,
                f"codemap agent failed (see {stderr_file})",
                scan_log_dir,
            )
            return None

        if not _has_content(codemap_path):
            log_phase_failure(
                "quick-codemap",
                codemap_path.name,
                "codemap agent produced empty output",
                scan_log_dir,
            )
            return None

        return result

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run_codemap_build(
        self,
        *,
        codemap_path: Path,
        codespace: Path,
        artifacts_dir: Path,
        scan_log_dir: Path,
        fingerprint_path: Path,
        model_policy: dict[str, str] | None = None,
    ) -> bool:
        """Build (or reuse) the codemap artifact.

        Returns ``True`` on success, ``False`` on failure.
        """
        if model_policy is None:
            model_policy = read_scan_model_policy(artifacts_dir)

        reuse = self._try_reuse_existing(
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            fingerprint_path=fingerprint_path,
            model_policy=model_policy,
        )
        if reuse is True:
            return True

        prompt_file = self._prepare_build_prompt(
            codemap_path=codemap_path,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
        )
        if prompt_file is None:
            return False

        result = self._dispatch_build_agent(
            codemap_path=codemap_path,
            codespace=codespace,
            scan_log_dir=scan_log_dir,
            prompt_file=prompt_file,
            model_policy=model_policy,
        )
        if result is None:
            return False

        print(f"[CODEMAP] Wrote: {codemap_path}")

        self._run_verification(
            codemap_path=codemap_path,
            codespace=codespace,
            artifacts_dir=artifacts_dir,
            scan_log_dir=scan_log_dir,
            model_policy=model_policy,
        )

        _store_codespace_fingerprint(
            codespace=codespace,
            fingerprint_path=fingerprint_path,
        )

        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_freshness_check(
        self,
        *,
        codemap_path: Path,
        codespace: Path,
        artifacts_dir: Path,
        scan_log_dir: Path,
        fingerprint_path: Path,
        current_fp: str,
        stored_fp: str,
        model_policy: dict[str, str],
    ) -> bool:
        """Dispatch GLM verifier for codemap freshness.

        Returns ``True`` if verifier says codemap is still valid (reuse).
        Returns ``False`` if rebuild is needed.
        """
        freshness_prompt = scan_log_dir / "codemap-freshness-prompt.md"
        freshness_output = scan_log_dir / "codemap-freshness-output.md"
        freshness_signal = artifacts_dir / "signals" / "codemap-freshness.json"

        if current_fp == NON_GIT_SENTINEL:
            change_desc = (
                "Non-git workspace; no reliable cheap fingerprint available.\n"
                "Use heuristic checks (list top-level dirs, sample key files) "
                "to assess freshness."
            )
        else:
            change_desc = (
                f"- Previous fingerprint: {stored_fp}\n"
                f"- Current fingerprint: {current_fp}"
            )

        # Thread codemap corrections into freshness evaluation (P9/Thread #1)
        corrections_path = PathRegistry(artifacts_dir.parent).corrections()
        corrections_ref = ""
        if corrections_path.is_file():
            corrections_ref = (
                f"\n3. Codemap corrections (authoritative fixes): "
                f"`{corrections_path}`"
            )

        prompt = load_scan_template("codemap_freshness.md").format(
            codemap_path=codemap_path,
            codespace=codespace,
            change_description=change_desc,
            freshness_signal=freshness_signal,
            corrections_ref=corrections_ref,
        )
        violations = self._prompt_guard.validate_dynamic(prompt)
        if violations:
            print(
                f"[CODEMAP] Freshness prompt blocked — safety violations: "
                f"{violations}",
            )
            return False
        freshness_prompt.write_text(prompt)

        result = dispatch_agent(
            model=model_policy["codemap_freshness"],
            project=codespace,
            prompt_file=freshness_prompt,
            agent_file=self._task_router.agent_for("scan.codemap_freshness"),
            stdout_file=freshness_output,
        )

        return self._interpret_freshness_signal(
            result, freshness_signal, fingerprint_path, current_fp,
        )

    def _interpret_freshness_signal(
        self,
        result: object,
        freshness_signal: Path,
        fingerprint_path: Path,
        current_fp: str,
    ) -> bool:
        """Interpret the freshness verifier result and signal file.

        Returns True if codemap is still valid, False if rebuild is needed.
        """
        if result.returncode != 0:
            print("[CODEMAP] Freshness check failed — rebuilding codemap")
            return False
        if not freshness_signal.is_file():
            print(
                "[CODEMAP] Verifier did not produce signal — "
                "rebuilding to be safe",
            )
            return False

        data = self._artifact_io.read_json(freshness_signal)
        if not isinstance(data, dict):
            print(
                f"[CODEMAP][WARN] Malformed freshness signal at "
                f"{freshness_signal} — renaming to .malformed.json",
            )
            if data is not None:
                self._artifact_io.rename_malformed(freshness_signal)
            return False

        rebuild = data.get("rebuild", True)
        if str(rebuild).lower() == "false" or rebuild is False:
            print("[CODEMAP] Verifier says codemap still valid — reusing")
            fingerprint_path.write_text(current_fp)
            return True

        print("[CODEMAP] Verifier says rebuild needed — rebuilding codemap")
        return False

    def _run_verification(
        self,
        *,
        codemap_path: Path,
        codespace: Path,
        artifacts_dir: Path,
        scan_log_dir: Path,
        model_policy: dict[str, str],
    ) -> None:
        """P5: Lightweight codemap verifier — sample files to validate routing."""
        verifier_prompt = scan_log_dir / "codemap-verify-prompt.md"
        verifier_output = scan_log_dir / "codemap-verify-output.md"
        corrections_signal = PathRegistry(artifacts_dir.parent).corrections()

        prompt = load_scan_template("codemap_verify.md").format(
            codemap_path=codemap_path,
            codespace=codespace,
            corrections_signal=corrections_signal,
        )
        violations = self._prompt_guard.validate_dynamic(prompt)
        if violations:
            print(
                f"[CODEMAP] Verify prompt blocked — safety violations: "
                f"{violations}",
            )
            return
        verifier_prompt.write_text(prompt)

        result = dispatch_agent(
            model=model_policy["validation"],
            project=codespace,
            prompt_file=verifier_prompt,
            agent_file=self._task_router.agent_for("scan.codemap_verify"),
            stdout_file=verifier_output,
        )

        if result.returncode == 0:
            print(f"[CODEMAP] Verification complete (see {verifier_output})")
        else:
            print("[CODEMAP] Verification failed — codemap used as-is")


# ------------------------------------------------------------------
# Pure helpers (no Services usage)
# ------------------------------------------------------------------


def _store_codespace_fingerprint(
    *,
    codespace: Path,
    fingerprint_path: Path,
) -> None:
    fp = compute_codespace_fingerprint(codespace)
    fingerprint_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint_path.write_text(fp)
    print(f"[CODEMAP] Stored codespace fingerprint: {fingerprint_path}")


def _has_content(path: Path) -> bool:
    """Return True if file exists and has non-whitespace content."""
    try:
        return bool(path.read_text().strip())
    except OSError:
        return False


def write_section_fragments(paths: PathRegistry) -> int:
    """Split the global codemap into per-section fragments.

    For each section spec file, reads the ``## Related Files`` block to
    get the section's file list, then extracts the relevant portion of
    the global codemap and writes it to
    ``PathRegistry.section_codemap(num)``.

    This is a best-effort seeding operation.  If the global codemap does
    not exist or a section has no related files, that section is silently
    skipped.

    Returns the number of fragments written.
    """
    codemap_path = paths.codemap()
    if not codemap_path.is_file():
        return 0

    codemap_text = codemap_path.read_text(encoding="utf-8")
    if not codemap_text.strip():
        return 0

    sections_dir = paths.sections_dir()
    if not sections_dir.is_dir():
        return 0

    from scan.related.cli_handler import extract_related_files

    written = 0
    for section_file in sorted(sections_dir.glob("section-*.md")):
        stem = section_file.stem  # "section-01"
        parts = stem.split("-", 1)
        if len(parts) < 2:
            continue
        section_number = parts[1]

        related = extract_related_files(
            section_file.read_text(encoding="utf-8"),
        )
        if not related:
            continue

        fragment = _extract_codemap_fragment(codemap_text, related)
        if not fragment:
            continue

        out_path = paths.section_codemap(section_number)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(fragment, encoding="utf-8")
        written += 1

    return written


def _extract_codemap_fragment(
    codemap_text: str,
    related_files: list[str],
) -> str:
    """Extract the portions of *codemap_text* relevant to *related_files*.

    Scans each line of the codemap.  A line is included if any related-
    file path appears in it (substring match).  Section headers (lines
    starting with ``#``) are always included to preserve document
    structure, but only if they precede at least one matched line.

    Returns the assembled fragment text, or an empty string if nothing
    matched.
    """
    if not related_files:
        return ""

    lines = codemap_text.splitlines(keepends=True)
    # Normalise related files for matching (strip leading ./ or /)
    normalised = [_normalise_path(f) for f in related_files]

    matched_indices: set[int] = set()
    for i, line in enumerate(lines):
        if any(norm in line for norm in normalised):
            matched_indices.add(i)

    if not matched_indices:
        return ""

    # Include section headers that precede matched content lines
    result_indices: set[int] = set(matched_indices)
    last_headers: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            last_headers = [i]
        elif i in matched_indices:
            result_indices.update(last_headers)

    # Always include the first line if it's the document title
    if lines and lines[0].strip().startswith("#"):
        result_indices.add(0)

    return "".join(lines[i] for i in sorted(result_indices))


def _normalise_path(path: str) -> str:
    """Strip leading ``./`` or ``/`` for substring matching."""
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    elif p.startswith("/"):
        p = p[1:]
    return p
