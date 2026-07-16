from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.dispatch.engine import agent_executor
from src.dispatch.engine.agent_executor import AgentExecutor
from containers import TaskRouterService


class _FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 43210,
        returncode: int = 3,
        stdout: str = "out",
        stderr: str = "err",
        timeout_on_first_communicate: bool = False,
        communication_error: BaseException | None = None,
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self._captured_stdout = stdout
        self._captured_stderr = stderr
        self._timeout_on_first_communicate = timeout_on_first_communicate
        self._communication_error = communication_error
        self.communicate_calls: list[tuple[object | None, int | None]] = []
        self.direct_killed = False
        self.wait_calls = 0
        self.reaped = False

    def communicate(
        self,
        input: object | None = None,
        timeout: int | None = None,
    ) -> tuple[str, str]:
        self.communicate_calls.append((input, timeout))
        if len(self.communicate_calls) == 1:
            if self._timeout_on_first_communicate:
                raise subprocess.TimeoutExpired(
                    cmd="agents",
                    timeout=timeout if timeout is not None else 0,
                )
            if self._communication_error is not None:
                raise self._communication_error
        self.reaped = True
        return self._captured_stdout, self._captured_stderr

    def kill(self) -> None:
        self.direct_killed = True

    def wait(self) -> int:
        self.wait_calls += 1
        self.reaped = True
        return self.returncode


def test_run_agent_invokes_agents_binary_with_expected_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_dir = tmp_path / "scan" / "agents"
    agent_dir.mkdir(parents=True)
    agent_path = agent_dir / "test-agent.md"
    agent_path.write_text("# test\n", encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    process = _FakeProcess()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(cmd: list[str], **kwargs: object) -> _FakeProcess:
        calls.append((cmd, kwargs))
        return process

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: agent_path,
    )
    monkeypatch.setenv("AGENT_EXECUTOR_SENTINEL", "preserved")
    monkeypatch.setenv("CLAUDECODE", "removed")
    monkeypatch.setattr(agent_executor.subprocess, "Popen", fake_popen)

    executor = AgentExecutor(task_router=TaskRouterService())
    result = executor.run_agent(
        "test-model",
        prompt_path,
        agent_file="test-agent.md",
        codespace=codespace,
        timeout=123,
    )

    assert result.output == "outerr"
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.returncode == 3
    assert result.timed_out is False
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd == [
        "agents",
        "--model",
        "test-model",
        "--file",
        str(prompt_path),
        "--agent-file",
        str(agent_path),
        "--project",
        str(codespace),
    ]
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["AGENT_EXECUTOR_SENTINEL"] == "preserved"
    assert "CLAUDECODE" not in env
    assert "timeout" not in kwargs
    assert process.communicate_calls == [(None, 123)]


def test_run_agent_returns_timeout_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_dir = tmp_path / "scan" / "agents"
    agent_dir.mkdir(parents=True)
    agent_path = agent_dir / "test-agent.md"
    agent_path.write_text("# test\n", encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    process = _FakeProcess(timeout_on_first_communicate=True)
    signal_calls: list[tuple[int, signal.Signals]] = []
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: agent_path,
    )
    monkeypatch.setattr(
        agent_executor.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        agent_executor.os,
        "killpg",
        lambda pgid, sig: signal_calls.append((pgid, sig)),
    )
    monkeypatch.setattr(agent_executor.time, "sleep", sleep_calls.append)

    executor = AgentExecutor(task_router=TaskRouterService())
    result = executor.run_agent(
        "test-model",
        prompt_path,
        agent_file="test-agent.md",
        timeout=45,
    )

    assert result.timed_out is True
    assert result.returncode == -1
    assert result.output == "TIMEOUT: Agent exceeded 45s time limit"
    assert result.stdout == ""
    assert result.stderr == ""
    assert signal_calls == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert sleep_calls == [1.0, 0.05]
    assert process.communicate_calls == [(None, 45), (None, None)]
    assert process.reaped is True


