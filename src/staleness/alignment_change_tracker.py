"""Alignment change flag and excerpt invalidation helpers."""

from __future__ import annotations

from pathlib import Path

from signals.database_client import DatabaseClient
from proposal.excerpt_repository import invalidate_all
from orchestrator.path_registry import PathRegistry


def _database_client(planspace: Path, db_sh: Path) -> DatabaseClient:
    return DatabaseClient(db_sh, PathRegistry(planspace).run_db())


def set_flag(planspace: Path, *, db_sh: Path, agent_name: str) -> None:
    """Persist the alignment-changed flag and record a lifecycle event."""
    flag = PathRegistry(planspace).alignment_changed_flag()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("1", encoding="utf-8")
    _database_client(planspace, db_sh).log_event(
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
    _database_client(planspace, db_sh).log_event(
        "lifecycle",
        "alignment-changed",
        "cleared",
        agent=agent_name,
        check=False,
    )
    return True


def invalidate_excerpts(planspace: Path) -> None:
    """Delete all section proposal/alignment excerpt artifacts."""
    invalidate_all(planspace)
