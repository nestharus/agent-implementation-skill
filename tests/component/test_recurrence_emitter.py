from __future__ import annotations

import json
from pathlib import Path

from src.intent.service.recurrence_emitter import emit_recurrence_signal


def test_emit_recurrence_signal_writes_expected_payload(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts" / "signals").mkdir(parents=True)

    emit_recurrence_signal(planspace, "07", 3)

    signal_path = planspace / "artifacts" / "signals" / "section-07-recurrence.json"
    assert signal_path.exists()
    assert json.loads(signal_path.read_text(encoding="utf-8")) == {
        "section": "07",
        "attempt": 3,
        "recurring": True,
        "escalate_to_coordinator": True,
    }