def test_timeout_accepts_group_gone_before_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_path = tmp_path / "test-agent.md"
    prompt_path = tmp_path / "prompt.md"
    process = _FakeProcess(timeout_on_first_communicate=True)
    signal_calls: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )
    monkeypatch.setattr(
        agent_executor.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )

    def group_is_gone(pgid: int, sig: signal.Signals) -> None:
        signal_calls.append((pgid, sig))
        raise ProcessLookupError

    monkeypatch.setattr(agent_executor.os, "killpg", group_is_gone)
    monkeypatch.setattr(
        agent_executor.time,
        "sleep",
        lambda seconds: pytest.fail("grace must be skipped after group disappearance"),
    )

    result = AgentExecutor(task_router=TaskRouterService()).run_agent(
        "test-model",
        prompt_path,
        agent_file="test-agent.md",
        timeout=45,
    )

    assert result.timed_out is True
    assert result.returncode == -1
    assert result.stdout == ""
    assert result.stderr == ""
    assert signal_calls == [(process.pid, signal.SIGTERM)]
    assert process.communicate_calls == [(None, 45), (None, None)]
    assert process.reaped is True


def test_timeout_accepts_group_gone_before_sigkill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_path = tmp_path / "test-agent.md"
    prompt_path = tmp_path / "prompt.md"
    process = _FakeProcess(timeout_on_first_communicate=True)
    signal_calls: list[tuple[int, signal.Signals]] = []
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )
    monkeypatch.setattr(
        agent_executor.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )

    def group_exits_during_grace(pgid: int, sig: signal.Signals) -> None:
        signal_calls.append((pgid, sig))
        if sig == signal.SIGKILL:
            raise ProcessLookupError

    monkeypatch.setattr(agent_executor.os, "killpg", group_exits_during_grace)
    monkeypatch.setattr(agent_executor.time, "sleep", sleep_calls.append)

    result = AgentExecutor(task_router=TaskRouterService()).run_agent(
        "test-model",
        prompt_path,
        agent_file="test-agent.md",
        timeout=45,
    )

    assert result.timed_out is True
    assert signal_calls == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert sleep_calls == [1.0]
    assert process.communicate_calls == [(None, 45), (None, None)]
    assert process.reaped is True


def test_timeout_signals_group_after_leader_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_path = tmp_path / "test-agent.md"
    prompt_path = tmp_path / "prompt.md"
    process = _FakeProcess(
        returncode=0,
        timeout_on_first_communicate=True,
    )
    signal_calls: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )
    monkeypatch.setattr(
        agent_executor.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        agent_executor.os,
        "killpg",
        lambda pgid, sig: signal_calls.append((pgid, sig)),
    )
    monkeypatch.setattr(agent_executor.time, "sleep", lambda seconds: None)

    result = AgentExecutor(task_router=TaskRouterService()).run_agent(
        "test-model",
        prompt_path,
        agent_file="test-agent.md",
        timeout=45,
    )

    assert result.timed_out is True
    assert signal_calls == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.reaped is True


def test_timeout_cleanup_failure_is_not_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_path = tmp_path / "test-agent.md"
    prompt_path = tmp_path / "prompt.md"
    process = _FakeProcess(timeout_on_first_communicate=True)
    signal_calls: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )
    monkeypatch.setattr(
        agent_executor.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )

    def cleanup_is_denied(pgid: int, sig: signal.Signals) -> None:
        signal_calls.append((pgid, sig))
        raise PermissionError("signal denied")

    monkeypatch.setattr(agent_executor.os, "killpg", cleanup_is_denied)
    monkeypatch.setattr(
        agent_executor.time,
        "sleep",
        lambda seconds: pytest.fail("grace must be skipped after SIGTERM failure"),
    )

    with pytest.raises(
        RuntimeError,
        match=rf"process group {process.pid} during SIGTERM; SIGKILL also failed",
    ) as exc_info:
        AgentExecutor(task_router=TaskRouterService()).run_agent(
            "test-model",
            prompt_path,
            agent_file="test-agent.md",
            timeout=45,
        )

    assert isinstance(exc_info.value.__cause__, PermissionError)
    assert signal_calls == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.direct_killed is True
    assert process.wait_calls == 1
    assert process.reaped is True
    assert process.stdout.closed is True
    assert process.stderr.closed is True


def test_non_timeout_communication_failure_cleans_group_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_path = tmp_path / "test-agent.md"
    prompt_path = tmp_path / "prompt.md"
    communication_error = ValueError("communication failed")
    process = _FakeProcess(communication_error=communication_error)
    signal_calls: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )
    monkeypatch.setattr(
        agent_executor.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        agent_executor.os,
        "killpg",
        lambda pgid, sig: signal_calls.append((pgid, sig)),
    )

    with pytest.raises(ValueError, match="communication failed") as exc_info:
        AgentExecutor(task_router=TaskRouterService()).run_agent(
            "test-model",
            prompt_path,
            agent_file="test-agent.md",
            timeout=45,
        )

    assert exc_info.value is communication_error
    assert signal_calls == [(process.pid, signal.SIGKILL)]
    assert process.communicate_calls == [(None, 45), (None, None)]
    assert process.reaped is True


