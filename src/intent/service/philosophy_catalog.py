"""Philosophy catalog: file-system scanning and candidate discovery.

Build a mechanical catalog of candidate philosophy source files from
the planspace and codespace directories.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_PREVIEW_START_LINES = 15
_PREVIEW_CONTEXT_BEFORE = 7
_PREVIEW_CONTEXT_AFTER = 8

_DEFAULT_CATALOG_MAX_FILES = 0  # 0 = unlimited
_DEFAULT_CATALOG_MAX_SIZE_KB = 0  # 0 = unlimited
_DEFAULT_CATALOG_MAX_DEPTH = 0  # 0 = unlimited

_CODESPACE_EXCLUDE_DIRS = frozenset({
    ".tmp", "artifacts", ".git", "node_modules", "__pycache__",
})


def walk_md_bounded(
    root: Path,
    *,
    max_depth: int,
    exclude_top_dirs: frozenset[str] = frozenset(),
    extensions: frozenset[str] = frozenset({".md"}),
):
    """Yield matching files under *root* with depth-bounded traversal.

    A *max_depth* of ``0`` means unlimited depth.
    """
    if not root.is_dir():
        return
    root_s = str(root)
    for dirpath, dirnames, filenames in os.walk(root_s):
        rel = os.path.relpath(dirpath, root_s)
        depth = 0 if rel == "." else rel.count(os.sep) + 1

        if depth == 0:
            dirnames[:] = sorted(
                d for d in dirnames if d not in exclude_top_dirs
            )
        else:
            dirnames.sort()

        if max_depth > 0:
            if depth + 1 >= max_depth:
                dirnames.clear()
            if depth + 1 > max_depth:
                continue

        for fname in sorted(filenames):
            if any(fname.endswith(ext) for ext in extensions):
                yield Path(dirpath) / fname


def build_philosophy_catalog(
    planspace: Path,
    codespace: Path,
    *,
    max_files: int = _DEFAULT_CATALOG_MAX_FILES,
    max_size_kb: int = _DEFAULT_CATALOG_MAX_SIZE_KB,
    max_depth: int = _DEFAULT_CATALOG_MAX_DEPTH,
    extensions: frozenset[str] = frozenset({".md"}),
) -> list[dict]:
    """Build a mechanical catalog of candidate philosophy source files.

    A *max_files* of ``0`` means unlimited files.
    A *max_size_kb* of ``0`` means unlimited file size.
    A *max_depth* of ``0`` means unlimited directory depth.
    """
    candidates: list[dict] = []
    seen: set[str] = set()

    for root_dir, exclude_top in (
        (codespace, _CODESPACE_EXCLUDE_DIRS),
        (planspace, frozenset({"artifacts"})),
    ):
        for found_file in walk_md_bounded(
            root_dir,
            max_depth=max_depth,
            exclude_top_dirs=exclude_top,
            extensions=extensions,
        ):
            try:
                size = found_file.stat().st_size
            except OSError:
                continue
            if size == 0:
                continue
            if max_size_kb > 0 and size > max_size_kb * 1024:
                continue

            resolved = str(found_file.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)

            try:
                lines = found_file.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            mid = len(lines) // 2
            candidates.append({
                "path": str(found_file),
                "size_kb": round(size / 1024, 1),
                "preview_start": "\n".join(lines[:_PREVIEW_START_LINES]),
                "preview_middle": "\n".join(lines[max(0, mid - _PREVIEW_CONTEXT_BEFORE):mid + _PREVIEW_CONTEXT_AFTER]),
                "headings": [
                    line.lstrip("#").strip()
                    for line in lines
                    if line.startswith("#")
                ],
            })

    return candidates


def declared_principle_ids(philosophy_text: str) -> set[str]:
    """Extract principle IDs only from ### headings inside ## Principles."""
    ids: set[str] = set()
    in_principles = False
    in_fence = False

    for raw_line in philosophy_text.splitlines():
        line = raw_line.lstrip()

        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if re.fullmatch(r"##\s+Principles\s*", line):
            in_principles = True
            continue

        if in_principles and line.startswith("## ") and not line.startswith("### "):
            break

        if not in_principles:
            continue

        match = re.match(r"^###\s+(P\d+)\b", line)
        if match is not None:
            ids.add(match.group(1))

    return ids
