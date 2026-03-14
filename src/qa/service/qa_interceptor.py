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
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from qa.helpers.qa_verdict import parse_qa_verdict

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        ModelPolicyService,
        PromptGuard,
        TaskRouterService,
    )


logger = logging.getLogger(__name__)


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


class QaInterceptor:
    """Evaluates tasks against agent contracts before dispatch.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        task_router: TaskRouterService,
        policies: ModelPolicyService,
        dispatcher: AgentDispatcher,
        prompt_guard: PromptGuard,
    ) -> None:
        self._artifact_io = artifact_io
        self._task_router = task_router
        self._policies = policies
        self._dispatcher = dispatcher
        self._prompt_guard = prompt_guard

    def read_qa_parameters(self, planspace: Path) -> dict:
        """Read QA parameters from ``artifacts/parameters.json``.

        Returns a dict with at minimum ``{"qa_mode": False}``.
        Falls back to defaults if the file is absent or malformed.
        """
        params_path = PathRegistry(planspace).parameters()
        defaults: dict = {"qa_mode": False}

        if not params_path.exists():
            return defaults

        data = self._artifact_io.read_json(params_path)
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
            self._artifact_io.rename_malformed(params_path)
            return defaults

        # Merge with defaults so qa_mode always exists.
        return {**defaults, **data}

    def _resolve_submitter_contract(self, submitter: str) -> str:
        """Resolve the submitter identity to a contract string."""
        try:
            agent_path = self._task_router.resolve_agent_path(f"{submitter}.md")
            return agent_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass

        if submitter in _INFRA_SUBMITTERS:
            return (
                f"Submitter: {submitter}\n\n"
                f"Description: {_INFRA_SUBMITTERS[submitter]}"
            )

        return (
            f"Submitter: {submitter}\n\n"
            f"No agent contract available for this submitter."
        )

    def _write_rationale(
        self,
        planspace: Path,
        task: dict[str, str],
        agent_file: str,
        verdict: str,
        rationale: str,
        violations: list[str],
    ) -> Path:
        """Write a structured rationale JSON file for a QA intercept."""
        intercepts_dir = PathRegistry(planspace).qa_intercepts_dir()

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

        self._artifact_io.write_json(rationale_path, rationale_data)
        return rationale_path

    def _dispatch_and_evaluate(
        self,
        task: dict[str, str], agent_file: str, planspace: Path,
        prompt_path: Path, output_path: Path,
    ) -> InterceptResult:
        """Dispatch QA agent and evaluate verdict."""
        policy = self._policies.load(planspace)
        model = self._policies.resolve(policy, "qa_interceptor")
        output = self._dispatcher.dispatch(
            model, prompt_path, output_path,
            planspace, None,
            agent_file=self._task_router.agent_for("qa.qa_intercept"),
        )

        qa_verdict = parse_qa_verdict(output.output)

        if qa_verdict.verdict == "REJECT":
            rationale_path = self._write_rationale(
                planspace, task, agent_file,
                qa_verdict.verdict, qa_verdict.rationale, qa_verdict.violations,
            )
            return InterceptResult(intercepted=False, verdict=str(rationale_path), output_path=None)

        if qa_verdict.verdict == "DEGRADED":
            rationale_path = self._write_rationale(
                planspace, task, agent_file,
                qa_verdict.verdict, qa_verdict.rationale, qa_verdict.violations,
            )
            return InterceptResult(intercepted=True, verdict=str(rationale_path), output_path="unparseable")

        return InterceptResult(intercepted=True, verdict=None, output_path=None)

    def intercept_dispatch(
        self,
        *,
        agent_file: str,
        prompt_path: Path,
        planspace: Path,
        submitted_by: str = "section-loop",
    ) -> InterceptResult:
        """Evaluate a direct dispatch against agent contracts.

        Creates a synthetic task dict and delegates to ``intercept_task()``.
        """
        task = {
            "id": f"dispatch-{int(time.time())}",
            "type": "direct-dispatch",
            "by": submitted_by,
            "payload": str(prompt_path),
            "priority": "normal",
            "scope": "unscoped",
        }
        return self.intercept_task(task, agent_file, planspace)

    def intercept_task(
        self,
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
            try:
                target_path = self._task_router.resolve_agent_path(agent_file)
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

            submitter_contract = self._resolve_submitter_contract(submitted_by)
            payload_content = _read_payload_content(task, planspace)

            qa_prompt_text = _build_qa_prompt(
                task, target_contract, submitter_contract, payload_content,
            )

            intercepts_dir = PathRegistry(planspace).qa_intercepts_dir()
            prompt_path = intercepts_dir / f"qa-{task_id}-prompt.md"
            prompt_path.write_text(qa_prompt_text, encoding="utf-8")

            safety_violations = self._prompt_guard.validate_dynamic(payload_content)
            if safety_violations:
                print(
                    f"[qa-interceptor] Prompt safety violation in payload "
                    f"for task {task_id}: {safety_violations} — "
                    f"failing open (PAT-0014 degraded)",
                    flush=True,
                )
                return InterceptResult(intercepted=True, verdict=None, output_path="safety_blocked")

            output_path = intercepts_dir / f"qa-{task_id}-output.md"
            return self._dispatch_and_evaluate(task, agent_file, planspace, prompt_path, output_path)

        except Exception:  # noqa: BLE001 — fail-open: QA errors must not block dispatch
            logger.error(
                "QA evaluation failed for task %s — failing open (degraded)",
                task_id,
                exc_info=True,
            )
            return InterceptResult(intercepted=True, verdict=None, output_path="dispatch_error")


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
    from pipeline.template import render_template

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


def _read_payload_content(
    task: dict[str, str], planspace: Path,
) -> str:
    """Read task payload content from the payload path."""
    payload_path_str = task.get("payload", "")
    if not payload_path_str:
        return ""
    pp = Path(payload_path_str)
    if not pp.is_absolute():
        pp = planspace / pp
    if pp.exists():
        return pp.read_text(encoding="utf-8")
    return ""
