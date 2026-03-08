"""AgentExecutor: raw subprocess invocation for the ``agents`` binary."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

WORKFLOW_HOME = Path(
    os.environ.get(
        "WORKFLOW_HOME",
        Path(__file__).resolve().parent.parent.parent.parent,
    )
)


@dataclass
class AgentResult:
    output: str
    returncode: int
    timed_out: bool


def run_agent(
    model: str,
    prompt_path: Path,
    output_path: Path,
    *,
    agent_file: str,
    codespace: Path | None = None,
    timeout: int = 600,
) -> AgentResult:
    """Run the ``agents`` binary and return the raw process result."""
    del output_path

    if not agent_file:
        raise ValueError(
            "agent_file is required — every dispatch must have "
            "behavioral constraints"
        )

    agent_path = WORKFLOW_HOME / "agents" / agent_file
    if not agent_path.exists():
        raise FileNotFoundError(f"Agent file not found: {agent_path}")

    cmd = [
        "agents",
        "--model",
        model,
        "--file",
        str(prompt_path),
        "--agent-file",
        str(agent_path),
    ]
    if codespace:
        cmd.extend(["--project", str(codespace)])

    # Strip CLAUDECODE so nested agents sessions can launch
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return AgentResult(
            output=result.stdout + result.stderr,
            returncode=result.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return AgentResult(
            output=f"TIMEOUT: Agent exceeded {timeout}s time limit",
            returncode=-1,
            timed_out=True,
        )
