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

import subprocess
from pathlib import Path


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
