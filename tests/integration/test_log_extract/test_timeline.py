"""Tests for timeline merge/decorate/filter — Packet 8."""

from log_extract.models import (
    CorrelationLink,
    DispatchCandidate,
    TimelineEvent,
)
from log_extract.timeline import apply_filters, decorate, dedup, merge_and_sort


def _ev(ts_ms=1000, source="run.db", kind="lifecycle", detail="test", **kw):
    return TimelineEvent(
        ts="2026-01-01T00:00:01.000Z", ts_ms=ts_ms,
        source=source, kind=kind, detail=detail, **kw,
    )


class TestMergeAndSort:
    def test_sorts_by_timestamp(self):
        e1 = _ev(ts_ms=2000, detail="second")
        e2 = _ev(ts_ms=1000, detail="first")
        result = merge_and_sort([[e1], [e2]])
        assert result[0].detail == "first"
        assert result[1].detail == "second"

    def test_same_ts_source_priority(self):
        e_db = _ev(ts_ms=1000, source="run.db", detail="db")
        e_claude = _ev(ts_ms=1000, source="claude", detail="claude")
        e_art = _ev(ts_ms=1000, source="artifact", detail="artifact")
        e_sig = _ev(ts_ms=1000, source="signal", detail="signal")
        result = merge_and_sort([[e_sig, e_art, e_claude, e_db]])
        assert [e.detail for e in result] == ["db", "claude", "artifact", "signal"]

    def test_stable_input_order(self):
        e1 = _ev(ts_ms=1000, source="claude", detail="first")
        e2 = _ev(ts_ms=1000, source="claude", detail="second")
        result = merge_and_sort([[e1, e2]])
        assert result[0].detail == "first"

    def test_empty_streams(self):
        assert merge_and_sort([[], []]) == []


class TestDedup:
    def test_removes_exact_duplicates(self):
        e = _ev()
        result = dedup([e, e])
        assert len(result) == 1

    def test_keeps_different_events(self):
        e1 = _ev(detail="a")
        e2 = _ev(detail="b")
        result = dedup([e1, e2])
        assert len(result) == 2


class TestDecorate:
    def test_fills_agent_from_dispatch(self):
        ev = _ev(source="claude", session_id="s1")
        disp = DispatchCandidate(
            dispatch_id="d1", ts="", ts_ms=0, backend="claude2",
            source_family="claude", agent="solver-03", section="03", model="opus",
        )
        link = CorrelationLink(session_id="s1", dispatch_id="d1", score=90, reasons=[])
        decorate([ev], [link], [disp])
        assert ev.agent == "solver-03"
        assert ev.section == "03"
        assert ev.model == "opus"

    def test_does_not_overwrite_existing(self):
        ev = _ev(source="claude", session_id="s1", agent="already-set")
        disp = DispatchCandidate(
            dispatch_id="d1", ts="", ts_ms=0, backend="claude2",
            source_family="claude", agent="different",
        )
        link = CorrelationLink(session_id="s1", dispatch_id="d1", score=90, reasons=[])
        decorate([ev], [link], [disp])
        assert ev.agent == "already-set"

    def test_unmatched_session_unchanged(self):
        ev = _ev(source="claude", session_id="s999")
        decorate([ev], [], [])
        assert ev.agent == ""


class TestFilters:
    def test_after(self):
        events = [_ev(ts_ms=1000), _ev(ts_ms=2000), _ev(ts_ms=3000)]
        result = apply_filters(events, after_ms=2000)
        assert len(result) == 2

    def test_before(self):
        events = [_ev(ts_ms=1000), _ev(ts_ms=2000), _ev(ts_ms=3000)]
        result = apply_filters(events, before_ms=2000)
        assert len(result) == 2

    def test_source_filter(self):
        events = [_ev(source="run.db"), _ev(source="claude")]
        result = apply_filters(events, sources={"claude"})
        assert len(result) == 1
        assert result[0].source == "claude"

    def test_agent_filter(self):
        events = [_ev(agent="solver-03"), _ev(agent="scanner")]
        result = apply_filters(events, agents={"solver-03"})
        assert len(result) == 1

    def test_section_filter(self):
        events = [_ev(section="03"), _ev(section="05")]
        result = apply_filters(events, sections={"03"})
        assert len(result) == 1

    def test_kind_filter(self):
        events = [_ev(kind="lifecycle"), _ev(kind="signal")]
        result = apply_filters(events, kinds={"signal"})
        assert len(result) == 1

    def test_grep(self):
        events = [_ev(detail="found a bug"), _ev(detail="all good")]
        result = apply_filters(events, grep="bug")
        assert len(result) == 1

    def test_grep_case_insensitive(self):
        events = [_ev(detail="Found a BUG")]
        result = apply_filters(events, grep="bug")
        assert len(result) == 1

    def test_no_mutation(self):
        events = [_ev(ts_ms=1000), _ev(ts_ms=5000)]
        result = apply_filters(events, after_ms=3000)
        assert len(events) == 2
        assert len(result) == 1
