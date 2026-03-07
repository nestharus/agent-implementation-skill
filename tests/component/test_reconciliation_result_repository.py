"""Component tests for reconciliation result persistence."""

from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.repositories.reconciliation_result_repository import (
    load_result,
    was_section_affected,
    write_result,
    write_scope_delta,
    write_substrate_trigger,
)


def test_write_result_and_load_result_round_trip(tmp_path: Path) -> None:
    write_result(tmp_path, "03", {"section": "03", "affected": True})

    assert load_result(tmp_path, "03") == {"section": "03", "affected": True}
    assert was_section_affected(tmp_path, "03") is True


def test_load_result_renames_non_object_json(tmp_path: Path) -> None:
    result_path = (
        tmp_path
        / "artifacts"
        / "reconciliation"
        / "section-04-reconciliation-result.json"
    )
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps(["bad"]), encoding="utf-8")

    assert load_result(tmp_path, "04") is None
    assert not result_path.exists()
    assert result_path.with_suffix(".malformed.json").exists()
    assert was_section_affected(tmp_path, "04") is False


def test_write_scope_delta_preserves_adjudicated_flag(tmp_path: Path) -> None:
    path = write_scope_delta(
        tmp_path,
        {
            "title": "Shared cache subsystem",
            "source_sections": ["01", "02"],
            "candidates": [{"section": "01", "candidate": "Shared cache subsystem"}],
            "adjudicated": True,
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "reconciliation"
    assert payload["source_sections"] == ["01", "02"]
    assert payload["adjudicated"] is True
    assert payload["delta_id"].startswith("delta-recon-01-02-")


def test_write_substrate_trigger_writes_signal_payload(tmp_path: Path) -> None:
    path = write_substrate_trigger(
        tmp_path,
        {"seam": "shared auth", "sections": ["01", "05"]},
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "source": "reconciliation",
        "seam": "shared auth",
        "sections": ["01", "05"],
        "trigger_type": "shared_seam_reconciliation",
    }
