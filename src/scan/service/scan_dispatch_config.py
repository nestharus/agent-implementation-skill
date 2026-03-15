"""Helpers for scan-stage dispatch configuration and routing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, TaskRouterService

DEFAULT_SCAN_MODELS: dict[str, str] = {
    "codemap_build": "claude-opus",
    "codemap_freshness": "glm",
    "exploration": "claude-opus",
    "validation": "glm",
    "tier_ranking": "glm",
    "deep_analysis": "glm",
    "feedback_updater": "glm",
}


class ScanDispatchConfig:
    """Scan-stage dispatch configuration and routing.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._task_router = task_router

    def read_scan_model_policy(self, artifacts_dir: Path) -> dict[str, str]:
        """Read scan-stage model policy from ``model-policy.json``."""
        policy = dict(DEFAULT_SCAN_MODELS)
        policy_path = PathRegistry(artifacts_dir.parent).model_policy()
        data = self._artifact_io.read_json(policy_path)
        if isinstance(data, dict):
            scan_overrides = data.get("scan", {})
            if isinstance(scan_overrides, dict):
                for key, val in scan_overrides.items():
                    if key in policy and isinstance(val, str):
                        policy[key] = val
        return policy

    def resolve_scan_agent_path(self, agent_file: str) -> Path:
        """Resolve a scan agent definition path."""
        return self._task_router.resolve_agent_path(agent_file)


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


