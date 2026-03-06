"""Session-to-dispatch correlation.

Pure matching logic — no file access.  Takes dispatch candidates from the
orchestration DB extractor and session candidates from AI backend
extractors and produces scored correlation links.
"""

from __future__ import annotations

from log_extract.models import (
    CorrelationLink,
    DispatchCandidate,
    SessionCandidate,
)


def correlate(
    dispatches: list[DispatchCandidate],
    sessions: list[SessionCandidate],
) -> list[CorrelationLink]:
    """Match session candidates to dispatch candidates.

    Returns one :class:`CorrelationLink` per matched pair.  Unmatched
    candidates are silently dropped.
    """
    scored: list[tuple[int, int, int, str, str, list[str]]] = []

    for si, sess in enumerate(sessions):
        for di, disp in enumerate(dispatches):
            score, reasons = _score(disp, sess)
            if score < 0:
                continue
            delta = abs(sess.ts_ms - disp.ts_ms)
            scored.append((score, delta, si, disp.dispatch_id, sess.session_id, reasons))

    # Greedy assignment by descending score, ascending delta
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_dispatches: set[str] = set()
    used_sessions: set[str] = set()
    links: list[CorrelationLink] = []

    for score, _delta, _si, d_id, s_id, reasons in scored:
        if d_id in used_dispatches or s_id in used_sessions:
            continue
        if score < 35:
            continue
        links.append(CorrelationLink(
            session_id=s_id,
            dispatch_id=d_id,
            score=score,
            reasons=reasons,
        ))
        used_dispatches.add(d_id)
        used_sessions.add(s_id)

    return links


def _score(
    disp: DispatchCandidate,
    sess: SessionCandidate,
) -> tuple[int, list[str]]:
    """Score a dispatch-session pair.  Returns ``(-1, [])`` for hard rejects."""
    # Hard reject: backend families differ (when both known)
    if (disp.source_family and sess.source_family
            and disp.source_family != sess.source_family):
        return -1, []

    delta_ms = abs(sess.ts_ms - disp.ts_ms)
    if delta_ms > 300_000:
        return -1, []

    score = 0
    reasons: list[str] = []

    # Prompt signature match
    if (disp.prompt_signature and sess.prompt_signature
            and disp.prompt_signature == sess.prompt_signature):
        score += 60
        reasons.append("prompt_signature_match")

    # Time proximity
    if delta_ms <= 5_000:
        score += 30
        reasons.append("time_delta<=5s")
    elif delta_ms <= 30_000:
        score += 20
        reasons.append("time_delta<=30s")
    elif delta_ms <= 120_000:
        score += 10
        reasons.append("time_delta<=120s")

    # CWD match
    if disp.cwd and sess.cwd:
        if disp.cwd == sess.cwd:
            score += 15
            reasons.append("cwd_exact")
        elif disp.cwd.rsplit("/", 1)[-1] == sess.cwd.rsplit("/", 1)[-1]:
            score += 5
            reasons.append("cwd_basename")

    # Model match
    if disp.model and sess.model:
        if disp.model == sess.model:
            score += 15
            reasons.append("model_exact")
        elif _compatible_models(disp.model, sess.model):
            score += 8
            reasons.append("model_compatible")

    # Section match
    if disp.section and sess.source_family:
        # Session candidates don't usually have section; skip
        pass

    return score, reasons


def _compatible_models(a: str, b: str) -> bool:
    """Check if two model names are from the same family."""
    a_lower, b_lower = a.lower(), b.lower()
    families = [
        ("claude", "opus", "sonnet", "haiku"),
        ("gpt", "codex", "o1", "o3"),
        ("glm", "cerebras", "zai"),
        ("gemini",),
    ]
    for family in families:
        a_match = any(tok in a_lower for tok in family)
        b_match = any(tok in b_lower for tok in family)
        if a_match and b_match:
            return True
    return False
