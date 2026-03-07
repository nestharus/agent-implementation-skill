import json
import subprocess
from pathlib import Path
from typing import Any

from lib.artifact_io import read_json, rename_malformed, write_json
from lib.database_client import DatabaseClient
from lib.monitor_service import MonitorService

from .agent_templates import render_template, validate_dynamic_content
from .communication import (
    AGENT_NAME,
    DB_SH,
    WORKFLOW_HOME,
    _log_artifact,
    log,
)
from .context_assembly import materialize_context_sidecar
from .pipeline_control import alignment_changed_pending, wait_if_paused


def _database_client(planspace: Path) -> DatabaseClient:
    return DatabaseClient(DB_SH, planspace / "run.db")


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
    cmd = ["agents", "--model", model,
           "--file", str(prompt_path),
           "--agent-file", str(WORKFLOW_HOME / "agents" / agent_file)]
    if codespace:
        cmd.extend(["--project", str(codespace)])
    timed_out = False
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            log(f"  WARNING: agent returned {result.returncode}")
    except subprocess.TimeoutExpired:
        timed_out = True
        output = "TIMEOUT: Agent exceeded 600s time limit"
        log("  WARNING: agent timed out after 600s")

    # Shut down agent-monitor after agent finishes
    if monitor_handle is not None:
        output = _monitor_service(planspace).stop(monitor_handle, output)

    # Write output AFTER signal check (so the saved file includes
    # the LOOP_DETECTED line for forensic debugging)
    output_path.write_text(output, encoding="utf-8")
    if planspace is not None:
        _log_artifact(planspace, f"output:{output_path.stem}")

    # Write dispatch metadata sidecar for callers that need return-code visibility
    meta_path = output_path.with_suffix(".meta.json")
    meta = {
        "returncode": result.returncode if not timed_out else None,
        "timed_out": timed_out,
    }
    write_json(meta_path, meta, indent=None)

    return output


def _write_agent_monitor_prompt(
    planspace: Path, agent_name: str, monitor_name: str,
) -> Path:
    """Write the prompt file for a per-agent GLM monitor."""
    db_path = planspace / "run.db"
    prompt_path = planspace / "artifacts" / f"{monitor_name}-prompt.md"

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
    prompt_path.write_text(
        render_template("monitor", dynamic_body),
        encoding="utf-8",
    )
    _log_artifact(planspace, f"prompt:agent-monitor-{agent_name}")
    return prompt_path


def summarize_output(output: str, max_len: int = 200) -> str:
    """Extract a brief summary from agent output for status messages."""
    # Look for explicit summary lines first
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("summary:"):
            return stripped[len("summary:"):].strip()[:max_len]
    # Fall back to first non-empty, non-heading line
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:max_len]
    return "(no output)"


def read_signal_tuple(signal_path: Path) -> tuple[str | None, str]:
    """Read a structured signal file written by an agent.

    Agents write JSON signal files when they need to pause the pipeline.
    Returns (signal_type, detail) or (None, "") if no signal file exists.
    The detail includes structured fields (needs, assumptions_refused,
    suggested_escalation_target) when available for richer context.
    """
    if not signal_path.exists():
        return None, ""
    data = read_json(signal_path)
    if isinstance(data, dict):
        state = data.get("state", "").lower()
        detail = data.get("detail", "")
        # Enrich detail with structured fields when present
        needs = data.get("needs", "")
        refused = data.get("assumptions_refused", "")
        target = data.get("suggested_escalation_target", "")
        extras = []
        if needs:
            extras.append(f"Needs: {needs}")
        if refused:
            extras.append(f"Refused assumptions: {refused}")
        if target:
            extras.append(f"Escalation target: {target}")
        if extras:
            detail = f"{detail} [{'; '.join(extras)}]"
        if state in ("underspec", "underspecified"):
            return "underspec", detail
        if state in ("need_decision",):
            return "need_decision", detail
        if state in ("dependency",):
            return "dependency", detail
        if state in ("loop_detected",):
            return "loop_detected", detail
        if state in ("out_of_scope", "out-of-scope"):
            return "out_of_scope", detail
        if state in ("needs_parent",):
            return "needs_parent", detail
        # Unknown state — fail closed rather than silently ignoring
        return "needs_parent", (
            f"Unknown signal state '{state}' in {signal_path} — "
            f"failing closed. Original detail: {detail}"
        )
    # Malformed signal — preserve corrupted file for diagnosis,
    # then fail closed rather than silently ignoring.
    exc = "invalid JSON"
    if data is not None:
        exc = "non-object JSON"
        print(
            f"[SIGNAL][WARN] Malformed signal JSON at {signal_path} "
            f"({exc}) — renaming to .malformed.json",
        )
        rename_malformed(signal_path)
    return "needs_parent", (
        f"Malformed signal JSON at {signal_path} ({exc}) — "
        f"failing closed"
    )


