"""Component tests for shared scan dispatch helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from containers import Services
from src.orchestrator.path_registry import PathRegistry
from src.scan.scan_dispatcher import dispatch_agent
from src.scan.service.scan_dispatch_config import (
    DEFAULT_SCAN_MODELS,
    ScanDispatchConfig,
    build_scan_dispatch_command,
)


def _make_config():
    return ScanDispatchConfig(
        artifact_io=Services.artifact_io(),
        task_router=Services.task_router(),
    )


def test_read_scan_model_policy_uses_defaults_and_overrides(tmp_path) -> None:
    """Uses runtime layout: artifacts_dir = planspace / 'artifacts'."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts_dir = planspace / "artifacts"
    policy_path = artifacts_dir / "model-policy.json"
    policy_path.write_text(
        json.dumps({"scan": {"tier_ranking": "custom-model"}}),
        encoding="utf-8",
    )

    policy = _make_config().read_scan_model_policy(artifacts_dir)

    assert policy["tier_ranking"] == "custom-model"
    assert policy["codemap_build"] == DEFAULT_SCAN_MODELS["codemap_build"]


def test_read_scan_model_policy_renames_malformed_json(tmp_path) -> None:
    """Uses runtime layout: artifacts_dir = planspace / 'artifacts'."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts_dir = planspace / "artifacts"
    policy_path = artifacts_dir / "model-policy.json"
    policy_path.write_text("{bad json", encoding="utf-8")

    policy = _make_config().read_scan_model_policy(artifacts_dir)

    assert policy == DEFAULT_SCAN_MODELS
    assert not policy_path.exists()
    assert policy_path.with_suffix(".malformed.json").exists()


def test_resolve_scan_agent_path_validates_presence(tmp_path, monkeypatch) -> None:
    agent_path = tmp_path / "scan" / "agents" / "scan-test.md"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text("prompt", encoding="utf-8")

    from containers import TaskRouterService
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: agent_path if name == "scan-test.md" else (_ for _ in ()).throw(FileNotFoundError(name)),
    )

    config = _make_config()
    assert config.resolve_scan_agent_path("scan-test.md") == agent_path

    with pytest.raises(FileNotFoundError):
        config.resolve_scan_agent_path("missing.md")


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


def test_default_scan_models_match_agent_frontmatter() -> None:
    """Scan defaults for codemap_freshness and validation must match agent files (glm)."""
    assert DEFAULT_SCAN_MODELS["codemap_freshness"] == "glm"
    assert DEFAULT_SCAN_MODELS["validation"] == "glm"


def test_dispatch_agent_routes_through_queue_and_copies_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    registry = PathRegistry(planspace)
    registry.ensure_artifacts_tree()

    prompt_path = registry.scan_logs_dir() / "prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("prompt", encoding="utf-8")
    stdout_file = registry.scan_logs_dir() / "stdout.md"
    stderr_file = registry.scan_logs_dir() / "stderr.log"
    task_output = registry.artifacts / "task-1-output.md"

    class _Submitter:
        def submit_chain(self, env, steps, **_kwargs):
            assert env.db_path == registry.run_db()
            assert steps[0].task_type == "scan.codemap_build"
            assert steps[0].payload_path == str(prompt_path.resolve())
            return [1]

    states = iter([
        {
            "id": "1",
            "type": "scan.codemap_build",
            "by": "scan.sync_dispatch",
            "payload": str(prompt_path.resolve()),
            "status": "pending",
        },
        {
            "id": "1",
            "type": "scan.codemap_build",
            "status": "complete",
            "output": str(task_output),
        },
    ])

    monkeypatch.setattr("src.scan.scan_dispatcher._get_submitter", lambda: _Submitter())
    monkeypatch.setattr("src.scan.scan_dispatcher.get_task", lambda *_args: next(states))

    def fake_dispatch_task(db_path, run_planspace, task, *, codespace, model_policy):
        assert db_path == str(registry.run_db())
        assert run_planspace == planspace
        assert task["id"] == "1"
        assert codespace == tmp_path / "codespace"
        assert model_policy == {"scan": {"codemap_build": "glm"}}
        task_output.write_text("combined\n", encoding="utf-8")
        task_output.with_suffix(".stdout.txt").write_text("stdout\n", encoding="utf-8")
        task_output.with_suffix(".stderr.txt").write_text("stderr\n", encoding="utf-8")

    monkeypatch.setattr("flow.engine.task_dispatcher.dispatch_task", fake_dispatch_task)

    result = dispatch_agent(
        task_type="scan.codemap_build",
        model="glm",
        project=tmp_path / "codespace",
        prompt_file=prompt_path,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
    )

    assert result.args == ["scan.codemap_build"]
    assert result.returncode == 0
    assert result.stdout == "stdout\n"
    assert result.stderr == "stderr\n"
    assert stdout_file.read_text(encoding="utf-8") == "stdout\n"
    assert stderr_file.read_text(encoding="utf-8") == "stderr\n"
