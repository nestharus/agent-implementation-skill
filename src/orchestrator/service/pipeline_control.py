from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from containers import ChangeTrackerService, ConfigService, LogService

from staleness.service.change_tracker import (
    check_pending as alignment_changed_pending_flag,
)
from signals.service.message_poller import (
    check_for_messages as drain_messages,
    handle_pending_messages as handle_messages,
    poll_control_messages as poll_messages,
)
from orchestrator.path_registry import PathRegistry
from orchestrator.service.pipeline_state import (
    PipelineState,
    check_pipeline_state as query_pipeline_state,
)
from staleness.service.input_hasher import section_inputs_hash

_section_inputs_hash = section_inputs_hash


class PipelineControl:
    def __init__(self, config: ConfigService, logger: LogService, change_tracker: ChangeTrackerService) -> None:
        self._config = config
        self._logger = logger
        self._change_tracker = change_tracker
        self._pipeline_state = PipelineState(logger=logger, change_tracker=change_tracker)
        self._parent: str = ""

    def set_parent(self, parent: str) -> None:
        """Set the parent mailbox name (called once at pipeline startup)."""
        self._parent = parent

    def check_pipeline_state(self, planspace: Path) -> str:
        return query_pipeline_state(planspace, db_sh=self._config.db_sh)

    def requeue_changed_sections(
        self,
        completed: set[str], queue: list[str],
        sections_by_num: dict[str, Any], planspace: Path,
        *, current_section: str | None = None,
    ) -> list[str]:
        """Targeted requeue: only requeue completed sections whose inputs changed.

        Compares current input hashes against persisted baselines in
        ``artifacts/section-inputs-hashes/``. Returns the list of section
        numbers that were actually requeued. Always re-adds *current_section*
        to the front of the queue (it was interrupted mid-flight).
        """
        paths = PathRegistry(planspace)
        hash_dir = paths.section_inputs_hashes_dir()
        requeued: list[str] = []
        for done_num in list(completed):
            cur = _section_inputs_hash(
                done_num, planspace, sections_by_num)
            prev_file = hash_dir / f"{done_num}.hash"
            prev = (prev_file.read_text(encoding="utf-8").strip()
                    if prev_file.exists() else "")
            if cur != prev:
                completed.discard(done_num)
                if done_num not in queue:
                    queue.append(done_num)
                requeued.append(done_num)
                prev_file.write_text(cur, encoding="utf-8")
        if current_section and current_section not in queue:
            queue.insert(0, current_section)
        if requeued:
            self._logger.log("Alignment changed — requeuing sections "
                f"with changed inputs: {requeued}")
        else:
            self._logger.log("Alignment changed but no section inputs "
                "differ — skipping requeue")
        return requeued

    def wait_if_paused(self, planspace: Path) -> None:
        """Block if pipeline is paused. Polls until state returns to running.

        Buffers non-abort messages in memory while paused and replays them
        after resume (avoids the re-send-to-self infinite loop).
        """
        cfg = self._config
        self._pipeline_state.wait_if_paused(
            planspace,
            self._parent,
            db_sh=cfg.db_sh,
            agent_name=cfg.agent_name,
        )

    def pause_for_parent(self, planspace: Path, signal: str) -> str:
        """Send a pause signal to parent and block until we get a response."""
        cfg = self._config
        return self._pipeline_state.pause_for_parent(
            planspace,
            self._parent,
            signal,
            db_sh=cfg.db_sh,
            agent_name=cfg.agent_name,
        )

    def poll_control_messages(
        self,
        planspace: Path,
        current_section: str | None = None,
    ) -> str | None:
        """Non-blocking poll for abort / alignment_changed control messages.

        Drains the section-loop mailbox and processes control messages:
        - abort: sends fail:aborted (with section if known), cleans up, exits.
        - alignment_changed: invalidates excerpts, sets flag, returns
          "alignment_changed" so the caller can restart.

        Returns "alignment_changed" if the flag was set, None otherwise.
        Non-control messages are re-queued to our own mailbox (replay).
        """
        cfg = self._config
        return poll_messages(
            planspace,
            current_section,
            db_sh=cfg.db_sh,
            agent_name=cfg.agent_name,
        )

    def check_for_messages(self, planspace: Path) -> list[str]:
        """Non-blocking check for any pending messages."""
        cfg = self._config
        return drain_messages(
            planspace,
            db_sh=cfg.db_sh,
            agent_name=cfg.agent_name,
        )

    def handle_pending_messages(self, planspace: Path) -> bool:
        """Process any pending messages. Returns True if should abort."""
        cfg = self._config
        return handle_messages(
            planspace,
            db_sh=cfg.db_sh,
            agent_name=cfg.agent_name,
        )


# Pure function — no Services usage

def alignment_changed_pending(planspace: Path) -> bool:
    """Check if alignment_changed flag is set (non-clearing)."""
    return alignment_changed_pending_flag(planspace)

