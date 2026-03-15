from __future__ import annotations

from pathlib import Path

import pytest

from _paths import DB_SH
from containers import Services
from orchestrator.types import PipelineAbortError
from src.orchestrator.path_registry import PathRegistry
from src.signals.service.database_client import DatabaseClient
from src.signals.service.mailbox_service import MailboxService
from src.orchestrator.service import pipeline_state
from src.orchestrator.service.pipeline_state import (
    PipelineState,
    check_pipeline_state,
)


def _make_pipeline_state() -> PipelineState:
    return PipelineState(
        logger=Services.logger(),
        change_tracker=Services.change_tracker(),
    )


def _db(tmp_path: Path) -> tuple[Path, DatabaseClient]:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    client = DatabaseClient(DB_SH, planspace / "run.db")
    client.execute("init")
    return planspace, client


def _mailbox(client: DatabaseClient, name: str) -> MailboxService:
    mailbox = MailboxService(client, name)
    mailbox.register()
    return mailbox


def test_check_pipeline_state_defaults_to_running(tmp_path: Path) -> None:
    planspace, _client = _db(tmp_path)

    assert check_pipeline_state(planspace, db_sh=DB_SH) == "running"


def test_check_pipeline_state_reads_latest_logged_value(tmp_path: Path) -> None:
    planspace, client = _db(tmp_path)
    client.log_event(
        "lifecycle",
        "pipeline-state",
        "paused",
        agent="section-loop",
        check=False,
    )

    assert check_pipeline_state(planspace, db_sh=DB_SH) == "paused"


def test_wait_if_paused_replays_buffered_messages_after_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    section_loop = _mailbox(client, "section-loop")

    parent.send("section-loop", "resume:now")
    states = iter(["paused", "paused", "running"])
    monkeypatch.setattr(
        pipeline_state,
        "check_pipeline_state",
        lambda _planspace, *, db_sh: next(states),
    )

    ps = _make_pipeline_state()
    ps.wait_if_paused(
        planspace,
        "parent",
        db_sh=DB_SH,
        agent_name="section-loop",
    )

    parent_messages = parent.drain()
    replayed = section_loop.drain()
    assert "status:paused" in parent_messages
    assert "status:resumed" in parent_messages
    assert replayed == ["resume:now"]


def test_pause_for_parent_consumes_alignment_changed_before_resume(
    tmp_path: Path,
) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    _mailbox(client, "section-loop")
    excerpts = planspace / "artifacts" / "sections"
    (excerpts / "section-01-proposal-excerpt.md").write_text("proposal")

    parent.send("section-loop", "alignment_changed")
    parent.send("section-loop", "resume:continue")

    ps = _make_pipeline_state()
    response = ps.pause_for_parent(
        planspace,
        "parent",
        "pause:test",
        db_sh=DB_SH,
        agent_name="section-loop",
    )

    assert response == "resume:continue"
    assert not (excerpts / "section-01-proposal-excerpt.md").exists()
    assert (planspace / "artifacts" / "alignment-changed-pending").exists()


def test_pause_for_parent_exits_on_abort(tmp_path: Path) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    _mailbox(client, "section-loop")
    parent.send("section-loop", "abort")

    ps = _make_pipeline_state()
    with pytest.raises(PipelineAbortError):
        ps.pause_for_parent(
            planspace,
            "parent",
            "pause:test",
            db_sh=DB_SH,
            agent_name="section-loop",
        )
