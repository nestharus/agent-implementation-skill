"""Timeline merge, decoration, deduplication, and filtering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

from log_extract.models import (
    CorrelationLink,
    DispatchCandidate,
    TimelineEvent,
)

_SOURCE_PRIORITY = {
    "run.db": 0,
    "claude": 1,
    "codex": 1,
    "opencode": 1,
    "gemini": 1,
    "artifact": 2,
    "signal": 3,
}


def decorate(
    events: list[TimelineEvent],
    links: list[CorrelationLink],
    dispatches: list[DispatchCandidate],
) -> None:
    """Fill missing agent/section/model on AI events from matched dispatches.

    Mutates events in place.
    """
    dispatch_by_id = {d.dispatch_id: d for d in dispatches}
    session_to_dispatch: dict[str, DispatchCandidate] = {}
    for link in links:
        disp = dispatch_by_id.get(link.dispatch_id)
        if disp:
            session_to_dispatch[link.session_id] = disp

    for ev in events:
        if not ev.session_id:
            continue
        disp = session_to_dispatch.get(ev.session_id)
        if disp is None:
            continue
        if not ev.agent and disp.agent:
            ev.agent = disp.agent
        if not ev.section and disp.section:
            ev.section = disp.section
        if not ev.model and disp.model:
            ev.model = disp.model
        if not ev.backend and disp.backend:
            ev.backend = disp.backend


def merge_and_sort(event_streams: Iterable[Iterable[TimelineEvent]]) -> list[TimelineEvent]:
    """Merge multiple event streams and stable-sort by timestamp."""
    all_events: list[tuple[int, int, int, TimelineEvent]] = []
    counter = 0
    for stream in event_streams:
        for ev in stream:
            pri = _SOURCE_PRIORITY.get(ev.source, 1)
            all_events.append((ev.ts_ms, pri, counter, ev))
            counter += 1

    all_events.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in all_events]


def dedup(events: list[TimelineEvent]) -> list[TimelineEvent]:
    """Remove exact duplicate events based on visible field fingerprint."""
    seen: set[str] = set()
    result: list[TimelineEvent] = []
    for ev in events:
        fp = _fingerprint(ev)
        if fp not in seen:
            seen.add(fp)
            result.append(ev)
    return result


def apply_filters(
    events: list[TimelineEvent],
    *,
    after_ms: int | None = None,
    before_ms: int | None = None,
    sources: set[str] | None = None,
    agents: set[str] | None = None,
    sections: set[str] | None = None,
    kinds: set[str] | None = None,
    grep: str | None = None,
) -> list[TimelineEvent]:
    """Filter events. Returns a new list (does not mutate)."""
    grep_re = re.compile(grep, re.IGNORECASE) if grep else None
    result: list[TimelineEvent] = []

    for ev in events:
        if after_ms is not None and ev.ts_ms < after_ms:
            continue
        if before_ms is not None and ev.ts_ms > before_ms:
            continue
        if sources is not None and ev.source not in sources:
            continue
        if agents is not None and ev.agent not in agents:
            continue
        if sections is not None and ev.section not in sections:
            continue
        if kinds is not None and ev.kind not in kinds:
            continue
        if grep_re is not None:
            haystack = ev.detail + " " + json.dumps(ev.raw) if ev.raw else ev.detail
            if not grep_re.search(haystack):
                continue
        result.append(ev)

    return result


def _fingerprint(ev: TimelineEvent) -> str:
    parts = f"{ev.ts}|{ev.source}|{ev.kind}|{ev.agent}|{ev.session_id}|{ev.detail}"
    return hashlib.md5(parts.encode()).hexdigest()
