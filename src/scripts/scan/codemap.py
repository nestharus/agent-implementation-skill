"""Codemap build, verify, and freshness check.

Translates ``run_codemap_build()`` and its helpers from scan.sh.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .dispatch import dispatch_agent, read_scan_model_policy
from .fingerprint import NON_GIT_SENTINEL, compute_codespace_fingerprint

# Template directory lives alongside this module
_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _load_template(name: str) -> str:
    return (_TEMPLATES / name).read_text()


def run_codemap_build(
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
    # --- Reuse check ---
    if codemap_path.is_file() and codemap_path.stat().st_size > 0:
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

            if _run_freshness_check(
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
            # Fall through to rebuild

        else:
            # No stored fingerprint — cannot assume codemap is fresh.
            # Dispatch verifier to decide reuse vs rebuild.
            print(
                "[CODEMAP] No stored fingerprint — dispatching verifier "
                "to check codemap freshness",
            )
            if _run_freshness_check(
                codemap_path=codemap_path,
                codespace=codespace,
                artifacts_dir=artifacts_dir,
                scan_log_dir=scan_log_dir,
                fingerprint_path=fingerprint_path,
                current_fp=current_fp,
                stored_fp="",
                model_policy=model_policy,
            ):
                return True
            # Fall through to rebuild

    # --- Build codemap ---
    codemap_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_file = scan_log_dir / "codemap-prompt.md"
    stderr_file = scan_log_dir / "codemap.stderr.log"

    signals_dir = artifacts_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    prompt = _load_template("codemap_build.md").format(
        project_mode_path=artifacts_dir / "project-mode.txt",
        project_mode_signal=signals_dir / "project-mode.json",
    )
    prompt_file.write_text(prompt)

    result = dispatch_agent(
        model=model_policy["codemap_build"],
        project=codespace,
        prompt_file=prompt_file,
        agent_file="scan-codemap-builder.md",
        stdout_file=codemap_path,
        stderr_file=stderr_file,
    )

    if result.returncode != 0:
        _log_phase_failure(
            scan_log_dir,
            "quick-codemap",
            codemap_path.name,
            f"codemap agent failed (see {stderr_file})",
        )
        return False

    if not _has_content(codemap_path):
        _log_phase_failure(
            scan_log_dir,
            "quick-codemap",
            codemap_path.name,
            "codemap agent produced empty output",
        )
        return False

    print(f"[CODEMAP] Wrote: {codemap_path}")

    # --- Lightweight verification ---
    _run_verification(
        codemap_path=codemap_path,
        codespace=codespace,
        artifacts_dir=artifacts_dir,
        scan_log_dir=scan_log_dir,
        model_policy=model_policy,
    )

    # Store fingerprint
    fp = compute_codespace_fingerprint(codespace)
    fingerprint_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint_path.write_text(fp)
    print(f"[CODEMAP] Stored codespace fingerprint: {fingerprint_path}")

    return True


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _run_freshness_check(
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
    (artifacts_dir / "signals").mkdir(parents=True, exist_ok=True)

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
    corrections_path = artifacts_dir / "signals" / "codemap-corrections.json"
    corrections_ref = ""
    if corrections_path.is_file():
        corrections_ref = (
            f"\n3. Codemap corrections (authoritative fixes): "
            f"`{corrections_path}`"
        )

    prompt = _load_template("codemap_freshness.md").format(
        codemap_path=codemap_path,
        codespace=codespace,
        change_description=change_desc,
        freshness_signal=freshness_signal,
        corrections_ref=corrections_ref,
    )
    freshness_prompt.write_text(prompt)

    result = dispatch_agent(
        model=model_policy["codemap_freshness"],
        project=codespace,
        prompt_file=freshness_prompt,
        agent_file="scan-codemap-freshness-judge.md",
        stdout_file=freshness_output,
    )

    if result.returncode == 0 and freshness_signal.is_file():
        try:
            data = json.loads(freshness_signal.read_text())
            if not isinstance(data, dict):
                print(
                    f"[CODEMAP][WARN] Freshness signal at "
                    f"{freshness_signal} is not a JSON object "
                    f"— renaming to .malformed.json")
                try:
                    freshness_signal.rename(
                        freshness_signal.with_suffix(".malformed.json"))
                except OSError:
                    pass
                rebuild = True
            else:
                rebuild = data.get("rebuild", True)
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[CODEMAP][WARN] Malformed freshness signal at "
                f"{freshness_signal} ({exc}) "
                f"— renaming to .malformed.json")
            try:
                freshness_signal.rename(
                    freshness_signal.with_suffix(".malformed.json"))
            except OSError:
                pass
            rebuild = True

        if str(rebuild).lower() == "false" or rebuild is False:
            print("[CODEMAP] Verifier says codemap still valid — reusing")
            fingerprint_path.write_text(current_fp)
            return True

        print("[CODEMAP] Verifier says rebuild needed — rebuilding codemap")
        return False

    if result.returncode == 0:
        print(
            "[CODEMAP] Verifier did not produce signal — "
            "rebuilding to be safe",
        )
    else:
        print("[CODEMAP] Freshness check failed — rebuilding codemap")

    return False


def _run_verification(
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
    corrections_signal = artifacts_dir / "signals" / "codemap-corrections.json"

    prompt = _load_template("codemap_verify.md").format(
        codemap_path=codemap_path,
        corrections_signal=corrections_signal,
    )
    verifier_prompt.write_text(prompt)

    result = dispatch_agent(
        model=model_policy.get("validation", "glm"),
        project=codespace,
        prompt_file=verifier_prompt,
        agent_file="scan-codemap-verifier.md",
        stdout_file=verifier_output,
    )

    if result.returncode == 0:
        print(f"[CODEMAP] Verification complete (see {verifier_output})")
    else:
        print("[CODEMAP] Verification failed — codemap used as-is")


def _has_content(path: Path) -> bool:
    """Return True if file exists and has non-whitespace content."""
    try:
        return bool(path.read_text().strip())
    except OSError:
        return False


def _log_phase_failure(
    scan_log_dir: Path,
    phase: str,
    context: str,
    message: str,
) -> None:
    """Append structured failure to failures.log and print to stderr."""
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
