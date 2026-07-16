"""AgentExecutor: raw subprocess invocation for the ``agents`` binary."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import TaskRouterService

_DEFAULT_AGENT_TIMEOUT_SECONDS = 600
_PROCESS_GROUP_TERMINATION_GRACE_SECONDS = 1.0
_PROCESS_GROUP_KILL_SETTLE_SECONDS = 0.05


@dataclass
class AgentResult:
    output: str
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool


class AgentExecutor:
    """Wraps subprocess invocation for the ``agents`` binary."""

    def __init__(self, task_router: TaskRouterService) -> None:
        self._task_router = task_router

    def run_agent(
        self,
        model: str,
        prompt_path: Path,
        *,
        agent_file: str,
        codespace: Path | None = None,
        timeout: int = _DEFAULT_AGENT_TIMEOUT_SECONDS,
    ) -> AgentResult:
        """Run the ``agents`` binary and return the raw process result."""

        if not agent_file:
            raise ValueError(
                "agent_file is required — every dispatch must have "
                "behavioral constraints"
            )

        agent_path = self._task_router.resolve_agent_path(agent_file)

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

        process = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        process_group_id = process.pid

        def reap_direct_child_after_group_failure() -> BaseException | None:
            """Best-effort direct cleanup when group closure is uncertain."""

            direct_cleanup_error: BaseException | None = None
            direct_child_stopped = False
            try:
                process.kill()
                direct_child_stopped = True
            except ProcessLookupError:
                direct_child_stopped = True
            except BaseException as exc:
                direct_cleanup_error = exc

            for stream in (process.stdout, process.stderr):
                if stream is None:
                    continue
                try:
                    stream.close()
                except BaseException as exc:
                    if direct_cleanup_error is None:
                        direct_cleanup_error = exc

            if direct_child_stopped:
                try:
                    process.wait()
                except BaseException as exc:
                    if direct_cleanup_error is None:
                        direct_cleanup_error = exc
            return direct_cleanup_error

        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return AgentResult(
                output=stdout + stderr,
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            cleanup_error: BaseException | None = None
            cleanup_phase = ""
            group_closed = False

            try:
                os.killpg(process_group_id, signal.SIGTERM)
            except ProcessLookupError:
                group_closed = True
            except BaseException as exc:
                cleanup_error = exc
                cleanup_phase = "SIGTERM"
            else:
                try:
                    time.sleep(_PROCESS_GROUP_TERMINATION_GRACE_SECONDS)
                except BaseException as exc:
                    cleanup_error = exc
                    cleanup_phase = "termination grace"

            if not group_closed:
                forced_signal_error: BaseException | None = None
                try:
                    os.killpg(process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    group_closed = True
                except BaseException as exc:
                    forced_signal_error = exc
                    if cleanup_error is None:
                        cleanup_error = exc
                        cleanup_phase = "SIGKILL"
                else:
                    group_closed = True
                    try:
                        time.sleep(_PROCESS_GROUP_KILL_SETTLE_SECONDS)
                    except BaseException as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
                            cleanup_phase = "SIGKILL settlement"

                if forced_signal_error is not None:
                    direct_cleanup_error = reap_direct_child_after_group_failure()
                    detail = f" during {cleanup_phase}" if cleanup_phase else ""
                    if cleanup_error is not forced_signal_error:
                        detail += "; SIGKILL also failed"
                    if direct_cleanup_error is not None:
                        detail += "; direct-child cleanup also failed"
                    raise RuntimeError(
                        "Failed to clean up agent process group "
                        f"{process_group_id}{detail}"
                    ) from cleanup_error

            process.communicate()
            if cleanup_error is not None:
                if isinstance(cleanup_error, OSError):
                    raise RuntimeError(
                        "Failed to clean up agent process group "
                        f"{process_group_id} during {cleanup_phase}"
                    ) from cleanup_error
                raise cleanup_error

            return AgentResult(
                output=f"TIMEOUT: Agent exceeded {timeout}s time limit",
                stdout="",
                stderr="",
                returncode=-1,
                timed_out=True,
            )
        except BaseException:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                process.communicate()
            except BaseException as cleanup_error:
                direct_cleanup_error = reap_direct_child_after_group_failure()
                detail = " during SIGKILL"
                if direct_cleanup_error is not None:
                    detail += "; direct-child cleanup also failed"
                raise RuntimeError(
                    "Failed to clean up agent process group "
                    f"{process_group_id}{detail}"
                ) from cleanup_error
            else:
                process.communicate()
            raise
