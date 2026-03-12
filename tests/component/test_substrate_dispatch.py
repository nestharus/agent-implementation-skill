"""Component tests for substrate dispatch wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.scan.substrate import dispatch
from src.taskrouter.agents import resolve_agent_path
from containers import TaskRouterService


def test_dispatch_substrate_agent_requires_agent_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        dispatch.dispatch_substrate_agent(
            model="model",
            prompt_path=tmp_path / "prompt.md",
            output_path=tmp_path / "output.txt",
            agent_file="",
        )


def test_dispatch_substrate_agent_validates_agent_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: (_ for _ in ()).throw(FileNotFoundError(name)),
    )

    with pytest.raises(FileNotFoundError):
        dispatch.dispatch_substrate_agent(
            model="model",
            prompt_path=tmp_path / "prompt.md",
            output_path=tmp_path / "output.txt",
            agent_file="missing.md",
        )


def test_dispatch_substrate_agent_writes_combined_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = resolve_agent_path("substrate-shard-explorer.md")
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: resolved_path,
    )
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    output_path = tmp_path / "logs" / "output.txt"
    codespace = tmp_path / "codespace"
    codespace.mkdir()

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 600
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="stdout\n",
            stderr="stderr\n",
        )

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    ok = dispatch.dispatch_substrate_agent(
        model="gpt-high",
        prompt_path=prompt_path,
        output_path=output_path,
        codespace=codespace,
        agent_file="substrate-shard-explorer.md",
    )

    assert ok is True
    assert output_path.read_text(encoding="utf-8") == "stdout\nstderr\n"
    assert calls == [[
        "agents",
        "--model", "gpt-high",
        "--file", str(prompt_path),
        "--agent-file", str(resolved_path),
        "--project", str(codespace),
    ]]


def test_dispatch_substrate_agent_handles_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_path = resolve_agent_path("substrate-shard-explorer.md")
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: resolved_path,
    )
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    output_path = tmp_path / "output.txt"

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="agents", timeout=600)

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    ok = dispatch.dispatch_substrate_agent(
        model="gpt-high",
        prompt_path=prompt_path,
        output_path=output_path,
        agent_file="substrate-shard-explorer.md",
    )

    assert ok is False
    assert "TIMEOUT: Agent exceeded 600s time limit" in output_path.read_text(
        encoding="utf-8",
    )
