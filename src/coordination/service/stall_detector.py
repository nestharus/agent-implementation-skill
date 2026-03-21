"""Observation-based starvation detection for adaptive coordination loops."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService
    from coordination.engine.global_coordinator import CoordinationRoundResult


class StarvationDetector:
    """Detects when a coordination round has no runnable work.

    The detector is observational rather than count-based. It captures
    the latest round snapshot and persists an observation artifact only
    when the round produced no runnable coordination work.
    """

    def __init__(
        self,
        planspace: Path,
        *,
        artifact_io: ArtifactIOService,
        logger: LogService,
    ) -> None:
        self._paths = PathRegistry(planspace)
        self._artifact_io = artifact_io
        self._logger = logger
        self._observation: CoordinationRoundResult | None = None

    def update(self, round_result: CoordinationRoundResult) -> None:
        """Record the latest coordination round snapshot."""
        observation_path = self._paths.coordination_starvation_observation()
        if self._is_starvation_round(round_result):
            self._observation = round_result
            self._artifact_io.write_json(observation_path, asdict(round_result))
            self._logger.log(
                "Coordination starvation observed — no runnable work this round",
            )
            return

        self._observation = None
        if observation_path.exists():
            observation_path.unlink()

    @property
    def is_starved(self) -> bool:
        return self._observation is not None

    @property
    def observation(self) -> CoordinationRoundResult | None:
        return self._observation

    @staticmethod
    def _is_starvation_round(round_result: CoordinationRoundResult) -> bool:
        return (
            not round_result.all_done
            and round_result.groups_built == 0
            and round_result.groups_executed == 0
            and not round_result.recurrence
            and not round_result.affected_sections
            and not round_result.modified_files
        )
