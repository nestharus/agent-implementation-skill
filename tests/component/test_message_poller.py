from __future__ import annotations

from pathlib import Path

import pytest

from _paths import DB_SH
from src.signals.service.database_client import DatabaseClient
from src.signals.service.mailbox_service import MailboxService
from src.signals.service.message_poller import (
    check_for_messages,
    handle_pending_messages,
    poll_control_messages,
)


def _db(tmp_path: Path) -> tuple[Path, DatabaseClient]:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts" / "sections").mkdir(parents=True)
    client = DatabaseClient(DB_SH, planspace / "run.db")
    client.execute("init")
    return planspace, client


def _mailbox(client: DatabaseClient, name: str) -> MailboxService:
    mailbox = MailboxService(client, name)
    mailbox.register()
    return mailbox


def test_check_for_messages_drains_pending_mail(tmp_path: Path) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    _mailbox(client, "section-loop")
    parent.send("section-loop", "one")
    parent.send("section-loop", "two")

    assert check_for_messages(
        planspace,
        db_sh=DB_SH,
        agent_name="section-loop",
    ) == ["one", "two"]


def test_poll_control_messages_replays_non_control_messages(tmp_path: Path) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    section_loop = _mailbox(client, "section-loop")
    parent.send("section-loop", "resume:keep")

    result = poll_control_messages(
        planspace,
        "parent",
        db_sh=DB_SH,
        agent_name="section-loop",
    )

    assert result is None
    assert section_loop.drain() == ["resume:keep"]


def test_poll_control_messages_sets_alignment_flag_and_invalidates_excerpts(
    tmp_path: Path,
) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    _mailbox(client, "section-loop")
    excerpts = planspace / "artifacts" / "sections"
    (excerpts / "section-01-alignment-excerpt.md").write_text("alignment")
    parent.send("section-loop", "alignment_changed")

    result = poll_control_messages(
        planspace,
        "parent",
        db_sh=DB_SH,
        agent_name="section-loop",
    )

    assert result == "alignment_changed"
    assert not (excerpts / "section-01-alignment-excerpt.md").exists()
    assert (planspace / "artifacts" / "alignment-changed-pending").exists()


def test_handle_pending_messages_returns_true_for_abort(tmp_path: Path) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    _mailbox(client, "section-loop")
    parent.send("section-loop", "abort")

    assert handle_pending_messages(
        planspace,
        [],
        set(),
        db_sh=DB_SH,
        agent_name="section-loop",
    ) is True


def test_poll_control_messages_exits_on_abort(tmp_path: Path) -> None:
    planspace, client = _db(tmp_path)
    parent = _mailbox(client, "parent")
    _mailbox(client, "section-loop")
    parent.send("section-loop", "abort")

    with pytest.raises(SystemExit):
        poll_control_messages(
            planspace,
            "parent",
            current_section="03",
            db_sh=DB_SH,
            agent_name="section-loop",
        )
