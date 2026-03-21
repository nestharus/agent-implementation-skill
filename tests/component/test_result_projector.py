from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService
from src.flow.engine.result_projector import TaskResultProjector


def _task(**overrides) -> dict:
    task = {
        "id": 7,
        "task_type": "research.synthesis",
        "status": "complete",
        "error": None,
        "output_path": None,
    }
    task.update(overrides)
    return task


def test_projector_parses_structured_json_output(tmp_path: Path) -> None:
    planspace = tmp_path
    output_path = planspace / "artifacts" / "result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "unresolved_problems": ["missing contract"],
                "new_value_axes": ["Latency"],
                "partial_solutions": [{"why_incomplete": "needs follow-up"}],
                "scope_expansions": ["admin-surface"],
            },
        ),
        encoding="utf-8",
    )

    projector = TaskResultProjector(ArtifactIOService())

    envelope = projector.project(
        _task(output_path="artifacts/result.json"),
        "artifacts/result.json",
        planspace,
    )

    assert envelope.unresolved_problems == ["missing contract"]
    assert envelope.new_value_axes == ["Latency"]
    assert envelope.partial_solutions == [{"why_incomplete": "needs follow-up"}]
    assert envelope.scope_expansions == ["admin-surface"]


def test_projector_missing_output_fails_closed_to_empty_envelope(tmp_path: Path) -> None:
    projector = TaskResultProjector(ArtifactIOService())

    envelope = projector.project(
        _task(output_path="artifacts/missing.json"),
        "artifacts/missing.json",
        tmp_path,
    )

    assert envelope.output_path == "artifacts/missing.json"
    assert envelope.unresolved_problems == []
    assert envelope.new_value_axes == []
    assert envelope.error is None


def test_projector_preserves_malformed_json_and_returns_error(tmp_path: Path) -> None:
    planspace = tmp_path
    output_path = planspace / "artifacts" / "bad.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("{bad json", encoding="utf-8")

    projector = TaskResultProjector(ArtifactIOService())

    envelope = projector.project(
        _task(output_path="artifacts/bad.json"),
        "artifacts/bad.json",
        planspace,
    )

    assert envelope.error == "malformed task output"
    assert not output_path.exists()
    assert (planspace / "artifacts" / "bad.malformed.json").exists()
