from __future__ import annotations

from pathlib import Path

import pytest

from src.coordination.engine import plan_executor as executor
from src.coordination.engine.plan_executor import (
    CoordinationExecutionExit,
    execute_coordination_plan,
    read_execution_modified_files,
)
from orchestrator.types import Section


def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts").mkdir(parents=True)
    return planspace


def test_execute_coordination_plan_runs_fix_groups_and_persists_modified_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = _planspace(tmp_path)
    sections_by_num = {
        "01": Section(
            number="01",
            path=planspace / "artifacts" / "section-01.md",
            related_files=["src/a.py"],
        ),
        "02": Section(
            number="02",
            path=planspace / "artifacts" / "section-02.md",
            related_files=["src/b.py"],
        ),
    }

    monkeypatch.setattr(
        executor,
        "poll_control_messages",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        executor,
        "_dispatch_fix_group",
        lambda group, group_index, *args, **kwargs: (group_index, [group[0]["files"][0]]),
    )

    affected_sections = execute_coordination_plan(
        {
            "coord_plan": {
                "groups": [
                    {"problems": [0], "strategy": "sequential", "bridge": {"needed": False}},
                    {"problems": [1], "strategy": "sequential", "bridge": {"needed": False}},
                ],
            },
            "confirmed_groups": [
                [{"section": "01", "files": ["src/a.py"]}],
                [{"section": "02", "files": ["src/b.py"]}],
            ],
        },
        sections_by_num,
        planspace,
        tmp_path / "codespace",
        "parent",
        {"coordination_fix": "fix-model"},
    )

    assert affected_sections == ["01", "02"]
    assert read_execution_modified_files(planspace) == ["src/a.py", "src/b.py"]


def test_execute_coordination_plan_runs_bridge_and_registers_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = _planspace(tmp_path)
    notes_dir = planspace / "artifacts" / "notes"
    notes_dir.mkdir(parents=True)
    sections_by_num = {
        "01": Section(
            number="01",
            path=planspace / "artifacts" / "section-01.md",
            related_files=["src/a.py"],
        ),
        "02": Section(
            number="02",
            path=planspace / "artifacts" / "section-02.md",
            related_files=["src/a.py"],
        ),
    }
    calls = {"dispatch": 0}

    def _dispatch_agent(*args, **kwargs):
        calls["dispatch"] += 1
        notes_dir.joinpath("from-bridge-0-to-01.md").write_text("Note for 01", encoding="utf-8")
        notes_dir.joinpath("from-bridge-0-to-02.md").write_text("Note for 02", encoding="utf-8")
        contract_delta = (
            planspace / "artifacts" / "contracts" / "contract-delta-group-0.md"
        )
        contract_delta.parent.mkdir(parents=True, exist_ok=True)
        contract_delta.write_text("delta", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(
        executor,
        "poll_control_messages",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        executor,
        "dispatch_agent",
        _dispatch_agent,
    )
    monkeypatch.setattr(
        executor,
        "_dispatch_fix_group",
        lambda group, group_index, *args, **kwargs: (group_index, ["src/a.py"]),
    )
    monkeypatch.setattr(
        executor,
        "content_hash",
        lambda payload: "abcdef1234567890",
    )

    affected_sections = execute_coordination_plan(
        {
            "coord_plan": {
                "groups": [
                    {
                        "problems": [0],
                        "strategy": "sequential",
                        "bridge": {"needed": True, "reason": "shared seam"},
                    },
                ],
            },
            "confirmed_groups": [
                [
                    {"section": "01", "files": ["src/a.py"]},
                    {"section": "02", "files": ["src/a.py"]},
                ],
            ],
        },
        sections_by_num,
        planspace,
        tmp_path / "codespace",
        "parent",
        {
            "coordination_fix": "fix-model",
            "coordination_bridge": "bridge-model",
        },
    )

    assert affected_sections == ["01", "02"]
    assert calls["dispatch"] == 1
    assert "Note ID" in (
        notes_dir / "from-bridge-0-to-01.md"
    ).read_text(encoding="utf-8")
    assert (
        planspace / "artifacts" / "inputs" / "section-01" / "contract-delta-group-0.ref"
    ).exists()


def test_execute_coordination_plan_raises_on_fix_group_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = _planspace(tmp_path)
    sections_by_num = {
        "01": Section(number="01", path=planspace / "artifacts" / "section-01.md"),
    }

    monkeypatch.setattr(
        executor,
        "poll_control_messages",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        executor,
        "_dispatch_fix_group",
        lambda *args, **kwargs: (0, None),
    )

    with pytest.raises(CoordinationExecutionExit):
        execute_coordination_plan(
            {
                "coord_plan": {
                    "groups": [
                        {"problems": [0], "strategy": "sequential", "bridge": {"needed": False}},
                    ],
                },
                "confirmed_groups": [
                    [{"section": "01", "files": ["src/a.py"]}],
                ],
            },
            sections_by_num,
            planspace,
            tmp_path / "codespace",
            "parent",
            {"coordination_fix": "fix-model"},
        )
