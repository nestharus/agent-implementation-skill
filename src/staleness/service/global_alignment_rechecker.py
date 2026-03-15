"""Phase 2 global alignment recheck helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.types import CoordinationStatus
from orchestrator.path_registry import PathRegistry
from staleness.service.section_alignment_checker import (
    SectionAlignmentChecker,
)
from coordination.service.completion_handler import CompletionHandler
from implementation.service.impact_analyzer import ImpactAnalyzer
from orchestrator.types import Section, SectionResult, ControlSignal
from signals.types import ALIGNMENT_INVALID_FRAME
from dispatch.types import ALIGNMENT_CHANGED_PENDING

if TYPE_CHECKING:
    from containers import (
        Communicator,
        DispatchHelperService,
        LogService,
        ModelPolicyService,
        PipelineControlService,
    )


def _update_result(
    section_results: dict[str, SectionResult],
    sec_num: str,
    *,
    aligned: bool,
    problems: str | None = None,
) -> None:
    """Update section_results preserving modified_files from any prior result."""
    prior = section_results.get(sec_num, SectionResult(sec_num))
    section_results[sec_num] = SectionResult(
        section_number=sec_num,
        aligned=aligned,
        problems=problems,
        modified_files=prior.modified_files,
    )


class GlobalAlignmentRechecker:
    """Phase 2 global alignment recheck across all sections.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        logger: LogService,
        policies: ModelPolicyService,
        pipeline_control: PipelineControlService,
        communicator: Communicator,
        dispatch_helpers: DispatchHelperService,
        alignment_checker: SectionAlignmentChecker,
        completion_handler: CompletionHandler,
    ) -> None:
        self._logger = logger
        self._policies = policies
        self._pipeline_control = pipeline_control
        self._communicator = communicator
        self._dispatch_helpers = dispatch_helpers
        self._alignment_checker = alignment_checker
        self._completion_handler = completion_handler

    def _recheck_section(
        self,
        section: Section,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        planspace: Path,
        codespace: Path,
    ) -> str | None:
        """Recheck a single section's alignment. Returns a CoordinationStatus to abort, or None to continue."""
        sec_num = section.number
        paths = PathRegistry(planspace)
        policy = self._policies.load(planspace)
        cur_hash = self._pipeline_control.section_inputs_hash(
            sec_num, planspace, sections_by_num,
        )
        prev_hash_file = paths.phase2_input_hash(sec_num)
        prev_hash = (
            prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash_file.exists()
            else ""
        )
        prev_result = section_results.get(sec_num)
        if prev_hash == cur_hash and prev_result and prev_result.aligned:
            self._logger.log(
                f"Section {sec_num}: inputs unchanged since ALIGNED — skipping "
                "Phase 2 recheck",
            )
            return None
        prev_hash_file.write_text(cur_hash, encoding="utf-8")

        ctrl = self._pipeline_control.poll_control_messages(planspace, sec_num)
        if ctrl == ControlSignal.ALIGNMENT_CHANGED:
            self._logger.log("Alignment changed during Phase 2 — restarting from Phase 1")
            return CoordinationStatus.RESTART_PHASE1

        notes = self._completion_handler.read_incoming_notes(section, planspace, codespace)
        if notes:
            self._logger.log(f"Section {sec_num}: has incoming notes for global alignment check")

        align_result = self._alignment_checker.run_alignment_check_with_retries(
            section, planspace, codespace,
            output_prefix="global-align",
            model=self._policies.resolve(policy, "alignment"),
        )
        if align_result == ALIGNMENT_CHANGED_PENDING:
            return CoordinationStatus.RESTART_PHASE1
        if align_result == ALIGNMENT_INVALID_FRAME:
            self._logger.log(
                f"Section {sec_num}: invalid alignment frame — requires parent intervention",
            )
            self._communicator.send_to_parent(planspace, f"fail:invalid_alignment_frame:{sec_num}")
            _update_result(section_results, sec_num, aligned=False,
                           problems="invalid alignment frame — requires parent intervention")
            return None
        if align_result is None:
            self._logger.log(f"Section {sec_num}: global alignment check timed out after retries")
            _update_result(section_results, sec_num, aligned=False,
                           problems="alignment check timed out after retries")
            return None

        self._apply_alignment_outcome(
            align_result, sec_num, planspace, codespace,
            section_results,
        )
        return None

    def _apply_alignment_outcome(
        self,
        align_result: str,
        sec_num: str,
        planspace: Path,
        codespace: Path,
        section_results: dict[str, SectionResult],
    ) -> None:
        """Extract problems and signals from alignment output, update results."""
        paths = PathRegistry(planspace)
        policy = self._policies.load(planspace)
        global_align_output = paths.artifacts / f"global-align-{sec_num}-output.md"
        problems = self._alignment_checker.extract_problems(
            align_result, output_path=global_align_output,
            planspace=planspace, codespace=codespace,
            adjudicator_model=self._policies.resolve(policy, "adjudicator"),
        )
        main_signal_dir = paths.signals_dir()
        signal, detail = self._dispatch_helpers.check_agent_signals(
            signal_path=main_signal_dir / f"global-align-{sec_num}-signal.json",
        )

        if problems is None and signal is None:
            _update_result(section_results, sec_num, aligned=True)
            return

        self._logger.log(f"Section {sec_num}: global alignment found problems")
        combined = problems or ""
        if signal:
            combined += f"\n[signal:{signal}] {detail}" if combined else f"[signal:{signal}] {detail}"
        _update_result(section_results, sec_num, aligned=False, problems=combined or None)

    def run_global_alignment_recheck(
        self,
        sections_by_num: dict[str, Section],
        section_results: dict[str, SectionResult],
        planspace: Path,
        codespace: Path,
    ) -> str:
        """Refresh per-section alignment results for Phase 2."""
        paths = PathRegistry(planspace)
        self._logger.log("=== Phase 2: global coordination ===")
        self._logger.log("Re-checking alignment across all sections...")

        phase2_hash_dir = paths.phase2_inputs_hashes_dir()

        for section in sections_by_num.values():
            abort_status = self._recheck_section(
                section, section_results, sections_by_num,
                planspace, codespace,
            )
            if abort_status is not None:
                return abort_status

        misaligned = [result for result in section_results.values() if not result.aligned]
        return CoordinationStatus.ALL_ALIGNED if not misaligned else CoordinationStatus.HAS_PROBLEMS


