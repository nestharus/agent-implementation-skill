"""Shared scan failure logging."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


def log_phase_failure(
    phase: str,
    section: str,
    error: str,
    planspace: Path,
) -> None:
    """Append a structured failure line and emit the same failure to stderr."""
    failure_log = planspace / "failures.log"
    ts = datetime.now(tz=timezone.utc).isoformat()
    line = f"{ts} phase={phase} context={section} message={error}\n"
    with failure_log.open("a") as f:
        f.write(line)
    print(
        f"[FAIL] phase={phase} context={section} message={error}",
        file=sys.stderr,
    )
