from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService, LogService
from src.orchestrator.path_registry import PathRegistry
from src.intent.service.recurrence_emitter import RecurrenceEmitter


def _make_emitter() -> RecurrenceEmitter:
    return RecurrenceEmitter(
        artifact_io=ArtifactIOService(),
        logger=LogService(),
    )


def test_emit_recurrence_signal_writes_expected_payload(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_emitter().emit_recurrence_signal(planspace, "07", 3)

    signal_path = planspace / "artifacts" / "signals" / "section-07-recurrence.json"
    assert signal_path.exists()
    assert json.loads(signal_path.read_text(encoding="utf-8")) == {
        "section": "07",
        "attempt": 3,
        "recurring": True,
        "escalate_to_coordinator": True,
    }
