"""QA dispatch interceptor — evaluates tasks against agent contracts.

Optional pre-dispatch gate that sends the task payload and both agent
contracts (submitter + target) to an Opus QA agent for contract
compliance checking.  Context-blind by design: the QA agent sees only
the two .md files and the task payload.

Enabled via ``{planspace}/artifacts/parameters.json``::

    {"qa_mode": true}

Design: fail-OPEN on QA errors.  If the QA agent times out, returns
garbage, or this module has an import error, the task PASSES.  Only an
explicit REJECT blocks dispatch.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from containers import Services
from signals.repository.artifact_io import read_json, rename_malformed, write_json
from orchestrator.path_registry import PathRegistry
from qa.helpers.qa_verdict import QaVerdict, parse_qa_verdict


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchEvaluation:
    """Structured result from ``intercept_dispatch``."""

    should_intercept: bool
    reason: str | None
    qa_prompt_path: str | None


@dataclass(frozen=True)
class InterceptResult:
    """Structured result from ``intercept_task``."""

    intercepted: bool
    verdict: str | None
    output_path: str | None

# Infrastructure submitters that are not agent files.
_INFRA_SUBMITTERS: dict[str, str] = {
    "section-loop": (
        "Infrastructure orchestrator that coordinates section-level "
        "proposal and implementation passes."
    ),
    "task-dispatcher": (
        "Infrastructure dispatcher that polls the task queue and "
        "launches agents."
    ),
}

# Maximum payload characters included in the QA prompt.
_PAYLOAD_TRUNCATION = 5000


def read_qa_parameters(planspace: Path) -> dict:
    """Read QA parameters from ``artifacts/parameters.json``.

    Returns a dict with at minimum ``{"qa_mode": False}``.
    Falls back to defaults if the file is absent or malformed.
    Malformed files are renamed to ``.malformed.json`` (same pattern
    as ``read_model_policy`` in ``section_loop/dispatch.py``).
    """
    params_path = PathRegistry(planspace).parameters()
    defaults: dict = {"qa_mode": False}

    if not params_path.exists():
        return defaults

    data = read_json(params_path)
    if data is None:
        print(
            f"[qa-interceptor] WARNING: Malformed parameters.json at "
            f"{params_path} — renaming to .malformed.json",
            flush=True,
        )
        return defaults

    if not isinstance(data, dict):
        print(
            f"[qa-interceptor] WARNING: parameters.json is not a JSON "
            f"object — renaming to .malformed.json",
            flush=True,
        )
        rename_malformed(params_path)
        return defaults

    # Merge with defaults so qa_mode always exists.
    return {**defaults, **data}


def _resolve_submitter_contract(submitter: str) -> str:
    """Resolve the submitter identity to a contract string.

    Tries ``agents/{submitter}.md``, then falls back to a description
    string for infrastructure submitters or an unknown-submitter note.
    """
    # Try direct agent file lookup.
    try:
        agent_path = Services.task_router().resolve_agent_path(f"{submitter}.md")
        return agent_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass

    # Infrastructure submitters get a description string.
    if submitter in _INFRA_SUBMITTERS:
        return (
            f"Submitter: {submitter}\n\n"
            f"Description: {_INFRA_SUBMITTERS[submitter]}"
        )

    return (
        f"Submitter: {submitter}\n\n"
        f"No agent contract available for this submitter."
    )


def _build_qa_prompt(
    task: dict[str, str],
    target_contract: str,
    submitter_contract: str,
    payload_content: str,
) -> str:
    """Build the QA evaluation prompt body.

    Uses ``render_template`` from ``agent_templates`` to wrap with
    system constraints.
    """
    from dispatch.prompt.template import render_template

    task_id = task.get("id", "?")
    task_type = task.get("type", "?")
    submitted_by = task.get("by", "unknown")
    priority = task.get("priority", "normal")
    scope = task.get("scope", "unscoped")

    # Truncate payload to keep the QA prompt focused.
    truncated = payload_content[:_PAYLOAD_TRUNCATION]
    if len(payload_content) > _PAYLOAD_TRUNCATION:
        truncated += "\n\n[... payload truncated ...]"

    dynamic_body = f"""# QA Contract Compliance Check

## Target Agent Contract

{target_contract}

## Submitting Agent Identity

{submitter_contract}

## Task Under Evaluation

- Task ID: {task_id}
- Task Type: {task_type}
- Submitted By: {submitted_by}
- Priority: {priority}
- Scope: {scope}

## Task Payload

{truncated}

## Instructions

Evaluate whether this task payload complies with BOTH agent contracts.
Reply with EXACTLY one JSON block — no other text.

PASS example:
```json
{{"verdict": "PASS", "rationale": "..."}}
```

