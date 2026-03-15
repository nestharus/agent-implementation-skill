"""Dispatch metadata sidecar helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from containers import ArtifactIOService


class DispatchMetaStatus(str, Enum):
    """Status of a dispatch metadata sidecar read."""

    PRESENT = "present"
    ABSENT = "absent"
    CORRUPT = "corrupt"

    def __str__(self) -> str:  # noqa: D105
        return self.value


@dataclass(frozen=True)
class DispatchMetaResult:
    """Result of reading a dispatch metadata sidecar."""

    status: DispatchMetaStatus
    data: dict[str, Any] | None = None

    @property
    def is_corrupt(self) -> bool:
        return self.status == DispatchMetaStatus.CORRUPT

    @property
    def is_absent(self) -> bool:
        return self.status == DispatchMetaStatus.ABSENT


def dispatch_meta_path(output_path: Path) -> Path:
    """Return the sidecar path for a dispatch output artifact."""
    return output_path.with_suffix(".meta.json")


class Metadata:
    """Dispatch metadata sidecar read/write operations."""

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def write_dispatch_metadata(
        self, output_path: Path, *, returncode: int | None, timed_out: bool,
    ) -> Path:
        """Write the dispatch metadata sidecar next to the output file."""
        meta_path = dispatch_meta_path(output_path)
        self._artifact_io.write_json(
            meta_path,
            {
                "returncode": returncode,
                "timed_out": timed_out,
            },
            indent=None,
        )
        return meta_path

    def read_dispatch_metadata(self, meta_path: Path) -> DispatchMetaResult:
        """Read a dispatch metadata sidecar with fail-closed semantics."""
        if not meta_path.exists():
            return DispatchMetaResult(DispatchMetaStatus.ABSENT)

        data = self._artifact_io.read_json(meta_path)
        if data is None:
            return DispatchMetaResult(DispatchMetaStatus.CORRUPT)
        if not isinstance(data, dict):
            self._artifact_io.rename_malformed(meta_path)
            return DispatchMetaResult(DispatchMetaStatus.CORRUPT)
        return DispatchMetaResult(DispatchMetaStatus.PRESENT, data)
