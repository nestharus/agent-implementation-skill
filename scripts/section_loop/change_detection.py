import hashlib
from pathlib import Path


def hash_file(path: Path) -> str:
    """Return SHA-256 hex digest of a file, or empty string if missing."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_files(codespace: Path, rel_paths: list[str]) -> dict[str, str]:
    """Hash all files before implementation. Returns {rel_path: hash}."""
    return {rp: hash_file(codespace / rp) for rp in rel_paths}


def diff_files(codespace: Path, before: dict[str, str],
               reported: list[str]) -> list[str]:
    """Filter reported modified files to only those that actually changed."""
    changed = []
    for rp in reported:
        after = hash_file(codespace / rp)
        if after != before.get(rp, ""):
            changed.append(rp)
    return changed
