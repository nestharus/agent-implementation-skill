from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.dispatch import agent_executor


def test_run_agent_invokes_agents_binary_with_expected_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_home = tmp_path / "workflow"
    agent_path = workflow_home / "agents" / "test-agent.md"
    agent_path.parent.mkdir(parents=True)
    agent_path.write_text("# test\n", encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")
    output_path = tmp_path / "output.md"
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    calls: list[tuple[list[str], int]] = []

    def fake_run(cmd: list[str], **kwargs) -> SimpleNamespace:
        calls.append((cmd, kwargs["timeout"]))
        return SimpleNamespace(stdout="out", stderr="err", returncode=3)

    monkeypatch.setattr(agent_executor, "WORKFLOW_HOME", workflow_home)
    monkeypatch.setattr(agent_executor.subprocess, "run", fake_run)

    result = agent_executor.run_agent(
        "test-model",
        prompt_path,
        output_path,
        agent_file="test-agent.md",
        codespace=codespace,
        timeout=123,
    )

    assert result.output == "outerr"
    assert result.returncode == 3
    assert result.timed_out is False
    assert calls == [
        (
            [
                "agents",
                "--model",
                "test-model",
                "--file",
                str(prompt_path),
                "--agent-file",
                str(agent_path),
                "--project",
                str(codespace),
            ],
            123,
        )
    ]


def test_run_agent_returns_timeout_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_home = tmp_path / "workflow"
    agent_path = workflow_home / "agents" / "test-agent.md"
    agent_path.parent.mkdir(parents=True)
    agent_path.write_text("# test\n", encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="agents", timeout=45)

    monkeypatch.setattr(agent_executor, "WORKFLOW_HOME", workflow_home)
    monkeypatch.setattr(agent_executor.subprocess, "run", fake_run)

    result = agent_executor.run_agent(
        "test-model",
        prompt_path,
        tmp_path / "output.md",
        agent_file="test-agent.md",
        timeout=45,
    )

    assert result.timed_out is True
    assert result.returncode == -1
    assert result.output == "TIMEOUT: Agent exceeded 45s time limit"


def test_run_agent_requires_existing_agent_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_executor, "WORKFLOW_HOME", tmp_path / "workflow")

    with pytest.raises(FileNotFoundError):
        agent_executor.run_agent(
            "test-model",
            tmp_path / "prompt.md",
            tmp_path / "output.md",
            agent_file="missing-agent.md",
        )
