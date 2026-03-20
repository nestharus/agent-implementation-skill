"""Component tests for substrate dispatch wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.scan.substrate.substrate_dispatcher import SubstrateDispatcher


def _make_dispatcher() -> SubstrateDispatcher:
    return SubstrateDispatcher()


def test_dispatch_substrate_agent_requires_task_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _make_dispatcher().dispatch_substrate_agent(
            model="model",
            prompt_path=tmp_path / "prompt.md",
            output_path=tmp_path / "output.txt",
            task_type="",
        )


def test_dispatch_substrate_agent_delegates_to_scan_dispatcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    output_path = tmp_path / "output.txt"
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    seen: dict[str, object] = {}

    def fake_dispatch_agent(**kwargs):
        seen.update(kwargs)
        kwargs["stdout_file"].write_text("stdout\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["scan.substrate_shard"],
            returncode=0,
            stdout="stdout\n",
            stderr="",
        )

    monkeypatch.setattr(
        "src.scan.substrate.substrate_dispatcher.dispatch_agent",
        fake_dispatch_agent,
    )

    ok = _make_dispatcher().dispatch_substrate_agent(
        model="gpt-high",
        prompt_path=prompt_path,
        output_path=output_path,
        codespace=codespace,
        task_type="scan.substrate_shard",
        concern_scope="section-03",
    )

    assert ok is True
    assert output_path.read_text(encoding="utf-8") == "stdout\n"
    assert seen == {
        "task_type": "scan.substrate_shard",
        "model": "gpt-high",
        "project": codespace,
        "prompt_file": prompt_path,
        "stdout_file": output_path,
        "concern_scope": "section-03",
        "submitted_by": "scan.substrate_shard.sync",
    }


def test_dispatch_substrate_agent_returns_false_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    output_path = tmp_path / "output.txt"

    def fake_dispatch_agent(**kwargs):
        kwargs["stdout_file"].write_text("failed\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["scan.substrate_shard"],
            returncode=1,
            stdout="",
            stderr="boom",
        )

    monkeypatch.setattr(
        "src.scan.substrate.substrate_dispatcher.dispatch_agent",
        fake_dispatch_agent,
    )

    ok = _make_dispatcher().dispatch_substrate_agent(
        model="gpt-high",
        prompt_path=prompt_path,
        output_path=output_path,
        task_type="scan.substrate_shard",
    )

    assert ok is False
    assert output_path.read_text(encoding="utf-8") == "failed\n"
