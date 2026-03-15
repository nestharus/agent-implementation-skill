from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from staleness.service.alignment_collector import (
    AlignmentCollector,
    extract_problems,
)
from orchestrator.path_registry import PathRegistry
from pipeline.template import render_template
from staleness.helpers.verdict_parsers import parse_alignment_verdict as _parse_alignment_verdict
from orchestrator.types import Section, ControlSignal
from dispatch.types import ALIGNMENT_CHANGED_PENDING, DispatchStatus
from signals.types import ALIGNMENT_INVALID_FRAME

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        LogService,
        PipelineControlService,
        PromptGuard,
        TaskRouterService,
    )


def _build_adjudicator_prompt(output_path: Path) -> str:
    """Build the prompt for the alignment adjudicator fallback."""
    return f"""# Classify Alignment Check Output

Read the alignment check output and determine whether the section is aligned.

## Alignment Output File
`{output_path}`

## Instructions

The alignment judge was expected to produce a structured JSON verdict
but did not.  Read its output and classify the result.

Reply with a JSON block:

```json
{{
  "aligned": true|false,
  "problems": ["list of problems if misaligned, empty if aligned"],
  "reason": "brief explanation of classification"
}}
```

- If the output indicates alignment / no issues → `"aligned": true`
- If the output identifies problems or misalignment → `"aligned": false`
  with the problems listed
- If you cannot determine the state → `"aligned": false` with a single
  problem: "Unable to determine alignment state from judge output"
"""


def _parse_adjudicator_response(adj_result: str) -> str | None:
    """Parse the adjudicator's JSON response. Returns problems or None if aligned."""
    import json as _json
    if not adj_result or adj_result == ALIGNMENT_CHANGED_PENDING:
        return None
    try:
        json_start = adj_result.find("{")
        json_end = adj_result.rfind("}")
        if json_start < 0 or json_end <= json_start:
            return None
        data = _json.loads(adj_result[json_start:json_end + 1])
        if data.get("aligned") is True:
            return None
        problems = data.get("problems")
        if isinstance(problems, list) and problems:
            return "\n".join(str(p) for p in problems)
        if isinstance(problems, str) and problems.strip():
            return problems.strip()
        return "Adjudicator classified as misaligned (no detail)"
    except (_json.JSONDecodeError, KeyError):
        return None


class SectionAlignmentChecker:
    """Section alignment checking with retry and adjudication logic.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        logger: LogService,
        dispatcher: AgentDispatcher,
        task_router: TaskRouterService,
        pipeline_control: PipelineControlService,
        prompt_guard: PromptGuard,
        alignment_collector: AlignmentCollector | None = None,
    ) -> None:
        self._logger = logger
        self._dispatcher = dispatcher
        self._task_router = task_router
        self._pipeline_control = pipeline_control
        self._prompt_guard = prompt_guard
        self._alignment_collector = alignment_collector or AlignmentCollector(logger=logger)

    def collect_modified_files(
        self, planspace: Path, section: Section, codespace: Path,
    ) -> list[str]:
        """Collect modified files from the implementation report."""
        return self._alignment_collector.collect_modified_files(planspace, section, codespace)

    def extract_problems(
        self,
        result: str,
        output_path: Path | None = None,
        planspace: Path | None = None,
        codespace: Path | None = None,
        *,
        adjudicator_model: str,
    ) -> str | None:
        """Extract problem list from an alignment check result.

        Returns the problems text if misaligned, ``None`` if aligned.
        """
        verdict = _parse_alignment_verdict(result)
        if verdict is not None:
            return extract_problems(verdict)

        if output_path is not None and planspace is not None:
            paths = PathRegistry(planspace)
            dynamic_body = _build_adjudicator_prompt(output_path)
            violations = self._prompt_guard.validate_dynamic(dynamic_body)
            if violations:
                self._logger.log(
                    f"Alignment adjudicate prompt safety violation: "
                    f"{violations} — skipping dispatch"
                )
                return None

            adj_prompt = paths.alignment_adjudicate_prompt()
            adj_prompt.write_text(
                render_template(
                    "alignment-adjudicate", dynamic_body,
                    file_paths=[str(output_path)],
                ),
                encoding="utf-8",
            )
            adj_result = self._dispatcher.dispatch(
                adjudicator_model, adj_prompt, paths.alignment_adjudicate_output(),
                planspace, codespace=codespace,
                agent_file=self._task_router.agent_for("staleness.alignment_adjudicate"),
            )
            return _parse_adjudicator_response(adj_result.output)

        return ("MISSING_JSON_VERDICT: alignment judge did not produce "
                "structured output and adjudicator was not available")

    def run_alignment_check_with_retries(
        self,
        section: Section, planspace: Path, codespace: Path,
        output_prefix: str = "align",
        max_retries: int = 2,
        *,
        model: str,
    ) -> str | None:
        """Run an alignment check with TIMEOUT retry logic.

        Dispatches the specified model for an implementation alignment check.
        If the agent times out, retries up to max_retries times. Returns the
        alignment result text, or None if all retries exhausted.
        """
        from dispatch.prompt.writers import Writers as PromptWriters
        from containers import Services

        prompt_writers = PromptWriters(
            task_router=Services.task_router(),
            prompt_guard=Services.prompt_guard(),
            logger=Services.logger(),
            communicator=Services.communicator(),
            section_alignment=Services.section_alignment(),
            artifact_io=Services.artifact_io(),
            cross_section=Services.cross_section(),
            config=Services.config(),
        )

        sec_num = section.number
        paths = PathRegistry(planspace)
        for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
            ctrl = self._pipeline_control.poll_control_messages(
                planspace, current_section=sec_num)
            if ctrl == ControlSignal.ALIGNMENT_CHANGED:
                return ALIGNMENT_CHANGED_PENDING
            align_prompt = prompt_writers.write_impl_alignment_prompt(
                section, planspace, codespace,
            )
            align_output = paths.artifacts / f"{output_prefix}-{sec_num}-output.md"
            result = self._dispatcher.dispatch(
                model, align_prompt, align_output,
                planspace, codespace=codespace,
                section_number=sec_num,
                agent_file=self._task_router.agent_for("staleness.alignment_check"),
            )
            if result == ALIGNMENT_CHANGED_PENDING:
                return ALIGNMENT_CHANGED_PENDING
            if result.status is not DispatchStatus.TIMEOUT:
                # Check for structured JSON verdict from alignment judge
                verdict = _parse_alignment_verdict(result.output)
                if verdict is not None and verdict.get("frame_ok") is False:
                    # Structural failure — the alignment prompt frame was
                    # invalid.  Do NOT retry; surface upward for parent
                    # intervention.  Retrying the same broken frame wastes
                    # cycles without fixing the root cause.
                    self._logger.log(f"  alignment judge reported invalid frame for "
                        f"section {sec_num} — structural failure, "
                        f"requires parent intervention")
                    return ALIGNMENT_INVALID_FRAME
                return result.output
            self._logger.log(f"  alignment check for section {sec_num} timed out "
                f"(attempt {attempt}/{max_retries + 1})")
        return None
