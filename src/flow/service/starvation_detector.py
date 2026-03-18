"""Starvation detection for per-section task chains.

Each section chain tracks its most recent task submission time.
When a section has been blocked longer than a configurable threshold
without progress, a starvation signal is emitted as a coordination
task.

Gap 4 of the fractal pipeline design.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService

DEFAULT_STARVATION_THRESHOLD_SECONDS = 1800  # 30 minutes


def record_chain_submission(
    artifact_io: ArtifactIOService,
    planspace: Path,
    section_number: str,
) -> None:
    """Record a task submission time for a section chain."""
    paths = PathRegistry(planspace)
    submission_path = paths.section_chain_submission(section_number)
    artifact_io.write_json(submission_path, {
        "section": section_number,
        "last_submission_time": time.time(),
    })


def detect_starvation(
    artifact_io: ArtifactIOService,
    planspace: Path,
    section_numbers: list[str],
    threshold_seconds: float = DEFAULT_STARVATION_THRESHOLD_SECONDS,
) -> list[str]:
    """Return section numbers that have been blocked beyond the threshold.

    Checks each section's chain submission timestamp against the
    current time.  Sections without a submission record are skipped
    (they haven't started yet).

    Emits a starvation signal for each starved section.
    """
    paths = PathRegistry(planspace)
    now = time.time()
    starved: list[str] = []

    for sec_num in section_numbers:
        submission_path = paths.section_chain_submission(sec_num)
        if not submission_path.exists():
            continue
        data = artifact_io.read_json(submission_path)
        if not isinstance(data, dict):
            continue
        last_time = data.get("last_submission_time")
        if not isinstance(last_time, (int, float)):
            continue

        elapsed = now - last_time
        if elapsed >= threshold_seconds:
            starved.append(sec_num)
            starvation_signal = {
                "type": "starvation",
                "section": sec_num,
                "elapsed_seconds": elapsed,
                "threshold_seconds": threshold_seconds,
                "detail": (
                    f"Section {sec_num} has been blocked for "
                    f"{elapsed:.0f}s (threshold: {threshold_seconds:.0f}s)"
                ),
            }
            signal_path = paths.starvation_signal(sec_num)
            artifact_io.write_json(signal_path, starvation_signal)

    return starved
