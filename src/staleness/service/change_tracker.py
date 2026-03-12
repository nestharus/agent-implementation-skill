"""Alignment change flag and excerpt invalidation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from signals.service.database_client import DatabaseClient
from proposal.repository.excerpts import invalidate_all
from orchestrator.path_registry import PathRegistry


def set_flag(planspace: Path, *, db_sh: Path, agent_name: str) -> None:
    """Persist the alignment-changed flag and record a lifecycle event."""
    flag = PathRegistry(planspace).alignment_changed_flag()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1", encoding="utf-8")
    DatabaseClient.for_planspace(planspace, db_sh).log_event(
        "lifecycle",
        "alignment-changed",
        "pending",
        agent=agent_name,
        check=False,
    )


def check_pending(planspace: Path) -> bool:
    """Return whether the alignment-changed flag is currently set."""
    return PathRegistry(planspace).alignment_changed_flag().exists()


def check_and_clear(planspace: Path, *, db_sh: Path, agent_name: str) -> bool:
    """Atomically consume the alignment-changed flag when present."""
    flag = PathRegistry(planspace).alignment_changed_flag()
    if not flag.exists():
        return False
    flag.unlink(missing_ok=True)
    DatabaseClient.for_planspace(planspace, db_sh).log_event(
        "lifecycle",
        "alignment-changed",
        "cleared",
        agent=agent_name,
        check=False,
    )
    return True


def make_alignment_checker(db_sh: Path, agent_name: str) -> Callable[[Path], bool]:
    """Return a ``(planspace) -> bool`` that calls :func:`check_and_clear`
    with *db_sh* and *agent_name* already bound."""

    def _check_and_clear_alignment_changed(planspace: Path) -> bool:
        return check_and_clear(planspace, db_sh=db_sh, agent_name=agent_name)

    return _check_and_clear_alignment_changed


def invalidate_excerpts(planspace: Path) -> None:
    """Delete all section proposal/alignment excerpt artifacts."""
    invalidate_all(planspace)
