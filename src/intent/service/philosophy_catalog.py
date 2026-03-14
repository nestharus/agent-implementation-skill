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
_CODESPACE_QUOTA_NUMERATOR = 4
_CODESPACE_QUOTA_DENOMINATOR = 5


def walk_md_bounded(
    root: Path,
    *,
    max_depth: int,
    exclude_top_dirs: frozenset[str] = frozenset(),
    extensions: frozenset[str] = frozenset({".md"}),
):
    """Yield matching files under *root* with depth-bounded traversal."""
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
    max_files: int = 50,
    max_size_kb: int = 100,
    max_depth: int = 3,
    extensions: frozenset[str] = frozenset({".md"}),
) -> list[dict]:
    """Build a mechanical catalog of candidate philosophy source files."""
    codespace_quota = max(max_files * _CODESPACE_QUOTA_NUMERATOR // _CODESPACE_QUOTA_DENOMINATOR, 1)
    planspace_quota = max(max_files - codespace_quota, 1)

    candidates: list[dict] = []
    seen: set[str] = set()

    for root_dir, quota, exclude_top in (
        (codespace, codespace_quota, frozenset()),
        (planspace, planspace_quota, frozenset({"artifacts"})),
    ):
        root_count = 0
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
            if size == 0 or size > max_size_kb * 1024:
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
            root_count += 1
            if root_count >= quota:
                break

    return candidates


def _declared_principle_ids(philosophy_text: str) -> set[str]:
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
