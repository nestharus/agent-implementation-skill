"""Helpers for scan-stage dispatch configuration and routing."""

from __future__ import annotations

import json
from pathlib import Path

from lib.core.artifact_io import rename_malformed

DEFAULT_SCAN_MODELS: dict[str, str] = {
    "codemap_build": "claude-opus",
    "codemap_freshness": "glm",
    "exploration": "claude-opus",
    "validation": "glm",
    "tier_ranking": "glm",
    "deep_analysis": "glm",
    "feedback_updater": "glm",
}


def read_scan_model_policy(artifacts_dir: Path) -> dict[str, str]:
    """Read scan-stage model policy from ``model-policy.json``."""
    policy = dict(DEFAULT_SCAN_MODELS)
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
                f"invalid ({exc}) - renaming to .malformed.json",
            )
            rename_malformed(policy_path)
    return policy


def resolve_scan_agent_path(workflow_home: Path, agent_file: str) -> Path:
    """Resolve a scan agent definition path from the workflow root."""
    if not agent_file:
        raise ValueError(
            "agent_file is required - every dispatch must have "
            "behavioral constraints"
        )
    agent_path = workflow_home / "agents" / agent_file
    if not agent_path.exists():
        raise FileNotFoundError(f"Agent file not found: {agent_path}")
    return agent_path


def build_scan_dispatch_command(
    *,
    model: str,
    project: Path,
    prompt_file: Path,
    agent_path: Path,
) -> list[str]:
    """Build the ``agents`` command used for scan dispatch."""
    return [
        "agents",
        "--model", model,
        "--project", str(project),
        "--file", str(prompt_file),
        "--agent-file", str(agent_path),
    ]
