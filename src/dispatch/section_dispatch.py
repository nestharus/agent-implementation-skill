import json
import subprocess
from pathlib import Path
from typing import Any

from dispatch import agent_executor
from staleness.alignment_change_tracker import check_pending as alignment_changed_pending
from signals.database_client import DatabaseClient
from dispatch.dispatch_helpers import (
    check_agent_signals,
    summarize_output,
    write_model_choice_signal,
)
from dispatch.dispatch_metadata import write_dispatch_metadata
from dispatch.model_policy import load_model_policy
from dispatch.monitor_service import MonitorService
from orchestrator.path_registry import PathRegistry
from signals.signal_reader import read_agent_signal, read_signal_tuple

from .agent_templates import render_template, validate_dynamic_content
from signals.section_loop_communication import (
    AGENT_NAME,
    DB_SH,
    WORKFLOW_HOME,
    _log_artifact,
    log,
)
from orchestrator.context_assembly import materialize_context_sidecar
from orchestrator.pipeline_control import wait_if_paused


def _database_client(planspace: Path) -> DatabaseClient:
    return DatabaseClient(DB_SH, PathRegistry(planspace).run_db())


def _monitor_service(planspace: Path) -> MonitorService:
    return MonitorService(
        _database_client(planspace),
        Path(WORKFLOW_HOME),
        AGENT_NAME,
        logger=log,
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
    agent_path = Path(WORKFLOW_HOME) / "agents" / agent_file
    if not agent_path.exists():
        raise FileNotFoundError(f"Agent file not found: {agent_path}")
    if planspace and parent:
        wait_if_paused(planspace, parent)
        # If alignment_changed was received during the pause (or was
        # already pending), do NOT launch the agent — excerpts are stale.
        if alignment_changed_pending(planspace):
            log("  dispatch_agent: alignment_changed pending — skipping")
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
            from dispatch.qa_interceptor import intercept_dispatch, read_qa_parameters
            qa_params = read_qa_parameters(planspace)
        except Exception:
            qa_params = {}

        if qa_params.get("qa_mode"):
            log(f"  QA intercept: evaluating dispatch ({agent_file})")
            try:
                passed, rationale_path, reason_code = intercept_dispatch(
                    agent_file=agent_file,
                    prompt_path=prompt_path,
                    planspace=planspace,
                    submitted_by=agent_name or "section-loop",
                )
            except Exception as exc:
                log(f"  QA ERROR: {exc} — failing open (degraded)")
                passed = True
                rationale_path = None
                reason_code = "dispatch_error"

            if not passed:
                log(f"  QA REJECT: {agent_file} — see {rationale_path}")
                return f"QA_REJECTED:{rationale_path}"
            if reason_code:
                log(f"  QA DEGRADED ({reason_code}) — failing open")
            else:
                log(f"  QA PASS: {agent_file}")

    log(f"  dispatch {model} → {prompt_path.name}")
    # Emit per-section dispatch summary event for QA monitor rule C1
    if planspace and section_number:
        name_label = agent_name or model
        _database_client(planspace).log_event(
            "summary",
            f"dispatch:{section_number}",
            f"{name_label} dispatched",
            agent=AGENT_NAME,
            check=False,
        )
    agent_executor.WORKFLOW_HOME = Path(WORKFLOW_HOME)
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
        log("  WARNING: agent timed out after 1800s")
    elif run_result.returncode != 0:
        log(f"  WARNING: agent returned {run_result.returncode}")

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

    dynamic_body = f"""# Agent Monitor: {agent_name}

## Your Job
Watch mailbox `{agent_name}` for messages from a running agent.
Detect if the agent is looping (repeating the same actions).
Report loops by logging signal events to the database.

## Setup
```bash
bash "{DB_SH}" register "{db_path}" {monitor_name}
```

## Paths
- Planspace: `{planspace}`
- Database: `{db_path}`
- Agent mailbox to watch: `{agent_name}`
- Your mailbox: `{monitor_name}`

## Monitor Loop
1. Drain all messages from `{agent_name}` mailbox
2. Track "plan:" messages in memory
3. If you see the same plan repeated (same action on same file) → loop detected
4. Check your own mailbox for `agent-finished` signal → exit
5. Wait 10 seconds, repeat

## Loop Detection
Keep a list of all `plan:` messages received. If a new `plan:` message
is substantially similar to a previous one (same file, same action),
the agent is looping.

**Agent self-reported loop:** If ANY drained message starts with
`LOOP_DETECTED:`, the agent has self-detected a loop. Immediately log
that payload as a signal event and exit — no further analysis needed.

When loop detected (either self-reported or by your analysis), log a
signal event:
```bash
bash "{DB_SH}" log "{db_path}" signal {agent_name} "LOOP_DETECTED:{agent_name}:<repeated action>" --agent {monitor_name}
```

Do NOT send loop signals via mailbox — only log signal events as above.

## Exit Conditions
- Receive `agent-finished` on your mailbox → exit normally
- 5 minutes with no messages from agent → log stalled warning, then exit:
  ```bash
  bash "{DB_SH}" log "{db_path}" signal {agent_name} "STALLED:{agent_name}:no messages for 5 minutes" --agent {monitor_name}
  ```
"""
    violations = validate_dynamic_content(dynamic_body)
    if violations:
        from signals.section_loop_communication import log
        log(f"  ERROR: monitor prompt blocked — dynamic violations: {violations}")
        return prompt_path
    prompt_path.write_text(
        render_template("monitor", dynamic_body),
        encoding="utf-8",
    )
    _log_artifact(planspace, f"prompt:agent-monitor-{agent_name}")
    return prompt_path


def adjudicate_agent_output(
    output_path: Path, planspace: Path, parent: str,
    codespace: Path | None = None,
    *,
    model: str,
) -> tuple[str | None, str]:
    """Dispatch state-adjudicator to classify ambiguous agent output.

    Used when structured signal file is absent but output may contain
    signals. Returns (signal_type, detail) or (None, "").
    """
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    artifacts.mkdir(parents=True, exist_ok=True)
    adj_prompt = artifacts / "adjudicate-prompt.md"
    adj_output = artifacts / "adjudicate-output.md"

    dynamic_body = f"""# Classify Agent Output

Read the agent output file and determine its state.

## Agent Output File
`{output_path}`

## Instructions

Classify the output into exactly one state. Reply with a JSON block:

```json
{{
  "state": "<STATE>",
  "detail": "<brief explanation>"
}}
```

States: ALIGNED, PROBLEMS, UNDERSPECIFIED, NEED_DECISION, DEPENDENCY,
LOOP_DETECTED, NEEDS_PARENT, OUT_OF_SCOPE, COMPLETED, UNKNOWN.
"""
    violations = validate_dynamic_content(dynamic_body)
    if violations:
        from signals.section_loop_communication import log
        log(f"  ERROR: adjudicate prompt blocked — dynamic violations: {violations}")
        return None, ""
    adj_prompt.write_text(
        render_template(
            "adjudicate", dynamic_body,
            file_paths=[str(output_path)],
        ),
        encoding="utf-8",
    )

    result = dispatch_agent(
        model, adj_prompt, adj_output,
        planspace, parent, codespace=codespace,
        agent_file="state-adjudicator.md",
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return None, "ALIGNMENT_CHANGED_PENDING"

    # Parse JSON from adjudicator output
    try:
        json_start = result.find("{")
        json_end = result.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(result[json_start:json_end + 1])
            state = data.get("state", "").lower()
            detail = data.get("detail", "")
            if state in ("underspecified", "underspec"):
                return "underspec", detail
            if state == "need_decision":
                return "need_decision", detail
            if state == "dependency":
                return "dependency", detail
            if state == "loop_detected":
                return "loop_detected", detail
            if state == "needs_parent":
                return "needs_parent", detail
            if state in ("out_of_scope", "out-of-scope"):
                return "out_of_scope", detail
    except (json.JSONDecodeError, KeyError) as exc:
        print(
            f"[ADJUDICATOR][WARN] Malformed adjudicator verdict JSON "
            f"({exc}) — treating as unrecognized signal",
        )
    return None, ""


def create_signal_template(section: str, state: str, detail: str = "",
                           **extra: Any) -> dict[str, Any]:
    """Create a standardized signal dict for agent output.

    Agents should include this JSON in their output for reliable
    state classification. Scripts read JSON — agents decide semantics.
    """
    signal: dict[str, Any] = {
        "state": state,
        "section": section,
        "detail": detail,
    }
    signal.update(extra)
    return signal


def read_model_policy(planspace: Path) -> dict[str, Any]:
    """Read model policy from artifacts/model-policy.json."""
    return load_model_policy(planspace)