REJECT example:
```json
{{"verdict": "REJECT", "rationale": "...", "violations": ["..."]}}
```
"""
    return render_template("qa-intercept", dynamic_body)


def _write_rationale(
    planspace: Path,
    task: dict[str, str],
    agent_file: str,
    verdict: str,
    rationale: str,
    violations: list[str],
) -> Path:
    """Write a structured rationale JSON file for a QA intercept.

    Returns the path to the written file.
    """
    intercepts_dir = PathRegistry(planspace).qa_intercepts_dir()
    intercepts_dir.mkdir(parents=True, exist_ok=True)

    task_id = task.get("id", "unknown")
    rationale_path = intercepts_dir / f"qa-{task_id}-rationale.json"

    rationale_data = {
        "task_id": task_id,
        "task_type": task.get("type", "unknown"),
        "submitter": task.get("by", "unknown"),
        "target_agent": agent_file,
        "verdict": verdict,
        "rationale": rationale,
        "violations": violations,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    write_json(rationale_path, rationale_data)
    return rationale_path


def intercept_dispatch(
    *,
    agent_file: str,
    prompt_path: Path,
    planspace: Path,
    submitted_by: str = "section-loop",
) -> InterceptResult:
    """Evaluate a direct dispatch against agent contracts.

    Creates a synthetic task dict and delegates to ``intercept_task()``.
    Used by ``dispatch.section_dispatch.dispatch_agent()`` to intercept
    dispatches that bypass the task queue.
    """
    task = {
        "id": f"dispatch-{int(time.time())}",
        "type": "direct-dispatch",
        "by": submitted_by,
        "payload": str(prompt_path),
        "priority": "normal",
        "scope": "unscoped",
    }
    return intercept_task(task, agent_file, planspace)


def intercept_task(
    task: dict[str, str],
    agent_file: str,
    planspace: Path,
) -> InterceptResult:
    """Evaluate a task against submitter and target agent contracts.

    Returns an ``InterceptResult``:

    - ``InterceptResult(True, None, None)`` — task genuinely passed QA.
    - ``InterceptResult(False, "/path/to/rationale.json", None)`` — task rejected.
    - ``InterceptResult(True, path_or_None, "reason_code")`` — degraded advisory
      (PAT-0014): QA failed internally, dispatch falls back to baseline.

    Fail-OPEN: any error during QA evaluation (timeout, parse failure,
    import error, missing files) results in the task passing with a
    degraded reason_code.  Only an explicit REJECT from the QA agent
    blocks dispatch.
    """
    task_id = task.get("id", "?")
    submitted_by = task.get("by", "unknown")

    try:
        # 1. Read target agent contract.
        try:
            target_path = Services.task_router().resolve_agent_path(agent_file)
        except FileNotFoundError:
            target_path = None
        if target_path is None:
            print(
                f"[qa-interceptor] WARNING: Target agent file not found: "
                f"{target_path} — failing open (task {task_id})",
                flush=True,
            )
            return InterceptResult(intercepted=True, verdict=None, output_path="target_unavailable")
        target_contract = target_path.read_text(encoding="utf-8")

        # 2. Resolve submitter contract.
        submitter_contract = _resolve_submitter_contract(submitted_by)

        # 3. Read payload.
        payload_path_str = task.get("payload", "")
        payload_content = ""
        if payload_path_str:
            pp = Path(payload_path_str)
            if not pp.is_absolute():
                pp = planspace / pp
            if pp.exists():
                payload_content = pp.read_text(encoding="utf-8")

        # 4. Build QA prompt.
        qa_prompt_text = _build_qa_prompt(
            task, target_contract, submitter_contract, payload_content,
        )

        # 5. Validate and write prompt to artifacts.
        # PAT-0002 (R109): payload_content is untrusted dynamic content
        # even though the payload path arrived through an internal task.
        # Agent contracts are trusted but payload is not — validate the
        # full rendered prompt before dispatch.
        intercepts_dir = PathRegistry(planspace).qa_intercepts_dir()
        intercepts_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = intercepts_dir / f"qa-{task_id}-prompt.md"
        prompt_path.write_text(qa_prompt_text, encoding="utf-8")

        safety_violations = Services.prompt_guard().validate_dynamic(payload_content)
        if safety_violations:
            print(
                f"[qa-interceptor] Prompt safety violation in payload "
                f"for task {task_id}: {safety_violations} — "
                f"failing open (PAT-0014 degraded)",
                flush=True,
            )
            return InterceptResult(intercepted=True, verdict=None, output_path="safety_blocked")

        output_path = intercepts_dir / f"qa-{task_id}-output.md"

        # 6. Dispatch QA agent.
        policy = Services.policies().load(planspace)
        model = Services.policies().resolve(policy, "qa_interceptor")
        output = Services.dispatcher().dispatch(
            model,
            prompt_path,
            output_path,
            planspace,
            None,  # parent — not inside section-loop context
            agent_file=Services.task_router().agent_for("qa.qa_intercept"),
        )

        # 7. Parse verdict.
        qa_verdict = parse_qa_verdict(output)

        if qa_verdict.verdict == "REJECT":
            rationale_path = _write_rationale(
                planspace, task, agent_file,
                qa_verdict.verdict, qa_verdict.rationale, qa_verdict.violations,
            )
            return InterceptResult(intercepted=False, verdict=str(rationale_path), output_path=None)

        if qa_verdict.verdict == "DEGRADED":
            # PAT-0014: QA parse failure — fail open but preserve evidence
            rationale_path = _write_rationale(
                planspace, task, agent_file,
                qa_verdict.verdict, qa_verdict.rationale, qa_verdict.violations,
            )
            return InterceptResult(intercepted=True, verdict=str(rationale_path), output_path="unparseable")

        # Genuine PASS
        return InterceptResult(intercepted=True, verdict=None, output_path=None)

    except Exception:
        # Fail-OPEN: any error during QA means the task passes.
        # PAT-0014: preserve degraded status distinctly from genuine PASS.
        logger.error(
            "QA evaluation failed for task %s — failing open (degraded)",
            task_id,
            exc_info=True,
        )
        return InterceptResult(intercepted=True, verdict=None, output_path="dispatch_error")
