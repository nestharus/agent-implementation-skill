"""Readiness-stage risk bridge.

Produces advisory risk artifacts for sections that fail readiness,
giving visibility into risk decisions even when sections are blocked.
This is additive -- it does not gate anything or affect the existing
post-readiness ROAL pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from risk.repository.serialization import serialize_package
from risk.types import PackageStep, RiskPackage, StepClass

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService


_BLOCKER_TYPE_TO_STEP_CLASS = {
    "blocking_research_questions": StepClass.EXPLORE,
    "unresolved_contracts": StepClass.COORDINATE,
    "unresolved_anchors": StepClass.VERIFY,
    "shared_seam_candidates": StepClass.COORDINATE,
    "user_root_questions": StepClass.STABILIZE,
}


def _blocker_steps_from_blockers(blockers: list[dict]) -> list[PackageStep]:
    """Convert readiness blockers into lightweight PackageStep entries."""
    steps: list[PackageStep] = []
    seen: set[str] = set()

    for blocker in blockers:
        btype = blocker.get("type") or blocker.get("state", "unknown")
        bdesc = blocker.get("description") or blocker.get("detail", "")
        dedup_key = f"{btype}:{bdesc}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        assessment_class = _BLOCKER_TYPE_TO_STEP_CLASS.get(
            btype, StepClass.STABILIZE,
        )
        step_index = len(steps) + 1
        step_id = f"{assessment_class.value}-readiness-{step_index:02d}"
        steps.append(
            PackageStep(
                step_id=step_id,
                assessment_class=assessment_class,
                summary=f"Resolve readiness blocker: {bdesc}" if bdesc else f"Resolve {btype} blocker",
                prerequisites=[],
                expected_outputs=[f"resolved-{btype}"],
                expected_resolutions=[f"{btype} cleared"],
                mutation_surface=[],
                verification_surface=[],
                reversibility="high",
            ),
        )
    return steps


def build_readiness_risk_package(
    section_number: str,
    blockers: list[dict],
) -> RiskPackage | None:
    """Build a lightweight advisory RiskPackage from readiness blockers.

    Returns ``None`` when there are no blockers to convert.
    """
    if not blockers:
        return None

    steps = _blocker_steps_from_blockers(blockers)
    if not steps:
        return None

    scope = f"section-{section_number}"
    return RiskPackage(
        package_id=f"pkg-readiness-{scope}",
        layer="readiness",
        scope=scope,
        origin_problem_id=f"{scope}:readiness-blockers",
        origin_source="readiness-gate",
        steps=steps,
    )


class ReadinessRiskBridge:
    """Persists advisory risk packages for blocked sections."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        logger: LogService,
    ) -> None:
        self._artifact_io = artifact_io
        self._logger = logger

    def persist_readiness_risk(
        self,
        section_number: str,
        blockers: list[dict],
        planspace: Path,
    ) -> Path | None:
        """Build and persist a readiness-stage risk package.

        Returns the artifact path on success, or ``None`` if there are
        no blockers to convert.
        """
        package = build_readiness_risk_package(section_number, blockers)
        if package is None:
            return None

        paths = PathRegistry(planspace)
        risk_dir = paths.risk_dir()
        risk_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = risk_dir / f"section-{section_number}-readiness-risk.json"
        self._artifact_io.write_json(artifact_path, serialize_package(package))

        self._logger.log(
            f"Section {section_number}: persisted readiness-stage risk package "
            f"({len(package.steps)} blocker steps) to {artifact_path}"
        )
        return artifact_path
