"""Tests for correlator — Packet 7."""

from log_extract.correlator import correlate
from log_extract.models import DispatchCandidate, SessionCandidate


def _disp(dispatch_id="d1", ts_ms=1000000, backend="claude2", family="claude", **kw):
    return DispatchCandidate(
        dispatch_id=dispatch_id, ts="2026-01-01T00:00:01.000Z",
        ts_ms=ts_ms, backend=backend, source_family=family, **kw,
    )


def _sess(session_id="s1", ts_ms=1000000, backend="claude2", family="claude", **kw):
    return SessionCandidate(
        session_id=session_id, ts="2026-01-01T00:00:01.000Z",
        ts_ms=ts_ms, backend=backend, source_family=family, **kw,
    )


class TestCorrelation:
    def test_exact_prompt_match_wins(self):
        d = _disp(prompt_signature="abc123", ts_ms=1000)
        s = _sess(prompt_signature="abc123", ts_ms=1500)
        links = correlate([d], [s])
        assert len(links) == 1
        assert links[0].dispatch_id == "d1"
        assert links[0].session_id == "s1"
        assert "prompt_signature_match" in links[0].reasons

    def test_time_and_cwd_sufficient(self):
        d = _disp(ts_ms=1000, cwd="/project")
        s = _sess(ts_ms=2000, cwd="/project")
        links = correlate([d], [s])
        assert len(links) == 1
        assert links[0].score >= 35

    def test_different_families_rejected(self):
        d = _disp(family="claude", ts_ms=1000)
        s = _sess(family="codex", ts_ms=1000)
        links = correlate([d], [s])
        assert len(links) == 0

    def test_time_delta_over_300s_rejected(self):
        d = _disp(ts_ms=0)
        s = _sess(ts_ms=400_000)
        links = correlate([d], [s])
        assert len(links) == 0

    def test_low_score_not_matched(self):
        # Only time delta <=120s gives 10 points — below threshold
        d = _disp(ts_ms=0, family="", cwd="", model="")
        s = _sess(ts_ms=100_000, family="", cwd="", model="")
        links = correlate([d], [s])
        assert len(links) == 0

    def test_one_to_one_assignment(self):
        d1 = _disp(dispatch_id="d1", prompt_signature="sig", ts_ms=1000)
        d2 = _disp(dispatch_id="d2", ts_ms=1000, cwd="/p")
        s1 = _sess(session_id="s1", prompt_signature="sig", ts_ms=1000, cwd="/p")
        links = correlate([d1, d2], [s1])
        assert len(links) == 1
        # Prompt match should win
        assert links[0].dispatch_id == "d1"

    def test_deterministic_tiebreak(self):
        d1 = _disp(dispatch_id="d1", ts_ms=1000, cwd="/p", model="opus")
        d2 = _disp(dispatch_id="d2", ts_ms=1000, cwd="/p", model="opus")
        s = _sess(ts_ms=1000, cwd="/p", model="opus")
        links1 = correlate([d1, d2], [s])
        links2 = correlate([d1, d2], [s])
        assert links1[0].dispatch_id == links2[0].dispatch_id

    def test_empty_inputs(self):
        assert correlate([], []) == []
        assert correlate([_disp()], []) == []
        assert correlate([], [_sess()]) == []