def test_timeout_terminates_full_agent_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_dir = tmp_path / "scan" / "agents"
    agent_dir.mkdir(parents=True)
    agent_path = agent_dir / "test-agent.md"
    agent_path.write_text("# test\n", encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    launcher_identity_path = tmp_path / "launcher.identity"
    descendant_pid_path = tmp_path / "descendant.pid"
    descendant_ready_path = tmp_path / "descendant.ready"
    descendant_sigterm_path = tmp_path / "descendant.sigterm"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agents_path = bin_dir / "agents"
    agents_path.write_text(
        f"""#!{sys.executable}
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

Path(os.environ["FAKE_AGENTS_LAUNCHER_IDENTITY"]).write_text(
    f"{{os.getpid()}} {{os.getpgid(0)}} {{os.getsid(0)}}", encoding="utf-8"
)
descendant_code = '''
import os
import signal
import sys
import time
from pathlib import Path

def handle_sigterm(signum, frame):
    Path(sys.argv[3]).write_text("received", encoding="utf-8")

signal.signal(signal.SIGTERM, handle_sigterm)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
while True:
    time.sleep(60)
'''
subprocess.Popen(
    [
        sys.executable,
        "-c",
        descendant_code,
        os.environ["FAKE_AGENTS_DESCENDANT_PID"],
        os.environ["FAKE_AGENTS_DESCENDANT_READY"],
        os.environ["FAKE_AGENTS_DESCENDANT_SIGTERM"],
    ],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
ready_path = Path(os.environ["FAKE_AGENTS_DESCENDANT_READY"])
ready_deadline = time.monotonic() + 5
while not ready_path.exists() and time.monotonic() < ready_deadline:
    time.sleep(0.01)
while True:
    time.sleep(60)
""",
        encoding="utf-8",
    )
    agents_path.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(
        "FAKE_AGENTS_LAUNCHER_IDENTITY", str(launcher_identity_path)
    )
    monkeypatch.setenv("FAKE_AGENTS_DESCENDANT_PID", str(descendant_pid_path))
    monkeypatch.setenv("FAKE_AGENTS_DESCENDANT_READY", str(descendant_ready_path))
    monkeypatch.setenv(
        "FAKE_AGENTS_DESCENDANT_SIGTERM", str(descendant_sigterm_path)
    )
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )

    def pid_state(pid: int) -> str:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except FileNotFoundError:
            return "absent"
        return stat[stat.rfind(")") + 2]

    def published_pid(path: Path) -> int | None:
        try:
            return int(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def published_identity() -> tuple[int, int, int] | None:
        try:
            values = launcher_identity_path.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return None
        if len(values) != 3:
            return None
        return int(values[0]), int(values[1]), int(values[2])

    caller_pgid = os.getpgrp()
    launcher_pid: int | None = None
    launcher_pgid: int | None = None
    launcher_sid: int | None = None
    descendant_pid: int | None = None
    launcher_state_on_return = "not-published"
    descendant_state_on_return = "not-published"
    elapsed: float | None = None
    try:
        started = time.monotonic()
        result = AgentExecutor(task_router=TaskRouterService()).run_agent(
            "test-model",
            prompt_path,
            agent_file="test-agent.md",
            timeout=2,
        )
        elapsed = time.monotonic() - started

        identity = published_identity()
        assert identity is not None, "fake agents launcher did not publish identity"
        launcher_pid, launcher_pgid, launcher_sid = identity
        descendant_pid = published_pid(descendant_pid_path)
        assert descendant_pid is not None, "fake agents descendant did not publish its PID"

        launcher_state_on_return = pid_state(launcher_pid)
        descendant_state_on_return = pid_state(descendant_pid)
        assert descendant_ready_path.exists(), "descendant did not publish readiness"
        assert result.timed_out is True
        assert result.returncode == -1
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.output == "TIMEOUT: Agent exceeded 2s time limit"
        assert elapsed < 6
        assert launcher_pid == launcher_pgid == launcher_sid
        assert launcher_pgid != caller_pgid
        assert launcher_state_on_return == "absent", (
            f"launcher PID {launcher_pid} is still alive after run_agent returned "
            f"(state={launcher_state_on_return})"
        )
        assert descendant_state_on_return in {"absent", "Z"}, (
            f"durable descendant PID {descendant_pid} is still alive after "
            f"run_agent returned (state={descendant_state_on_return})"
        )
        assert descendant_sigterm_path.exists(), (
            "durable descendant did not record receipt of SIGTERM"
        )
    finally:
        identity = published_identity()
        if identity is not None:
            launcher_pid = launcher_pid or identity[0]
            launcher_pgid = launcher_pgid or identity[1]
            launcher_sid = launcher_sid or identity[2]
        descendant_pid = descendant_pid or published_pid(descendant_pid_path)
        print(
            "Return-boundary evidence: "
            f"launcher_pid={launcher_pid} "
            f"launcher_pgid={launcher_pgid} "
            f"launcher_sid={launcher_sid} "
            f"caller_pgid={caller_pgid} "
            f"launcher_state_on_return={launcher_state_on_return} "
            f"descendant_pid={descendant_pid} "
            f"descendant_state_on_return={descendant_state_on_return} "
            f"sigterm_received={descendant_sigterm_path.exists()} "
            f"elapsed={elapsed}"
        )
        for pid in (descendant_pid, launcher_pid):
            if pid is None or pid_state(pid) in {"absent", "Z"}:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            states = [
                pid_state(pid) if pid is not None else "not-published"
                for pid in (launcher_pid, descendant_pid)
            ]
            if all(state in {"absent", "Z", "not-published"} for state in states):
                break
            time.sleep(0.01)

        launcher_cleanup_state = (
            pid_state(launcher_pid) if launcher_pid is not None else "not-published"
        )
        descendant_cleanup_state = (
            pid_state(descendant_pid) if descendant_pid is not None else "not-published"
        )
        print(
            "Cleanup evidence: "
            f"launcher_state={launcher_cleanup_state} "
            f"descendant_state={descendant_cleanup_state}"
        )
        assert launcher_cleanup_state in {"absent", "Z", "not-published"}
        assert descendant_cleanup_state in {"absent", "Z", "not-published"}


def test_real_non_timeout_preserves_agent_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_dir = tmp_path / "scan" / "agents"
    agent_dir.mkdir(parents=True)
    agent_path = agent_dir / "test-agent.md"
    agent_path.write_text("# test\n", encoding="utf-8")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")
    codespace = tmp_path / "codespace"
    codespace.mkdir()
    argv_path = tmp_path / "argv.json"
    env_path = tmp_path / "env.json"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agents_path = bin_dir / "agents"
    agents_path.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_AGENTS_ARGV"]).write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8"
)
Path(os.environ["FAKE_AGENTS_ENV"]).write_text(
    json.dumps({{
        "sentinel": os.environ.get("AGENT_EXECUTOR_SENTINEL"),
        "claudecode_present": "CLAUDECODE" in os.environ,
    }}),
    encoding="utf-8",
)
sys.stdout.write("real stdout\\n")
sys.stderr.write("real stderr\\n")
raise SystemExit(3)
""",
        encoding="utf-8",
    )
    agents_path.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_AGENTS_ARGV", str(argv_path))
    monkeypatch.setenv("FAKE_AGENTS_ENV", str(env_path))
    monkeypatch.setenv("AGENT_EXECUTOR_SENTINEL", "preserved")
    monkeypatch.setenv("CLAUDECODE", "removed")
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path", lambda self, name: agent_path
    )

    result = AgentExecutor(task_router=TaskRouterService()).run_agent(
        "test-model",
        prompt_path,
        agent_file="test-agent.md",
        codespace=codespace,
        timeout=10,
    )

    assert json.loads(argv_path.read_text(encoding="utf-8")) == [
        "--model",
        "test-model",
        "--file",
        str(prompt_path),
        "--agent-file",
        str(agent_path),
        "--project",
        str(codespace),
    ]
    assert json.loads(env_path.read_text(encoding="utf-8")) == {
        "sentinel": "preserved",
        "claudecode_present": False,
    }
    assert result.stdout == "real stdout\n"
    assert result.stderr == "real stderr\n"
    assert result.output == "real stdout\nreal stderr\n"
    assert result.returncode == 3
    assert result.timed_out is False


def test_run_agent_requires_existing_agent_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TaskRouterService, "resolve_agent_path",
        lambda self, name: (_ for _ in ()).throw(FileNotFoundError(name)),
    )

    executor = AgentExecutor(task_router=TaskRouterService())
    with pytest.raises(FileNotFoundError):
        executor.run_agent(
            "test-model",
            tmp_path / "prompt.md",
            agent_file="missing-agent.md",
        )
