"""Thin subprocess wrapper for ``agents`` binary dispatch.

This is intentionally separate from ``dispatch.section_dispatch``.
Stage 3 scan is a different execution stage with simpler needs:
no monitoring, no pause/resume, no mailbox integration.  Keeping
a thin boundary here avoids coupling scan to the section-loop
orchestration layer.

For testing, mock ``scan.dispatch.dispatch_agent`` the same way
``dispatch.section_dispatch.dispatch_agent`` is mocked — both are the
single LLM boundary for their respective stages.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scan.service.scan_dispatch import (
    DEFAULT_SCAN_MODELS,
    build_scan_dispatch_command,
    read_scan_model_policy,
    resolve_scan_agent_path,
)

_DEFAULT_MODELS = DEFAULT_SCAN_MODELS


def dispatch_agent(
    *,
    model: str,
    project: Path,
    prompt_file: Path,
    agent_file: str,
    stdout_file: Path | None = None,
    stderr_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Dispatch an agent via the ``agents`` binary.

    Parameters
    ----------
    model:
        Model name (e.g. ``"claude-opus"``, ``"glm"``).
    project:
        ``--project`` directory (typically the codespace).
    prompt_file:
        ``--file`` path containing the agent prompt.
    agent_file:
        REQUIRED basename of the agent definition file (e.g.
        ``"scan-codemap-builder.md"``).  Every dispatch must have
        behavioral constraints.  Resolved via
        ``taskrouter.agents.resolve_agent_path()``.
    stdout_file:
        If given, stdout is written to this path.
    stderr_file:
        If given, stderr is written to this path.

    Returns
    -------
    subprocess.CompletedProcess
        The finished process.  Caller decides how to handle non-zero rc.
    """
    if not agent_file:
        raise ValueError(
            "agent_file is required — every dispatch must have "
            "behavioral constraints"
        )
    agent_path = resolve_scan_agent_path(Path(), agent_file)
    cmd = build_scan_dispatch_command(
        model=model,
        project=project,
        prompt_file=prompt_file,
        agent_path=agent_path,
    )

    # Strip CLAUDECODE to prevent nested-session detection when running
    # inside Claude Code or another agents session.
    import os
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)  # noqa: S603

    if stdout_file is not None:
        stdout_file.parent.mkdir(parents=True, exist_ok=True)
        stdout_file.write_text(result.stdout)

    if stderr_file is not None:
        stderr_file.parent.mkdir(parents=True, exist_ok=True)
        stderr_file.write_text(result.stderr)

    return result
