"""Codespace fingerprinting for staleness detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

NON_GIT_SENTINEL = "non-git:no-fingerprint"


def compute_codespace_fingerprint(codespace: Path) -> str:
    """Mechanical fingerprint of codespace: git HEAD + tracked file listing.

    Cheap to compute, detects meaningful changes without reading contents.
    For non-git workspaces returns a sentinel so callers know to dispatch
    the verifier for heuristic freshness checks instead of brute-force
    full-tree traversal.
    """
    # Check if codespace is inside a git repo
    is_git = (codespace / ".git").is_dir()
    if not is_git:
        try:
            subprocess.run(
                ["git", "-C", str(codespace), "rev-parse", "--git-dir"],
                capture_output=True, check=True,
            )
            is_git = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    if not is_git:
        return NON_GIT_SENTINEL

    # git HEAD
    try:
        head = subprocess.run(
            ["git", "-C", str(codespace), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        head = "no-head"

    # git diff --stat HEAD (last line = summary)
    try:
        diff_out = subprocess.run(
            ["git", "-C", str(codespace), "diff", "--stat", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        diff_summary = diff_out.splitlines()[-1] if diff_out else ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        diff_summary = ""

    # git ls-files count
    try:
        ls_out = subprocess.run(
            ["git", "-C", str(codespace), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
        file_count = str(len(ls_out.splitlines()))
    except (subprocess.CalledProcessError, FileNotFoundError):
        file_count = "0"

    return f"{head}:{diff_summary}:{file_count}"
