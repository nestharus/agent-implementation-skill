"""Component tests for coordination stall detector."""

from __future__ import annotations

from pathlib import Path

from containers import LogService
from src.coordination.service.stall_detector import (
    STALL_TERMINATION_THRESHOLD,
    StallDetector,
)
from tests.conftest import CapturingCommunicator, StubPolicies
from src.orchestrator.path_registry import PathRegistry


def _make_detector(
    planspace: Path,
    *,
    stall_count_threshold: int = 2,
    communicator: CapturingCommunicator | None = None,
) -> tuple[StallDetector, CapturingCommunicator]:
    """Build a StallDetector with test doubles."""
    if communicator is None:
        communicator = CapturingCommunicator()
    policies = StubPolicies(
        {"escalation_triggers": {"stall_count": stall_count_threshold}}
    )
    detector = StallDetector(
        planspace,
        logger=LogService(),
        policies=policies,
        communicator=communicator,
    )
    return detector, communicator


# -- Stall detection thresholds ------------------------------------------------


def test_no_stall_on_first_update(planspace: Path) -> None:
    """First update has no previous baseline, so stall_count stays 0."""
    detector, _ = _make_detector(planspace)
    detector.update(cur_unresolved=5, round_num=1)
    assert detector.stall_count == 0
    assert not detector.should_terminate


def test_stall_increments_when_unresolved_stays_same(planspace: Path) -> None:
    """Stall counter increases when unresolved count does not decrease."""
    detector, _ = _make_detector(planspace)
    detector.update(cur_unresolved=5, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)
    assert detector.stall_count == 1


def test_stall_increments_when_unresolved_increases(planspace: Path) -> None:
    """Stall counter increases when unresolved count gets worse."""
    detector, _ = _make_detector(planspace)
    detector.update(cur_unresolved=3, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)
    assert detector.stall_count == 1


def test_stall_resets_when_progress_is_made(planspace: Path) -> None:
    """Stall counter resets to 0 when unresolved count decreases."""
    detector, _ = _make_detector(planspace)
    detector.update(cur_unresolved=5, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)
    assert detector.stall_count == 1
    detector.update(cur_unresolved=3, round_num=3)
    assert detector.stall_count == 0


# -- Stall signal emission (escalation) ----------------------------------------


def test_escalation_fires_at_threshold(planspace: Path) -> None:
    """Escalation signal and communicator message when stall_count == threshold."""
    detector, comm = _make_detector(planspace, stall_count_threshold=2)
    detector.update(cur_unresolved=5, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)
    detector.update(cur_unresolved=5, round_num=3)
    # Stall count is now 2 -- exactly at threshold
    assert detector.stall_count == 2
    # Communicator should have received an escalation message
    assert len(comm.messages) == 1
    assert "escalation:coordination:round-3" in comm.messages[0]
    assert "stall_count=2" in comm.messages[0]


def test_escalation_writes_model_file(planspace: Path) -> None:
    """Escalation writes a model-escalation file to the artifacts tree."""
    detector, _ = _make_detector(planspace, stall_count_threshold=2)
    detector.update(cur_unresolved=5, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)
    detector.update(cur_unresolved=5, round_num=3)
    escalation_file = PathRegistry(planspace).coordination_model_escalation()
    assert escalation_file.exists()
    content = escalation_file.read_text(encoding="utf-8")
    assert content == "test-model"


def test_escalation_does_not_fire_before_threshold(planspace: Path) -> None:
    """No escalation when stall_count has not reached the threshold yet."""
    detector, comm = _make_detector(planspace, stall_count_threshold=3)
    detector.update(cur_unresolved=5, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)
    # Only 1 stall so far — below threshold of 3
    assert detector.stall_count == 1
    assert len(comm.messages) == 0


# -- No false positives on active sections ------------------------------------


def test_no_stall_when_unresolved_steadily_decreases(planspace: Path) -> None:
    """A section making progress each round should never trigger stall."""
    detector, comm = _make_detector(planspace)
    for round_num, unresolved in enumerate([10, 8, 6, 4, 2, 0], start=1):
        detector.update(cur_unresolved=unresolved, round_num=round_num)
    assert detector.stall_count == 0
    assert not detector.should_terminate
    assert len(comm.messages) == 0


def test_intermittent_progress_resets_stall(planspace: Path) -> None:
    """One round of progress resets the stall counter even after stalling."""
    detector, _ = _make_detector(planspace, stall_count_threshold=3)
    detector.update(cur_unresolved=5, round_num=1)
    detector.update(cur_unresolved=5, round_num=2)  # stall 1
    detector.update(cur_unresolved=5, round_num=3)  # stall 2
    detector.update(cur_unresolved=4, round_num=4)  # progress! reset
    assert detector.stall_count == 0
    detector.update(cur_unresolved=4, round_num=5)  # stall 1 again
    assert detector.stall_count == 1


# -- Edge cases ---------------------------------------------------------------


def test_set_initial_establishes_baseline(planspace: Path) -> None:
    """set_initial allows the very first update to detect a stall."""
    detector, _ = _make_detector(planspace)
    detector.set_initial(5)
    detector.update(cur_unresolved=5, round_num=1)
    assert detector.stall_count == 1


def test_should_terminate_after_termination_threshold(planspace: Path) -> None:
    """should_terminate is True once stall_count >= STALL_TERMINATION_THRESHOLD."""
    detector, _ = _make_detector(planspace, stall_count_threshold=10)
    detector.set_initial(5)
    for i in range(1, STALL_TERMINATION_THRESHOLD + 1):
        detector.update(cur_unresolved=5, round_num=i)
    assert detector.stall_count == STALL_TERMINATION_THRESHOLD
    assert detector.should_terminate


def test_zero_unresolved_throughout(planspace: Path) -> None:
    """Zero unresolved from the start — stalls because no improvement."""
    detector, _ = _make_detector(planspace)
    detector.set_initial(0)
    detector.update(cur_unresolved=0, round_num=1)
    assert detector.stall_count == 1


def test_single_update_never_terminates(planspace: Path) -> None:
    """A single update can never cause termination."""
    detector, _ = _make_detector(planspace)
    detector.update(cur_unresolved=5, round_num=1)
    assert not detector.should_terminate
