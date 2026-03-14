"""Shared context bundle for scan pipeline functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScanContext:
    """Immutable environment context threaded through scan functions.

    Bundles the five parameters that almost every scan function receives:
    codespace, codemap_path, corrections_path, scan_log_dir, and
    model_policy.
    """

    codespace: Path
    codemap_path: Path
    corrections_path: Path
    scan_log_dir: Path
    model_policy: dict[str, str]

    @classmethod
    def from_artifacts(
        cls,
        *,
        codespace: Path,
        codemap_path: Path,
        artifacts_dir: Path,
        scan_log_dir: Path,
        model_policy: dict[str, str],
    ) -> ScanContext:
        """Build a context, deriving ``corrections_path`` from *artifacts_dir*."""
        from orchestrator.path_registry import PathRegistry  # noqa: E402

        return cls(
            codespace=codespace,
            codemap_path=codemap_path,
            corrections_path=PathRegistry(artifacts_dir.parent).corrections(),
            scan_log_dir=scan_log_dir,
            model_policy=model_policy,
        )
