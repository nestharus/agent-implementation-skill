"""Component tests for shared scan dispatch helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scripts.lib.scan.scan_dispatch import (
    DEFAULT_SCAN_MODELS,
    build_scan_dispatch_command,
    read_scan_model_policy,
    resolve_scan_agent_path,
)


def test_read_scan_model_policy_uses_defaults_and_overrides(tmp_path) -> None:
    policy_path = tmp_path / "model-policy.json"
    policy_path.write_text(
        json.dumps({"scan": {"tier_ranking": "custom-model"}}),
        encoding="utf-8",
    )

    policy = read_scan_model_policy(tmp_path)

    assert policy["tier_ranking"] == "custom-model"
    assert policy["codemap_build"] == DEFAULT_SCAN_MODELS["codemap_build"]


def test_read_scan_model_policy_renames_malformed_json(tmp_path) -> None:
    policy_path = tmp_path / "model-policy.json"
    policy_path.write_text("{bad json", encoding="utf-8")

    policy = read_scan_model_policy(tmp_path)

    assert policy == DEFAULT_SCAN_MODELS
    assert not policy_path.exists()
    assert policy_path.with_suffix(".malformed.json").exists()


def test_resolve_scan_agent_path_validates_presence(tmp_path) -> None:
    workflow_home = tmp_path
    agent_path = workflow_home / "agents" / "scan-test.md"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text("prompt", encoding="utf-8")

    assert resolve_scan_agent_path(workflow_home, "scan-test.md") == agent_path

    with pytest.raises(FileNotFoundError):
        resolve_scan_agent_path(workflow_home, "missing.md")


def test_build_scan_dispatch_command_matches_agents_invocation(tmp_path) -> None:
    command = build_scan_dispatch_command(
        model="glm",
        project=tmp_path / "codespace",
        prompt_file=tmp_path / "prompt.md",
        agent_path=tmp_path / "agents" / "scan.md",
    )

    assert command == [
        "agents",
        "--model", "glm",
        "--project", str(tmp_path / "codespace"),
        "--file", str(tmp_path / "prompt.md"),
        "--agent-file", str(tmp_path / "agents" / "scan.md"),
    ]