def adjudicate_agent_output(
    output_path: Path, planspace: Path, parent: str,
    codespace: Path | None = None,
    model: str = "glm",
) -> tuple[str | None, str]:
    """Dispatch state-adjudicator to classify ambiguous agent output.

    Used when structured signal file is absent but output may contain
    signals. Returns (signal_type, detail) or (None, "").

    The ``model`` parameter defaults to ``"glm"`` but callers should
    pass ``policy["adjudicator"]`` for policy-driven selection.
    """
    artifacts = planspace / "artifacts"
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


def read_agent_signal(
    signal_path: Path, expected_fields: list[str] | None = None,
) -> dict[str, Any] | None:
    """Read a structured JSON signal artifact written by an agent.

    Returns the parsed dict if the file exists and is valid JSON.
    Returns None if the file doesn't exist or is malformed.
    If expected_fields is provided, returns None when any are missing.

    Scripts read JSON only — if missing/invalid, the caller should
    dispatch the appropriate agent to regenerate.
    """
    if not signal_path.exists():
        return None
    data = read_json(signal_path)
    if data is None:
        print(
            f"[SIGNAL][WARN] Malformed JSON in {signal_path} "
            f"— renaming to .malformed.json",
        )
        return None
    if not isinstance(data, dict):
        print(
            f"[SIGNAL][WARN] Signal at {signal_path} is not a JSON object "
            f"— renaming to .malformed.json",
        )
        rename_malformed(signal_path)
        return None
    if expected_fields:
        for f in expected_fields:
            if f not in data:
                return None
    return data


def write_model_choice_signal(
    planspace: Path, section: str, step: str,
    model: str, reason: str,
    escalated_from: str | None = None,
) -> None:
    """Write a structured model-choice signal for auditability."""
    signals_dir = planspace / "artifacts" / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal = {
        "section": section,
        "step": step,
        "model": model,
        "reason": reason,
        "escalated_from": escalated_from,
    }
    signal_path = signals_dir / f"model-choice-{section}-{step}.json"
    write_json(signal_path, signal)


def check_agent_signals(
    output: str, signal_path: Path | None = None,
    output_path: Path | None = None,
    planspace: Path | None = None,
    parent: str | None = None,
    codespace: Path | None = None,
) -> tuple[str | None, str]:
    """Check for agent signals via the structured JSON file.

    The JSON signal file is the sole truth channel.  If the agent
    wrote a signal file, it is read and returned.  If no signal file
    exists, the function returns ``(None, "")``.

    Adjudication (``adjudicate_agent_output``) is available for
    callers that detect a mechanical anomaly (expected artifact
    missing, empty output, malformed signal) — but it is NOT invoked
    automatically here.  This avoids paying an "adjudicator tax" on
    every unblocked agent dispatch in the common path.
    """
    # Structured signal file — the only automatic check.
    if signal_path:
        sig, detail = read_signal_tuple(signal_path)
        if sig:
            return sig, detail

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
    """Read model policy from artifacts/model-policy.json.

    Returns policy dict with defaults and escalation triggers.
    Falls back to built-in defaults if no policy file exists.
    """
    policy_path = planspace / "artifacts" / "model-policy.json"
    defaults: dict[str, Any] = {
        "setup": "claude-opus",
        "proposal": "gpt-5.4-high",
        "alignment": "claude-opus",
        "implementation": "gpt-5.4-high",
        "coordination_plan": "claude-opus",
        "coordination_fix": "gpt-5.4-high",
        "coordination_bridge": "gpt-5.4-xhigh",
        "exploration": "glm",
        "adjudicator": "glm",
        "impact_analysis": "glm",
        "impact_normalizer": "glm",
        "triage": "glm",
        "microstrategy_decider": "glm",
        "tool_registrar": "glm",
        "bridge_tools": "gpt-5.4-high",
        "escalation_model": "gpt-5.4-xhigh",
        "intent_triage": "glm",
        "intent_philosophy": "claude-opus",
        "intent_pack": "gpt-5.4-high",
        "intent_judge": "claude-opus",
        "intent_problem_expander": "claude-opus",
        "intent_philosophy_expander": "claude-opus",
        "intent_triage_escalation": "claude-opus",
        "intent_recurrence_adjudicator": "glm",
        "intent_philosophy_selector": "glm",
        "substrate_shard": "gpt-5.4-high",
        "substrate_pruner": "gpt-5.4-xhigh",
        "substrate_seeder": "gpt-5.4-high",
        "reconciliation_adjudicate": "claude-opus",
        "escalation_triggers": {
            "stall_count": 2,
            "max_attempts_before_escalation": 3,
        },
    }
    if not policy_path.exists():
        return defaults
    policy = read_json(policy_path)
    if isinstance(policy, dict):
        # Merge with defaults (policy overrides)
        merged = {**defaults, **policy}
        if "escalation_triggers" in policy:
            merged["escalation_triggers"] = {
                **defaults["escalation_triggers"],
                **policy["escalation_triggers"],
            }
        return merged
    log("  WARNING: model-policy.json exists but is invalid — "
        "renaming to .malformed.json")
    if policy is not None:
        rename_malformed(policy_path)
    return defaults
