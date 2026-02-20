#!/usr/bin/env python3
"""Generate a deterministic markdown structural scan for a repository."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".terraform",
}
IGNORE_FILE_NAMES = {".DS_Store"}
IGNORE_SUFFIXES = {".egg-info"}

PROJECT_MARKERS = {
    "pyproject.toml": "Python project",
    "package.json": "Node project",
    "Cargo.toml": "Rust project",
    "go.mod": "Go project",
    "Makefile": "Make-based build",
    "CMakeLists.txt": "C/C++ CMake build",
    "Dockerfile": "Containerized",
    "docker-compose.yml": "Compose stack",
    "setup.py": "Python setuptools",
    "setup.cfg": "Python setup config",
    "pom.xml": "Maven project",
    "build.gradle": "Gradle project",
    "Gemfile": "Ruby project",
    "requirements.txt": "Python requirements",
}

ROOT_KEY_FILES = {
    "README",
    "README.md",
    "README.rst",
    "LICENSE",
    "LICENSE.md",
    "main.py",
    "manage.py",
    "app.py",
    "index.js",
    "index.ts",
    "main.ts",
    "main.rs",
    "go.mod",
    "pyproject.toml",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    "Makefile",
}


@dataclass(frozen=True)
class DirSummary:
    """Summary of a directory in the scanned tree."""

    rel_path: str
    depth: int
    file_count: int
    extensions: tuple[str, ...]
    marker_names: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a local structural scan and emit markdown."
    )
    parser.add_argument("codespace", help="Repository root to scan")
    parser.add_argument("output", help="Output markdown path")
    return parser.parse_args()


def should_ignore_dir(name: str) -> bool:
    """Check if a directory name should be excluded from scanning."""
    if name in IGNORE_DIRS:
        return True
    return any(name.endswith(suffix) for suffix in IGNORE_SUFFIXES)


def extension_for(path: str) -> str:
    """Return the lowercase file extension or '[no-ext]'."""
    suffix = Path(path).suffix.lower()
    return suffix if suffix else "[no-ext]"


def scan_tree(root: Path) -> tuple[list[DirSummary], Counter[str], set[str], list[str], int]:
    """Walk the directory tree and collect summaries."""
    dir_summaries: list[DirSummary] = []
    extension_counts: Counter[str] = Counter()
    marker_hits: set[str] = set()
    key_files: list[str] = []
    total_files = 0

    try:
        root_entries = list(root.iterdir())
    except OSError as exc:
        raise RuntimeError(f"Unable to read root directory: {exc}") from exc  # noqa: TRY003

    for entry in sorted(root_entries, key=lambda p: p.name.lower()):
        if entry.name in ROOT_KEY_FILES and entry.is_file():
            key_files.append(entry.name)
        if entry.name == ".github" and entry.is_dir():
            workflows = entry / "workflows"
            if workflows.exists() and workflows.is_dir():
                marker_hits.add(".github/workflows")

    def on_walk_error(exc: OSError) -> None:
        print(f"[WARN] Skipping unreadable path: {exc}", file=sys.stderr)

    for current_root, dirs, files in os.walk(
        root, topdown=True, followlinks=False, onerror=on_walk_error
    ):
        dirs[:] = sorted(d for d in dirs if not should_ignore_dir(d))
        files = sorted(files)

        current_path = Path(current_root)
        rel = current_path.relative_to(root)
        rel_str = "." if rel == Path(".") else rel.as_posix()
        depth = 0 if rel_str == "." else len(rel.parts)

        marker_names: set[str] = set()
        if rel_str == "." and ".github" in dirs:
            workflows = current_path / ".github" / "workflows"
            if workflows.exists() and workflows.is_dir():
                marker_names.add(".github/workflows")
                marker_hits.add(".github/workflows")

        ext_set: set[str] = set()
        file_count = 0
        for filename in files:
            if filename in IGNORE_FILE_NAMES:
                continue
            file_count += 1
            total_files += 1
            ext = extension_for(filename)
            ext_set.add(ext)
            extension_counts[ext] += 1
            if filename in PROJECT_MARKERS:
                marker_names.add(filename)
                marker_hits.add(filename)

        dir_summaries.append(
            DirSummary(
                rel_path=rel_str,
                depth=depth,
                file_count=file_count,
                extensions=tuple(sorted(ext_set)),
                marker_names=tuple(sorted(marker_names)),
            )
        )

    key_files.sort()
    return dir_summaries, extension_counts, marker_hits, key_files, total_files


def render_markdown(
    root: Path,
    summaries: list[DirSummary],
    ext_counts: Counter[str],
    markers: set[str],
    key_files: list[str],
    total_files: int,
) -> str:
    """Render directory summaries as a markdown report."""
    project_name = root.name or str(root)
    lines: list[str] = [f"# Structural Scan: {project_name}", ""]

    lines.append("## Project Markers")
    if markers:
        for marker in sorted(markers):
            description = PROJECT_MARKERS.get(marker, "Project marker")
            lines.append(f"- `{marker}` ({description})")
    else:
        lines.append("- None detected")
    lines.append("")

    lines.append("## Key Files")
    if key_files:
        for filename in key_files:
            lines.append(f"- `{filename}`")
    else:
        lines.append("- None detected at repository root")
    lines.append("")

    lines.append("## File Type Distribution")
    lines.append("| Extension | Count |")
    lines.append("|-----------|-------|")
    if ext_counts:
        for ext, count in sorted(ext_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"| `{ext}` | {count} |")
    else:
        lines.append("| `[none]` | 0 |")
    lines.append("")

    lines.append("## Directory Tree")
    lines.append(f"- Total files scanned: {total_files}")

    if total_files > 500:
        max_depth = 3
        collapse_threshold = 20
    elif total_files < 50:
        max_depth = 50
        collapse_threshold = 1000000
    else:
        max_depth = 6
        collapse_threshold = 40

    for summary in sorted(summaries, key=lambda item: (item.depth, item.rel_path)):
        if summary.depth > max_depth:
            continue

        indent = "  " * summary.depth
        rel_display = "<root>" if summary.rel_path == "." else f"{summary.rel_path}/"
        ext_display = ", ".join(summary.extensions) if summary.extensions else "[none]"
        marker_display = (
            f"; markers: {', '.join(summary.marker_names)}" if summary.marker_names else ""
        )

        if summary.file_count > collapse_threshold:
            detail = f"{summary.file_count} files; collapsed; ext: {ext_display}"
            lines.append(
                f"{indent}- `{rel_display}` ({detail}{marker_display})"
            )
            continue

        detail = f"{summary.file_count} files; ext: {ext_display}"
        lines.append(
            f"{indent}- `{rel_display}` ({detail}{marker_display})"
        )

    lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    """CLI entry point for structural scanning."""
    args = parse_args()
    root = Path(args.codespace).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        print(f"[ERROR] codespace is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        summaries, ext_counts, markers, key_files, total_files = scan_tree(root)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 3

    markdown = render_markdown(root, summaries, ext_counts, markers, key_files, total_files)
    if not markdown.strip():
        print("[ERROR] generated structural scan is empty", file=sys.stderr)
        return 4

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_suffix(output.suffix + ".tmp")
    try:
        tmp_output.write_text(markdown, encoding="utf-8")
        if not tmp_output.read_text(encoding="utf-8").strip():
            print("[ERROR] structural scan output file is empty", file=sys.stderr)
            return 5
        tmp_output.replace(output)
    except OSError as exc:
        print(f"[ERROR] failed to write output: {exc}", file=sys.stderr)
        return 6

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
