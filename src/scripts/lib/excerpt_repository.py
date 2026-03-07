"""Repository helpers for section excerpt artifacts."""

from __future__ import annotations

from pathlib import Path

from .path_registry import PathRegistry


def _excerpt_path(planspace: Path, section: str, excerpt_type: str) -> Path:
    paths = PathRegistry(planspace)
    if excerpt_type == "proposal":
        return paths.proposal_excerpt(section)
    if excerpt_type == "alignment":
        return paths.alignment_excerpt(section)
    raise ValueError(f"Unknown excerpt type: {excerpt_type}")


def write(planspace: Path, section: str, excerpt_type: str, content: str) -> Path:
    """Write an excerpt artifact and return its path."""
    excerpt_path = _excerpt_path(planspace, section, excerpt_type)
    excerpt_path.parent.mkdir(parents=True, exist_ok=True)
    excerpt_path.write_text(content, encoding="utf-8")
    return excerpt_path


def read(planspace: Path, section: str, excerpt_type: str) -> str | None:
    """Read an excerpt artifact if present."""
    excerpt_path = _excerpt_path(planspace, section, excerpt_type)
    if not excerpt_path.exists():
        return None
    return excerpt_path.read_text(encoding="utf-8")


def exists(planspace: Path, section: str, excerpt_type: str) -> bool:
    """Return whether an excerpt artifact exists."""
    return _excerpt_path(planspace, section, excerpt_type).exists()


def invalidate_all(planspace: Path) -> None:
    """Delete all proposal and alignment excerpts across sections."""
    sections_dir = PathRegistry(planspace).sections_dir()
    if not sections_dir.exists():
        return
    for pattern in (
        "section-*-proposal-excerpt.md",
        "section-*-alignment-excerpt.md",
    ):
        for path in sections_dir.glob(pattern):
            path.unlink(missing_ok=True)
