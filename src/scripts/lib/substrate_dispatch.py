"""Substrate-specific agent dispatch wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

# lib -> scripts -> src
WORKFLOW_HOME = Path(__file__).resolve().parent.parent.parent


def dispatch_substrate_agent(
    model: str,
    prompt_path: Path,
    output_path: Path,
    codespace: Path | None = None,
    *,
    agent_file: str,
) -> bool:
    """Run an agent via the ``agents`` binary and capture output."""
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
        "--model", model,
        "--file", str(prompt_path),
        "--agent-file", str(agent_path),
    ]
    if codespace:
        cmd.extend(["--project", str(codespace)])

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output_path.write_text(
            result.stdout + result.stderr,
            encoding="utf-8",
        )
        if result.returncode != 0:
            print(
                f"[SUBSTRATE][WARN] Agent returned "
                f"{result.returncode} for {prompt_path.name}"
            )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        output_path.write_text(
            "TIMEOUT: Agent exceeded 600s time limit\n",
            encoding="utf-8",
        )
        print(
            f"[SUBSTRATE][WARN] Agent timed out for {prompt_path.name}"
        )
        return False
