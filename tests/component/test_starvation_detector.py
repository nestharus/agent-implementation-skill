"""Component tests for coordination starvation detection."""

from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService, LogService
from src.coordination.engine.global_coordinator import CoordinationRoundResult
from src.coordination.service.stall_detector import StarvationDetector
from src.orchestrator.path_registry import PathRegistry


def _make_detector(planspace: Path) -> StarvationDetector:
    return StarvationDetector(
        planspace,
        artifact_io=ArtifactIOService(),
        logger=LogService(),
    )


def _round_result(**overrides) -> CoordinationRoundResult:
    data = {
        "all_done": False,
        "problem_count": 1,
        "recurrence": False,
        "groups_built": 0,
        "groups_executed": 0,
        "affected_sections": [],
        "modified_files": [],
    }
    data.update(overrides)
    return CoordinationRoundResult(**data)


def test_starvation_detected_when_round_has_no_runnable_work(
    planspace: Path,
) -> None:
    detector = _make_detector(planspace)
    result = _round_result()

    detector.update(result)

    assert detector.is_starved
    assert detector.observation == result
    observation = json.loads(
        PathRegistry(planspace).coordination_starvation_observation().read_text(
            encoding="utf-8",
        ),
    )
    assert observation["groups_built"] == 0
    assert observation["groups_executed"] == 0
    assert observation["problem_count"] == 1


def test_not_starved_when_round_executes_coordination_work(
    planspace: Path,
) -> None:
    detector = _make_detector(planspace)

    detector.update(
        _round_result(
            groups_built=1,
            groups_executed=1,
            affected_sections=["01"],
            modified_files=["src/main.py"],
        ),
    )

    assert not detector.is_starved
    assert detector.observation is None
    assert not PathRegistry(planspace).coordination_starvation_observation().exists()


def test_recurrence_handling_prevents_starvation(
    planspace: Path,
) -> None:
    detector = _make_detector(planspace)

    detector.update(_round_result(recurrence=True))

    assert not detector.is_starved
    assert detector.observation is None


def test_starvation_observation_clears_after_work_resumes(
    planspace: Path,
) -> None:
    detector = _make_detector(planspace)
    detector.update(_round_result())

    detector.update(
        _round_result(
            groups_built=1,
            groups_executed=1,
            affected_sections=["01"],
        ),
    )

    assert not detector.is_starved
    assert detector.observation is None
    assert not PathRegistry(planspace).coordination_starvation_observation().exists()
