"""Thin subprocess wrapper for ``uv run --frozen agents ...`` dispatch.

This is intentionally separate from ``section_loop.dispatch``.
Stage 3 scan is a different execution stage with simpler needs:
no monitoring, no pause/resume, no mailbox integration.  Keeping
a thin boundary here avoids coupling scan to the section-loop
orchestration layer.

For testing, mock ``scan.dispatch.dispatch_agent`` the same way
``section_loop.dispatch.dispatch_agent`` is mocked — both are the
single LLM boundary for their respective stages.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Default model assignments per scan task.
_DEFAULT_MODELS: dict[str, str] = {
    "codemap_build": "claude-opus",
    "codemap_freshness": "claude-opus",
    "exploration": "claude-opus",
    "validation": "claude-opus",
    "tier_ranking": "glm",
    "deep_analysis": "glm",
    "feedback_updater": "glm",
}


def read_scan_model_policy(artifacts_dir: Path) -> dict[str, str]:
    """Read scan-stage model policy from ``model-policy.json``.

    Looks for a ``"scan"`` key inside the policy file. Falls back to
    defaults when the file is missing, malformed, or has no scan key.

    Returns a dict mapping task name → model string.
    """
    policy = dict(_DEFAULT_MODELS)
    policy_path = artifacts_dir / "model-policy.json"
    if policy_path.is_file():
        try:
            data = json.loads(policy_path.read_text())
            scan_overrides = data.get("scan", {})
            if isinstance(scan_overrides, dict):
                for key, val in scan_overrides.items():
                    if key in policy and isinstance(val, str):
                        policy[key] = val
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[SCAN] WARNING: model-policy.json exists but is "
                f"invalid ({exc}) — renaming to .malformed.json",
            )
            try:
                policy_path.rename(
                    policy_path.with_suffix(".malformed.json"))
            except OSError:
                pass
    return policy


def dispatch_agent(
    *,
    model: str,
    project: Path,
    prompt_file: Path,
    stdout_file: Path | None = None,
    stderr_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Dispatch an agent via ``uv run --frozen agents``.

    Parameters
    ----------
    model:
        Model name (e.g. ``"claude-opus"``, ``"glm"``).
    project:
        ``--project`` directory (typically the codespace).
    prompt_file:
        ``--file`` path containing the agent prompt.
    stdout_file:
        If given, stdout is written to this path.
    stderr_file:
        If given, stderr is written to this path.

    Returns
    -------
    subprocess.CompletedProcess
        The finished process.  Caller decides how to handle non-zero rc.
    """
    cmd = [
        "uv", "run", "--frozen", "agents",
        "--model", model,
        "--project", str(project),
        "--file", str(prompt_file),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603

    if stdout_file is not None:
        stdout_file.parent.mkdir(parents=True, exist_ok=True)
        stdout_file.write_text(result.stdout)

    if stderr_file is not None:
        stderr_file.parent.mkdir(parents=True, exist_ok=True)
        stderr_file.write_text(result.stderr)

    return result
