"""Problem-frame validation gate for section-loop runner."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        HasherService,
        LogService,
        ModelPolicyService,
        TaskRouterService,
    )

from proposal.repository.excerpts import EXCERPT_PROPOSAL, exists as excerpt_exists
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.writers import write_section_setup_prompt
from signals.service.blocker_manager import update_blocker_rollup
from implementation.service.section_reexplorer import write_alignment_surface
from orchestrator.types import PauseType, Section
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import SIGNAL_NEEDS_PARENT


class ProblemFrameGate:
    def __init__(
        self,
        logger: LogService,
        policies: ModelPolicyService,
        dispatcher: AgentDispatcher,
        task_router: TaskRouterService,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        hasher: HasherService,
    ) -> None:
        self._logger = logger
        self._policies = policies
        self._dispatcher = dispatcher
        self._task_router = task_router
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._hasher = hasher

    def validate_problem_frame(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
    ) -> str | None:
        """Ensure the problem frame exists, has content, and is tracked."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        problem_frame_path = paths.problem_frame(section.number)
        if not problem_frame_path.exists():
            self._logger.log(f"Section {section.number}: problem frame missing — retrying setup once")
            retry_prompt = write_section_setup_prompt(
                section,
                planspace,
                codespace,
                section.global_proposal_path,
                section.global_alignment_path,
            )
            retry_output = paths.artifacts / f"setup-{section.number}-retry-output.md"
            retry_result = self._dispatcher.dispatch(
                policy["setup"],
                retry_prompt,
                retry_output,
                planspace,
                f"setup-{section.number}-retry",
                codespace=codespace,
                section_number=section.number,
                agent_file=self._task_router.agent_for("proposal.section_setup"),
            )
            if retry_result == ALIGNMENT_CHANGED_PENDING:
                return None

        if not problem_frame_path.exists():
            self._emit_missing_frame_blocker(planspace, section)
            return None

        pf_content = problem_frame_path.read_text(encoding="utf-8").strip()
        if not pf_content:
            self._emit_empty_frame_blocker(planspace, section)
            return None

        self._validate_frame_content(planspace, section, problem_frame_path)
        return "ok"

    def _emit_missing_frame_blocker(
        self,
        planspace: Path,
        section: Section,
    ) -> None:
        """Signal that the problem frame is still missing after retry."""
        self._logger.log(
            f"Section {section.number}: problem frame still missing after retry "
            "— emitting needs_parent signal",
        )
        self._write_problem_frame_signal(
            PathRegistry(planspace).setup_signal(section.number),
            {
                "state": SIGNAL_NEEDS_PARENT,
                "detail": (
                    f"Setup agent failed to create problem frame for section "
                    f"{section.number} after 2 attempts. The pipeline requires "
                    f"a problem frame before integration work can begin."
                ),
                "needs": (
                    "Parent must either provide a problem frame or resolve "
                    "why the setup agent cannot produce one."
                ),
                "why_blocked": (
                    "Problem frame is a mandatory gate — without it, the "
                    "pipeline cannot validate that the section is solving the "
                    "right problem."
                ),
            },
        )
        update_blocker_rollup(planspace)
        self._communicator.send_to_parent(
            planspace,
            f"pause:{PauseType.NEEDS_PARENT}:{section.number}:problem frame missing after retry",
        )

    def _emit_empty_frame_blocker(
        self,
        planspace: Path,
        section: Section,
    ) -> None:
        """Signal that the problem frame exists but is empty."""
        self._logger.log(f"Section {section.number}: problem frame is empty")
        self._write_problem_frame_signal(
            PathRegistry(planspace).setup_signal(section.number),
            {
                "state": SIGNAL_NEEDS_PARENT,
                "detail": (
                    f"Problem frame for section {section.number} exists but is empty"
                ),
                "needs": (
                    "Parent must ensure the setup agent produces a non-empty "
                    "problem frame."
                ),
                "why_blocked": "Empty problem frame cannot validate problem understanding",
            },
        )
        update_blocker_rollup(planspace)
        self._communicator.send_to_parent(
            planspace,
            f"pause:{PauseType.NEEDS_PARENT}:{section.number}:problem frame empty",
        )

    def _validate_frame_content(
        self,
        planspace: Path,
        section: Section,
        problem_frame_path: Path,
    ) -> None:
        """Hash-check the problem frame, invalidate stale proposals, and track excerpts."""
        paths = PathRegistry(planspace)
        self._logger.log(f"Section {section.number}: problem frame present and validated")
        pf_hash_path = paths.problem_frame_hash(section.number)
        pf_hash_path.parent.mkdir(parents=True, exist_ok=True)
        current_pf_hash = self._hasher.file_hash(problem_frame_path)
        if pf_hash_path.exists():
            prev_pf_hash = pf_hash_path.read_text(encoding="utf-8").strip()
            if prev_pf_hash != current_pf_hash:
                self._logger.log(
                    f"Section {section.number}: problem frame changed — forcing "
                    "integration proposal re-run",
                )
                existing_proposal = paths.proposal(section.number)
                if existing_proposal.exists():
                    existing_proposal.unlink()
                    self._logger.log(
                        f"Section {section.number}: invalidated existing integration "
                        "proposal due to problem frame change",
                    )
        pf_hash_path.write_text(current_pf_hash, encoding="utf-8")

        if (
            excerpt_exists(planspace, section.number, EXCERPT_PROPOSAL)
            and excerpt_exists(planspace, section.number, "alignment")
        ):
            self._logger.log(f"Section {section.number}: setup — excerpts ready")
            self._communicator.record_traceability(
                planspace,
                section.number,
                f"section-{section.number}-proposal-excerpt.md",
                str(section.global_proposal_path),
                "excerpt extraction from global proposal",
            )
            self._communicator.record_traceability(
                planspace,
                section.number,
                f"section-{section.number}-alignment-excerpt.md",
                str(section.global_alignment_path),
                "excerpt extraction from global alignment",
            )
            write_alignment_surface(planspace, section)

    def _write_problem_frame_signal(self, signal_path: Path, payload: dict) -> None:
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        self._artifact_io.write_json(signal_path, payload)


# Backward-compat wrappers

def _get_problem_frame_gate() -> ProblemFrameGate:
    from containers import Services
    return ProblemFrameGate(
        logger=Services.logger(),
        policies=Services.policies(),
        dispatcher=Services.dispatcher(),
        task_router=Services.task_router(),
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        hasher=Services.hasher(),
    )


def validate_problem_frame(
    section: Section,
    planspace: Path,
    codespace: Path,
) -> str | None:
    """Ensure the problem frame exists, has content, and is tracked."""
    return _get_problem_frame_gate().validate_problem_frame(
        section, planspace, codespace,
    )
