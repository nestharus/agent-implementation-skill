"""Stall detection for adaptive coordination loops.

Tracks progress across rounds and triggers model escalation when
the coordination loop stops making progress.
"""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry


class StallDetector:
    """Detects when a coordination loop stops making progress.

    Tracks unresolved counts across rounds and escalates (writes a
    model-escalation signal) when progress stalls for too long.
    """

    def __init__(
        self,
        planspace: Path,
        parent: str,
        policy: dict,
    ) -> None:
        self._planspace = planspace
        self._parent = parent
        self._policy = policy
        self._paths = PathRegistry(planspace)
        self._stall_count = 0
        self._prev_unresolved: int | None = None
        self._escalation_threshold = policy.get(
            "escalation_triggers", {},
        ).get("stall_count", 2)

    def update(self, cur_unresolved: int, round_num: int) -> None:
        """Record progress for the current round.

        Compares against the previous round's unresolved count.
        If no improvement, increments the stall counter and may
        trigger model escalation.
        """
        if self._prev_unresolved is not None:
            if cur_unresolved >= self._prev_unresolved:
                self._stall_count += 1
                if self._stall_count == self._escalation_threshold:
                    self._escalate(round_num)
            else:
                self._stall_count = 0
        self._prev_unresolved = cur_unresolved

    @property
    def should_terminate(self) -> bool:
        """True when the loop has stalled for 3+ consecutive rounds."""
        return self._stall_count >= 3

    @property
    def stall_count(self) -> int:
        return self._stall_count

    def set_initial(self, unresolved: int) -> None:
        """Set the initial unresolved count (before first round)."""
        self._prev_unresolved = unresolved

    def _escalate(self, round_num: int) -> None:
        """Write model-escalation signal and notify parent."""
        Services.logger().log(
            f"Coordination churning ({self._stall_count} rounds without "
            "improvement) — escalating model",
        )
        escalation_file = (
            self._paths.coordination_dir() / "model-escalation.txt"
        )
        escalation_file.parent.mkdir(parents=True, exist_ok=True)
        escalation_file.write_text(
            Services.policies().resolve(self._policy, "escalation_model"),
            encoding="utf-8",
        )
        Services.communicator().mailbox_send(
            self._planspace,
            self._parent,
            f"escalation:coordination:round-{round_num}:"
            f"stall_count={self._stall_count}",
        )
