"""Artifact and signal extractor for the log extraction pipeline.

Walks an artifacts directory to emit TimelineEvent instances for every
regular file (artifacts) and for signal JSON files in the ``signals/``
subdirectory.  Special handling for ``*.meta.json`` and
``traceability.json`` files.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterator

from log_extract.models import TimelineEvent
from log_extract.utils import infer_section, parse_timestamp, summarize_text


def iter_events(artifacts_dir: Path) -> Iterator[TimelineEvent]:
    """Yield :class:`TimelineEvent` instances for files under *artifacts_dir*.

    * Regular files (excluding ``signals/``) produce ``source="artifact"``
      events.
    * Files under ``signals/*.json`` produce ``source="signal"`` events.
    * Missing or non-directory paths are handled gracefully (no events).
    """
    if not artifacts_dir.is_dir():
        return

    yield from _iter_artifact_events(artifacts_dir)
    yield from _iter_signal_events(artifacts_dir)


# ------------------------------------------------------------------
# Artifact events
# ------------------------------------------------------------------

def _iter_artifact_events(artifacts_dir: Path) -> Iterator[TimelineEvent]:
    """Walk *artifacts_dir* excluding the ``signals/`` subtree."""
    signals_dir = (artifacts_dir / "signals").resolve()

    for dirpath, dirnames, filenames in os.walk(artifacts_dir):
        # Prune the signals subdirectory so os.walk never descends into it.
        resolved = Path(dirpath).resolve()
        if resolved == signals_dir:
            dirnames.clear()
            continue

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if not fpath.is_file():
                continue

            yield from _artifact_file_events(artifacts_dir, fpath)


def _artifact_file_events(
    artifacts_dir: Path, fpath: Path,
) -> Iterator[TimelineEvent]:
    """Emit event(s) for a single artifact file."""
    rel = fpath.relative_to(artifacts_dir)
    mtime = os.path.getmtime(fpath)
    ts, ts_ms = parse_timestamp(mtime)

    try:
        size = fpath.stat().st_size
    except OSError:
        size = 0

    detail = f"{rel} ({size} bytes)"

    # *.meta.json — enrich detail with returncode / timed_out
    if fpath.name.endswith(".meta.json"):
        detail = _enrich_meta(fpath, detail)

    section = infer_section(str(rel))

    yield TimelineEvent(
        ts=ts,
        ts_ms=ts_ms,
        source="artifact",
        kind="artifact",
        detail=detail,
        section=section,
    )

    # traceability.json — additional per-entry events
    if fpath.name == "traceability.json":
        yield from _traceability_entry_events(fpath, ts, ts_ms)


def _enrich_meta(fpath: Path, base_detail: str) -> str:
    """Append returncode and timed_out from a meta JSON file."""
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: cannot parse {fpath}: {exc}", file=sys.stderr)
        return base_detail

    parts = [base_detail]
    if "returncode" in data:
        parts.append(f"returncode={data['returncode']}")
    if "timed_out" in data:
        parts.append(f"timed_out={data['timed_out']}")
    return "; ".join(parts)


def _traceability_entry_events(
    fpath: Path, file_ts: str, file_ts_ms: int,
) -> Iterator[TimelineEvent]:
    """Emit one event per entry in a traceability.json array."""
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: cannot parse {fpath}: {exc}", file=sys.stderr)
        return

    if not isinstance(data, list):
        print(
            f"WARNING: traceability.json is not an array in {fpath}",
            file=sys.stderr,
        )
        return

    for entry in data:
        if not isinstance(entry, dict):
            continue

        parts: list[str] = []
        for key in ("section", "artifact", "source", "detail"):
            val = entry.get(key)
            if val is not None:
                parts.append(f"{key}={val}")

        detail = summarize_text("; ".join(parts)) if parts else "(empty entry)"
        section = infer_section(
            str(entry.get("section", "")),
            str(entry.get("artifact", "")),
        )

        yield TimelineEvent(
            ts=file_ts,
            ts_ms=file_ts_ms,
            source="artifact",
            kind="artifact",
            detail=detail,
            section=section,
        )


# ------------------------------------------------------------------
# Signal events
# ------------------------------------------------------------------

def _iter_signal_events(artifacts_dir: Path) -> Iterator[TimelineEvent]:
    """Yield events for ``signals/*.json`` files."""
    signals_dir = artifacts_dir / "signals"
    if not signals_dir.is_dir():
        return

    for fpath in sorted(signals_dir.iterdir()):
        if not fpath.is_file() or fpath.suffix != ".json":
            continue

        mtime = os.path.getmtime(fpath)
        ts, ts_ms = parse_timestamp(mtime)

        detail = _signal_detail(fpath)
        section = infer_section(fpath.name)

        yield TimelineEvent(
            ts=ts,
            ts_ms=ts_ms,
            source="signal",
            kind="signal",
            detail=detail,
            section=section,
        )


def _signal_detail(fpath: Path) -> str:
    """Build a detail string from a signal JSON file."""
    base = fpath.stem

    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: cannot parse signal {fpath}: {exc}", file=sys.stderr)
        return base

    if not isinstance(data, dict):
        return f"{base}: {summarize_text(str(data))}"

    # Shallow summary: first few key=value pairs
    pairs: list[str] = []
    for key in list(data)[:5]:
        val = data[key]
        rendered = str(val) if not isinstance(val, (dict, list)) else type(val).__name__
        pairs.append(f"{key}={summarize_text(rendered, limit=60)}")

    summary = "; ".join(pairs)
    return f"{base}: {summary}" if summary else base