# --- Backward-compat wrappers (used by tests) ---
# Uses module-level references (_run_alignment_check_with_retries,
# read_incoming_notes) so they remain monkeypatchable by tests.


def _compat_recheck_section(
    section: Section,
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
) -> str | None:
    """Backward-compat single-section recheck using module-level references."""
    from containers import Services

    sec_num = section.number
    paths = PathRegistry(planspace)
    policy = Services.policies().load(planspace)
    cur_hash = Services.pipeline_control().section_inputs_hash(
        sec_num, planspace, sections_by_num,
    )
    prev_hash_file = paths.phase2_input_hash(sec_num)
    prev_hash = (
        prev_hash_file.read_text(encoding="utf-8").strip()
        if prev_hash_file.exists()
        else ""
    )
    prev_result = section_results.get(sec_num)
    if prev_hash == cur_hash and prev_result and prev_result.aligned:
        Services.logger().log(
            f"Section {sec_num}: inputs unchanged since ALIGNED — skipping "
            "Phase 2 recheck",
        )
        return None
    prev_hash_file.write_text(cur_hash, encoding="utf-8")

    ctrl = Services.pipeline_control().poll_control_messages(planspace, sec_num)
    if ctrl == ControlSignal.ALIGNMENT_CHANGED:
        Services.logger().log("Alignment changed during Phase 2 — restarting from Phase 1")
        return CoordinationStatus.RESTART_PHASE1

    _ch = CompletionHandler(
        artifact_io=Services.artifact_io(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
        hasher=Services.hasher(),
        impact_analyzer=ImpactAnalyzer(
            communicator=Services.communicator(),
            config=Services.config(),
            context_assembly=Services.context_assembly(),
            cross_section=Services.cross_section(),
            dispatcher=Services.dispatcher(),
            logger=Services.logger(),
            policies=Services.policies(),
            prompt_guard=Services.prompt_guard(),
            task_router=Services.task_router(),
        ),
        logger=Services.logger(),
    )
    notes = _ch.read_incoming_notes(section, planspace, codespace)
    if notes:
        Services.logger().log(f"Section {sec_num}: has incoming notes for global alignment check")

    checker = Services.section_alignment()._get_checker()
    align_result = checker.run_alignment_check_with_retries(
        section, planspace, codespace,
        output_prefix="global-align",
        model=Services.policies().resolve(policy, "alignment"),
    )
    if align_result == ALIGNMENT_CHANGED_PENDING:
        return CoordinationStatus.RESTART_PHASE1
    if align_result == ALIGNMENT_INVALID_FRAME:
        Services.logger().log(
            f"Section {sec_num}: invalid alignment frame — requires parent intervention",
        )
        Services.communicator().send_to_parent(planspace, f"fail:invalid_alignment_frame:{sec_num}")
        _update_result(section_results, sec_num, aligned=False,
                       problems="invalid alignment frame — requires parent intervention")
        return None
    if align_result is None:
        Services.logger().log(f"Section {sec_num}: global alignment check timed out after retries")
        _update_result(section_results, sec_num, aligned=False,
                       problems="alignment check timed out after retries")
        return None

    _compat_apply_alignment_outcome(
        align_result, sec_num, planspace, codespace,
        section_results,
    )
    return None


def _compat_apply_alignment_outcome(
    align_result,
    sec_num: str,
    planspace: Path,
    codespace: Path,
    section_results: dict[str, SectionResult],
) -> None:
    """Backward-compat alignment outcome using module-level references."""
    from containers import Services

    paths = PathRegistry(planspace)
    policy = Services.policies().load(planspace)
    global_align_output = paths.artifacts / f"global-align-{sec_num}-output.md"
    checker = Services.section_alignment()._get_checker()
    problems = checker.extract_problems(
        align_result, output_path=global_align_output,
        planspace=planspace, codespace=codespace,
        adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
    )
    main_signal_dir = paths.signals_dir()
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        signal_path=main_signal_dir / f"global-align-{sec_num}-signal.json",
    )

    if problems is None and signal is None:
        _update_result(section_results, sec_num, aligned=True)
        return

    Services.logger().log(f"Section {sec_num}: global alignment found problems")
    combined = problems or ""
    if signal:
        combined += f"\n[signal:{signal}] {detail}" if combined else f"[signal:{signal}] {detail}"
    _update_result(section_results, sec_num, aligned=False, problems=combined or None)


def run_global_alignment_recheck(
    sections_by_num: dict[str, Section],
    section_results: dict[str, SectionResult],
    planspace: Path,
    codespace: Path,
) -> str:
    """Backward-compat wrapper using module-level references so tests can
    monkeypatch ``_run_alignment_check_with_retries`` and
    ``read_incoming_notes``.
    """
    from containers import Services

    paths = PathRegistry(planspace)
    Services.logger().log("=== Phase 2: global coordination ===")
    Services.logger().log("Re-checking alignment across all sections...")

    phase2_hash_dir = paths.phase2_inputs_hashes_dir()

    for section in sections_by_num.values():
        abort_status = _compat_recheck_section(
            section, section_results, sections_by_num,
            planspace, codespace,
        )
        if abort_status is not None:
            return abort_status

    misaligned = [result for result in section_results.values() if not result.aligned]
    return CoordinationStatus.ALL_ALIGNED if not misaligned else CoordinationStatus.HAS_PROBLEMS
