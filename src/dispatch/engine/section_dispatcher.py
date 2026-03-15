"""Section dispatcher: orchestrates agent dispatch with monitoring and QA."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dispatch.engine.agent_executor import AgentExecutor
from dispatch.types import DispatchResult, DispatchStatus
from signals.service.database_client import DatabaseClient
from dispatch.repository.metadata import Metadata
from dispatch.service.monitor_service import MonitorService
from orchestrator.path_registry import PathRegistry

from pipeline.template import SRC_TEMPLATE_DIR, load_template, render, render_template

from dispatch.service.context_sidecar import ContextSidecar

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        ConfigService,
        LogService,
        PipelineControlService,
        PromptGuard,
        TaskRouterService,
    )

_SECTION_DISPATCH_TIMEOUT_SECONDS = 1800


class SectionDispatcher:
    """Orchestrates agent dispatch with monitoring and QA gates."""

    def __init__(
        self,
        config: ConfigService,
        pipeline_control: PipelineControlService,
        logger: LogService,
        communicator: Communicator,
        task_router: TaskRouterService,
        prompt_guard: PromptGuard,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._config = config
        self._pipeline_control = pipeline_control
        self._logger = logger
        self._communicator = communicator
        self._task_router = task_router
        self._prompt_guard = prompt_guard
        self._artifact_io = artifact_io

    def _monitor_service(self, planspace: Path) -> MonitorService:
        return MonitorService(
            DatabaseClient.for_planspace(planspace, self._config.db_sh),
            self._config.agent_name,
            self._task_router,
            self._logger,
        )

    def _check_pre_dispatch_state(
        self,
        planspace: Path | None,
    ) -> DispatchResult | None:
        """Check pipeline state before dispatching. Returns early result or None."""
        if not planspace:
            return None
        self._pipeline_control.wait_if_paused(planspace)
        # If alignment_changed was received during the pause (or was
        # already pending), do NOT launch the agent — excerpts are stale.
        if self._pipeline_control.alignment_changed_pending(planspace):
            self._logger.log("  dispatch_agent: alignment_changed pending — skipping")
            return DispatchResult(DispatchStatus.ALIGNMENT_CHANGED, "")
        return None

    def _evaluate_qa_intercept(
        self,
        planspace: Path,
        agent_file: str, prompt_path: Path,
        agent_name: str | None,
    ) -> DispatchResult | None:
        """Run QA gate evaluation. Returns rejection result or None to proceed."""
        if agent_file == "qa-interceptor.md":
            return None
        from qa.service.qa_gate import evaluate_qa_gate
        intercept = evaluate_qa_gate(
            planspace, agent_file, prompt_path,
            submitted_by=agent_name or "section-loop",
        )
        if intercept is None:
            return None
        self._logger.log(f"  QA intercept: evaluating dispatch ({agent_file})")
        if not intercept.intercepted:
            self._logger.log(f"  QA REJECT: {agent_file} — see {intercept.verdict}")
            return DispatchResult(DispatchStatus.QA_REJECTED, intercept.verdict or "")
        if intercept.output_path:
            self._logger.log(f"  QA DEGRADED ({intercept.output_path}) — failing open")
        else:
            self._logger.log(f"  QA PASS: {agent_file}")
        return None

    def _finalize_dispatch(
        self,
        run_result: object, output_path: Path,
        planspace: Path | None, monitor_handle: object | None,
    ) -> DispatchResult:
        """Process agent result: stop monitor, write output and metadata."""
        output = run_result.output
        if run_result.timed_out:
            self._logger.log("  WARNING: agent timed out after 1800s")
        elif run_result.returncode != 0:
            self._logger.log(f"  WARNING: agent returned {run_result.returncode}")

        if monitor_handle is not None:
            output = self._monitor_service(planspace).stop(monitor_handle, output)

        output_path.write_text(output, encoding="utf-8")
        if planspace is not None:
            self._communicator.log_artifact(planspace, f"output:{output_path.stem}")

        Metadata(self._artifact_io).write_dispatch_metadata(
            output_path,
            returncode=run_result.returncode if not run_result.timed_out else None,
            timed_out=run_result.timed_out,
        )

        status = DispatchStatus.TIMEOUT if run_result.timed_out else DispatchStatus.SUCCESS
        return DispatchResult(status, output)

    def _write_agent_monitor_prompt(
        self,
        planspace: Path, agent_name: str, monitor_name: str,
    ) -> Path:
        """Write the prompt file for a per-agent GLM monitor."""
        paths = PathRegistry(planspace)
        db_path = paths.run_db()
        prompt_path = paths.artifacts / f"{monitor_name}-prompt.md"

        template = load_template("dispatch/agent-monitor.md", SRC_TEMPLATE_DIR)
        dynamic_body = render(template, {
            "agent_name": agent_name,
            "monitor_name": monitor_name,
            "db_sh": str(self._config.db_sh),
            "db_path": str(db_path),
            "planspace": str(planspace),
        })
        violations = self._prompt_guard.validate_dynamic(dynamic_body)
        if violations:
            self._logger.log(f"  ERROR: monitor prompt blocked — dynamic violations: {violations}")
            return prompt_path
        prompt_path.write_text(
            render_template("monitor", dynamic_body),
            encoding="utf-8",
        )
        self._communicator.log_artifact(planspace, f"prompt:agent-monitor-{agent_name}")
        return prompt_path

    def dispatch_agent(self, model: str, prompt_path: Path, output_path: Path,
                       planspace: Path | None = None,
                       agent_name: str | None = None,
                       codespace: Path | None = None,
                       section_number: str | None = None,
                       *,
                       agent_file: str) -> DispatchResult:
        """Run an agent via the agents binary and return the output text.

        If planspace is provided, checks pipeline state before dispatching
        and waits if paused.

        If agent_name is provided, launches an agent-monitor alongside the
        agent to watch for loops and stuck states. The monitor is a GLM
        agent that reads the agent's mailbox.

        If codespace is provided, passes --project to the agent so it runs
        with the correct working directory and model config lookup.

        ``agent_file`` is REQUIRED — every dispatch must have behavioral
        constraints. Pass a basename like ``"alignment-judge.md"``; the
        agent definition is prepended to the prompt via ``--agent-file``.
        """
        if not agent_file:
            raise ValueError(
                "agent_file is required — every dispatch must have "
                "behavioral constraints"
            )
        agent_path = self._task_router.resolve_agent_path(agent_file)

        early = self._check_pre_dispatch_state(planspace)
        if early is not None:
            return early

        if planspace:
            ContextSidecar(self._artifact_io).materialize_context_sidecar(
                str(agent_path), planspace, section=section_number,
            )

        monitor_handle = None
        if planspace and agent_name:
            monitor_prompt = self._write_agent_monitor_prompt(
                planspace, agent_name, f"{agent_name}-monitor",
            )
            monitor_handle = self._monitor_service(planspace).start(
                agent_name, monitor_prompt,
            )

        if planspace:
            qa_result = self._evaluate_qa_intercept(
                planspace, agent_file, prompt_path, agent_name,
            )
            if qa_result is not None:
                return qa_result

        self._logger.log(f"  dispatch {model} → {prompt_path.name}")
        if planspace and section_number:
            name_label = agent_name or model
            cfg = self._config
            DatabaseClient.for_planspace(planspace, cfg.db_sh).log_event(
                "summary",
                f"dispatch:{section_number}",
                f"{name_label} dispatched",
                agent=cfg.agent_name,
                check=False,
            )

        executor = AgentExecutor(task_router=self._task_router)
        run_result = executor.run_agent(
            model, prompt_path,
            agent_file=agent_file, codespace=codespace, timeout=_SECTION_DISPATCH_TIMEOUT_SECONDS,
        )
        return self._finalize_dispatch(run_result, output_path, planspace, monitor_handle)
