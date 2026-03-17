"""ROAL-scoped verification chain builder.

Builds the list of ``TaskSpec`` objects to submit via the flow system
after implementation completes for a section.  The chain is scoped by the
section's ROAL posture profile so that low-risk sections get lightweight
verification and high-risk sections get comprehensive checking.

Posture rules (from design doc Resolution Item 6):

* **P0 (minimal)**: structural verification with ``scope=imports_only``.
* **P1 (relaxed)**: structural (``scope=full``); behavioral testing only
  when the section has incoming consequence notes (2-test cap).
* **P2 (standard)**: structural + integration (consequence-note-scoped)
  + behavioral (5-test cap).
* **P3 (guarded)**: structural + integration (``scope=expanded``) +
  behavioral (5 tests, risk-aware) + codemap refresh flag.
* **P4 (locked)**: empty list -- section reopens to proposal.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.repository.notes import list_notes_to
from flow.types.schema import TaskSpec
from orchestrator.path_registry import PathRegistry
from risk.types import PostureProfile
from verification.service.verification_context import write_verification_context

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService

_POSTURE_PRIORITY = {
    PostureProfile.P0_DIRECT: "low",
    PostureProfile.P1_LIGHT: "normal",
    PostureProfile.P2_STANDARD: "normal",
    PostureProfile.P3_GUARDED: "high",
}

_P1_TEST_CAP = 2
_P2_TEST_CAP = 5


def _resolve_posture(raw: str) -> PostureProfile:
    """Normalise a posture string to a ``PostureProfile`` enum member."""
    for member in PostureProfile:
        if raw == member.value or raw == member.name:
            return member
    return PostureProfile.P2_STANDARD


class VerificationChainBuilder:
    """Builds posture-gated verification task chains."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        logger: LogService,
    ) -> None:
        self._artifact_io = artifact_io
        self._logger = logger

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_verification_chain(
        self,
        section_number: str,
        planspace: Path,
        roal_posture: str,
        has_incoming_consequence_notes: bool,
    ) -> list[TaskSpec]:
        """Return the ordered task chain for post-implementation verification.

        Parameters
        ----------
        section_number:
            The section that just completed implementation.
        planspace:
            Root planspace directory.
        roal_posture:
            Posture string (``"P0"`` .. ``"P4"``) from the ROAL plan.
        has_incoming_consequence_notes:
            Whether other sections wrote consequence notes targeting
            this section.
        """
        posture = _resolve_posture(roal_posture)

        if posture == PostureProfile.P4_REOPEN:
            self._logger.log(
                f"[verification] section {section_number}: P4 posture "
                f"-- no verification chain (section reopens to proposal)"
            )
            return []

        paths = PathRegistry(planspace)
        concern = f"section-{section_number}"
        priority = _POSTURE_PRIORITY.get(posture, "normal")
        note_paths = self._consequence_note_paths(paths, section_number)
        risk_ctx_path = self._risk_context_path(paths, section_number, posture)

        chain: list[TaskSpec] = []

        # ---- Structural verification (P0-P3) ----
        chain.append(
            self._structural_task(
                section_number, planspace, concern, priority, posture,
            )
        )

        # ---- Integration verification (P2+) ----
        if posture.rank >= PostureProfile.P2_STANDARD.rank:
            chain.append(
                self._integration_task(
                    section_number, planspace, concern, priority,
                    posture, note_paths,
                )
            )

        # ---- Behavioral testing ----
        if self._should_include_behavioral(
            posture, has_incoming_consequence_notes,
        ):
            chain.append(
                self._behavioral_task(
                    section_number, planspace, concern, priority,
                    posture, note_paths, risk_ctx_path,
                )
            )

        self._logger.log(
            f"[verification] section {section_number}: "
            f"posture={posture.value}, chain length={len(chain)}"
        )
        return chain

    # ------------------------------------------------------------------
    # Task builders (private)
    # ------------------------------------------------------------------

    def _structural_task(
        self,
        section_number: str,
        planspace: Path,
        concern: str,
        priority: str,
        posture: PostureProfile,
    ) -> TaskSpec:
        scope = "imports_only" if posture == PostureProfile.P0_DIRECT else "full"
        ctx_path = write_verification_context(
            self._artifact_io,
            planspace,
            section_number,
            task_type="structural",
            scope=scope,
        )
        return TaskSpec(
            task_type="verification.structural",
            concern_scope=concern,
            priority=priority,
            payload_path=str(ctx_path),
        )

    def _integration_task(
        self,
        section_number: str,
        planspace: Path,
        concern: str,
        priority: str,
        posture: PostureProfile,
        note_paths: list[str],
    ) -> TaskSpec:
        scope = (
            "expanded"
            if posture.rank >= PostureProfile.P3_GUARDED.rank
            else "consequence_notes"
        )
        ctx_path = write_verification_context(
            self._artifact_io,
            planspace,
            section_number,
            task_type="integration",
            scope=scope,
            consequence_note_paths=note_paths or None,
        )
        return TaskSpec(
            task_type="verification.integration",
            concern_scope=concern,
            priority=priority,
            payload_path=str(ctx_path),
        )

    def _behavioral_task(
        self,
        section_number: str,
        planspace: Path,
        concern: str,
        priority: str,
        posture: PostureProfile,
        note_paths: list[str],
        risk_ctx_path: str | None,
    ) -> TaskSpec:
        max_tests = (
            _P1_TEST_CAP
            if posture == PostureProfile.P1_LIGHT
            else _P2_TEST_CAP
        )
        codemap_refresh = posture.rank >= PostureProfile.P3_GUARDED.rank
        ctx_path = write_verification_context(
            self._artifact_io,
            planspace,
            section_number,
            task_type="behavioral",
            scope="full",
            consequence_note_paths=note_paths or None,
            risk_context_path=risk_ctx_path if posture.rank >= PostureProfile.P3_GUARDED.rank else None,
            codemap_refresh=codemap_refresh,
            max_tests=max_tests,
        )
        return TaskSpec(
            task_type="testing.behavioral",
            concern_scope=concern,
            priority=priority,
            payload_path=str(ctx_path),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _should_include_behavioral(
        posture: PostureProfile,
        has_incoming_consequence_notes: bool,
    ) -> bool:
        """Decide whether behavioral testing is included in the chain."""
        if posture.rank >= PostureProfile.P2_STANDARD.rank:
            return True
        if posture == PostureProfile.P1_LIGHT and has_incoming_consequence_notes:
            return True
        return False

    @staticmethod
    def _consequence_note_paths(
        paths: PathRegistry, section_number: str,
    ) -> list[str]:
        """Collect string paths for notes targeting this section."""
        return [str(p) for p in list_notes_to(paths, section_number)]

    @staticmethod
    def _risk_context_path(
        paths: PathRegistry, section_number: str, posture: PostureProfile,
    ) -> str | None:
        """Return the risk assessment path for P3+ sections, else None."""
        if posture.rank >= PostureProfile.P3_GUARDED.rank:
            risk_path = paths.risk_assessment(f"section-{section_number}")
            if risk_path.exists():
                return str(risk_path)
        return None
