from __future__ import annotations

from pathlib import Path

from _paths import DB_SH
from src.scripts.lib.alignment_change_tracker import (
    check_and_clear,
    check_pending,
    invalidate_excerpts,
    set_flag,
)
from src.scripts.lib.database_client import DatabaseClient


def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts" / "sections").mkdir(parents=True)
    DatabaseClient(DB_SH, planspace / "run.db").execute("init")
    return planspace


def test_set_flag_writes_marker_and_logs_pending_event(tmp_path: Path) -> None:
    planspace = _planspace(tmp_path)

    set_flag(planspace, db_sh=DB_SH, agent_name="section-loop")

    assert check_pending(planspace) is True
    rows = DatabaseClient(DB_SH, planspace / "run.db").query(
        "lifecycle",
        tag="alignment-changed",
        limit=1,
        check=False,
    )
    assert "pending" in rows


def test_check_and_clear_consumes_flag_and_logs_clear(tmp_path: Path) -> None:
    planspace = _planspace(tmp_path)
    set_flag(planspace, db_sh=DB_SH, agent_name="section-loop")

    assert check_and_clear(
        planspace,
        db_sh=DB_SH,
        agent_name="section-loop",
    ) is True
    assert check_pending(planspace) is False

    rows = DatabaseClient(DB_SH, planspace / "run.db").query(
        "lifecycle",
        tag="alignment-changed",
        limit=2,
        check=False,
    )
    assert "cleared" in rows


def test_check_and_clear_returns_false_when_flag_missing(tmp_path: Path) -> None:
    planspace = _planspace(tmp_path)

    assert check_and_clear(
        planspace,
        db_sh=DB_SH,
        agent_name="section-loop",
    ) is False


def test_invalidate_excerpts_deletes_only_excerpt_files(tmp_path: Path) -> None:
    planspace = _planspace(tmp_path)
    sections = planspace / "artifacts" / "sections"
    (sections / "section-01-proposal-excerpt.md").write_text("proposal")
    (sections / "section-01-alignment-excerpt.md").write_text("alignment")
    (sections / "section-01.md").write_text("spec")

    invalidate_excerpts(planspace)

    assert not (sections / "section-01-proposal-excerpt.md").exists()
    assert not (sections / "section-01-alignment-excerpt.md").exists()
    assert (sections / "section-01.md").exists()
