"""Philosophy bootstrap signal, status, and result infrastructure.

Houses the constants, path helpers, and writers that the bootstrap
pipeline uses to communicate state to the rest of the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from containers import Services
from orchestrator.path_registry import PathRegistry


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Typed result from ``ensure_global_philosophy``."""

    status: str
    blocking_state: str | None
    philosophy_path: Path | None
    detail: str

    def __getitem__(self, key: str) -> Any:
        """Backward compat: allow ``result["status"]`` style access."""
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """Backward compat: allow ``result.get("blocking_state")`` style access."""
        return getattr(self, key, default)

# ── constants ─────────────────────────────────────────────────────────

BOOTSTRAP_SIGNAL_NAME = "philosophy-bootstrap-signal.json"
BOOTSTRAP_STATUS_NAME = "philosophy-bootstrap-status.json"
BOOTSTRAP_GUIDANCE_NAME = "philosophy-bootstrap-guidance.json"
BOOTSTRAP_DECISIONS_NAME = "philosophy-bootstrap-decisions.md"
USER_SOURCE_NAME = "philosophy-source-user.md"


# ── time helpers ──────────────────────────────────────────────────────

def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── path helpers ──────────────────────────────────────────────────────

def bootstrap_signal_path(paths: PathRegistry) -> Path:
    return paths.signals_dir() / BOOTSTRAP_SIGNAL_NAME


def bootstrap_status_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / BOOTSTRAP_STATUS_NAME


def bootstrap_diagnostics_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / "philosophy-bootstrap-diagnostics.json"


def bootstrap_guidance_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / BOOTSTRAP_GUIDANCE_NAME


def bootstrap_decisions_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / BOOTSTRAP_DECISIONS_NAME


def user_source_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / USER_SOURCE_NAME


# ── signal / status writers ───────────────────────────────────────────

def clear_bootstrap_signal(paths: PathRegistry) -> None:
    bootstrap_signal_path(paths).unlink(missing_ok=True)


def write_bootstrap_status(
    paths: PathRegistry,
    *,
    bootstrap_state: str,
    blocking_state: str | None,
    source_mode: str,
    detail: str,
) -> None:
    signal_path = bootstrap_signal_path(paths)
    Services.artifact_io().write_json(bootstrap_status_path(paths), {
        "bootstrap_state": bootstrap_state,
        "blocking_state": blocking_state,
        "source_mode": source_mode,
        "detail": detail,
        "active_signal": str(signal_path) if signal_path.exists() else None,
        "updated_at": _timestamp_now(),
    })


def write_bootstrap_signal(
    paths: PathRegistry,
    *,
    state: str,
    detail: str,
    needs: str,
    why_blocked: str,
    extras: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "section": "global",
        "state": state,
        "detail": detail,
        "needs": needs,
        "why_blocked": why_blocked,
    }
    if extras:
        payload.update(extras)
    Services.artifact_io().write_json(bootstrap_signal_path(paths), payload)


def bootstrap_result(
    *,
    status: str,
    blocking_state: str | None,
    philosophy_path: Path | None,
    detail: str,
) -> BootstrapResult:
    return BootstrapResult(
        status=status,
        blocking_state=blocking_state,
        philosophy_path=philosophy_path,
        detail=detail,
    )


def block_bootstrap(
    paths: PathRegistry,
    *,
    bootstrap_state: str,
    blocking_state: str,
    source_mode: str,
    detail: str,
    needs: str,
    why_blocked: str,
    philosophy_path: Path | None = None,
    extras: dict[str, Any] | None = None,
) -> BootstrapResult:
    write_bootstrap_signal(
        paths,
        state=blocking_state,
        detail=detail,
        needs=needs,
        why_blocked=why_blocked,
        extras=extras,
    )
    write_bootstrap_status(
        paths,
        bootstrap_state=bootstrap_state,
        blocking_state=blocking_state,
        source_mode=source_mode,
        detail=detail,
    )
    return bootstrap_result(
        status=bootstrap_state,
        blocking_state=blocking_state,
        philosophy_path=philosophy_path,
        detail=detail,
    )


def write_bootstrap_diagnostics(
    paths: PathRegistry,
    *,
    stage: str,
    attempts: list[dict[str, Any]],
    final_outcome: str,
) -> None:
    Services.artifact_io().write_json(bootstrap_diagnostics_path(paths), {
        "stage": stage,
        "attempts": attempts,
        "final_outcome": final_outcome,
        "updated_at": _timestamp_now(),
    })
