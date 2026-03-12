from pathlib import Path

from dispatch.engine import agent_executor
from signals.service.database_client import DatabaseClient
from dispatch.repository.metadata import write_dispatch_metadata
from dispatch.service.monitor_service import MonitorService
from orchestrator.path_registry import PathRegistry

from dispatch.prompt.template import SRC_TEMPLATE_DIR, load_template, render, render_template
from dispatch.service.prompt_guard import validate_dynamic_content
from _config import AGENT_NAME, DB_SH
from signals.service.communication import (
    _log_artifact,
)
from orchestrator.service.context_assembly import materialize_context_sidecar
from containers import Services


def _monitor_service(planspace: Path) -> MonitorService:
    return MonitorService(
        DatabaseClient.for_planspace(planspace, DB_SH),
        AGENT_NAME,
        logger=Services.logger().log,
    )


def dispatch_agent(model: str, prompt_path: Path, output_path: Path,
                   planspace: Path | None = None,
                   parent: str | None = None,
                   agent_name: str | None = None,
                   codespace: Path | None = None,
                   section_number: str | None = None,
                   *,
                   agent_file: str) -> str:
    """Run an agent via the agents binary and return the output text.

    If planspace and parent are provided, checks pipeline state before
    dispatching and waits if paused.

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
    agent_path = Services.task_router().resolve_agent_path(agent_file)
    if planspace and parent:
        Services.pipeline_control().wait_if_paused(planspace, parent)
        # If alignment_changed was received during the pause (or was
        # already pending), do NOT launch the agent — excerpts are stale.
        if Services.pipeline_control().alignment_changed_pending(planspace):
            Services.logger().log("  dispatch_agent: alignment_changed pending — skipping")
            return "ALIGNMENT_CHANGED_PENDING"

    # --- Resolve agent-scoped context (S1) ---
    # Creates/refreshes the JSON sidecar so the agent has scoped context.
    # Prompt writers also call materialize_context_sidecar() before
    # rendering to ensure the sidecar exists at prompt-write time.
    if planspace:
        materialize_context_sidecar(
            str(agent_path), planspace, section=section_number,
        )

    monitor_handle = None

    if planspace and agent_name:
        monitor_prompt = _write_agent_monitor_prompt(
            planspace,
            agent_name,
            f"{agent_name}-monitor",
        )
        monitor_handle = _monitor_service(planspace).start(
            agent_name,
            monitor_prompt,
        )

    # --- QA dispatch interceptor (optional) ---
    # Mirrors task_dispatcher.py QA gate but at the dispatch level.
    # Skip for qa-interceptor.md to prevent infinite recursion.
    if planspace and agent_file != "qa-interceptor.md":
        try:
            from qa.service.qa_interceptor import intercept_dispatch, read_qa_parameters
            qa_params = read_qa_parameters(planspace)
        except Exception:
            qa_params = {}

        if qa_params.get("qa_mode"):
            Services.logger().log(f"  QA intercept: evaluating dispatch ({agent_file})")
            try:
                intercept = intercept_dispatch(
                    agent_file=agent_file,
                    prompt_path=prompt_path,
                    planspace=planspace,
                    submitted_by=agent_name or "section-loop",
                )
            except Exception as exc:
                Services.logger().log(f"  QA ERROR: {exc} — failing open (degraded)")
                from qa.service.qa_interceptor import InterceptResult
                intercept = InterceptResult(intercepted=True, verdict=None, output_path="dispatch_error")

            if not intercept.intercepted:
                Services.logger().log(f"  QA REJECT: {agent_file} — see {intercept.verdict}")
                return f"QA_REJECTED:{intercept.verdict}"
            if intercept.output_path:
                Services.logger().log(f"  QA DEGRADED ({intercept.output_path}) — failing open")
            else:
                Services.logger().log(f"  QA PASS: {agent_file}")

    Services.logger().log(f"  dispatch {model} → {prompt_path.name}")
    # Emit per-section dispatch summary event for QA monitor rule C1
    if planspace and section_number:
        name_label = agent_name or model
        DatabaseClient.for_planspace(planspace, DB_SH).log_event(
            "summary",
            f"dispatch:{section_number}",
            f"{name_label} dispatched",
            agent=AGENT_NAME,
            check=False,
        )
    run_result = agent_executor.run_agent(
        model,
        prompt_path,
        output_path,
        agent_file=agent_file,
        codespace=codespace,
        timeout=1800,
    )
    output = run_result.output
    if run_result.timed_out:
        Services.logger().log("  WARNING: agent timed out after 1800s")
    elif run_result.returncode != 0:
        Services.logger().log(f"  WARNING: agent returned {run_result.returncode}")

    # Shut down agent-monitor after agent finishes
    if monitor_handle is not None:
        output = _monitor_service(planspace).stop(monitor_handle, output)

    # Write output AFTER signal check (so the saved file includes
    # the LOOP_DETECTED line for forensic debugging)
    output_path.write_text(output, encoding="utf-8")
    if planspace is not None:
        _log_artifact(planspace, f"output:{output_path.stem}")

    # Write dispatch metadata sidecar for callers that need return-code visibility
    write_dispatch_metadata(
        output_path,
        returncode=run_result.returncode if not run_result.timed_out else None,
        timed_out=run_result.timed_out,
    )

    return output


def _write_agent_monitor_prompt(
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
        "db_sh": str(DB_SH),
        "db_path": str(db_path),
        "planspace": str(planspace),
    })
    violations = validate_dynamic_content(dynamic_body)
    if violations:
        Services.logger().log(f"  ERROR: monitor prompt blocked — dynamic violations: {violations}")
        return prompt_path
    prompt_path.write_text(
        render_template("monitor", dynamic_body),
        encoding="utf-8",
    )
    _log_artifact(planspace, f"prompt:agent-monitor-{agent_name}")
    return prompt_path
