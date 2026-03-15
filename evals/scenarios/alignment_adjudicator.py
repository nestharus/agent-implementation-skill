"""Alignment output adjudicator live-eval scenarios.

Dispatches the real `alignment-output-adjudicator.md` agent with raw
alignment text (simulating unparseable primary alignment output) and
checks that it returns a normalized structured verdict JSON.

Scenarios:
  alignment_adjudicator_clear: Clear alignment text -> aligned=true
  alignment_adjudicator_ambiguous: Conflicting signals -> definitive verdict
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_verdict_json(agent_output: str) -> dict[str, object] | None:
    """Extract the verdict JSON object from agent output.

    Tries: fenced JSON block, any JSON with 'aligned' key, brace-delimited.
    """
    candidate = agent_output.strip()
    if not candidate:
        return None
    # Try fenced JSON block
    fenced = _JSON_FENCE_RE.search(candidate)
    if fenced is not None:
        try:
            payload = json.loads(fenced.group(1))
            if isinstance(payload, dict) and "aligned" in payload:
                return payload
        except json.JSONDecodeError:
            pass
    # Try any JSON object containing "aligned"
    for m in re.finditer(r"\{[^{}]*\"aligned\"[^{}]*\}", candidate):
        try:
            payload = json.loads(m.group(0))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    # Try raw JSON
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    # Try brace-delimited substring
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(candidate[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Fixtures: raw alignment text that the adjudicator must classify
# ---------------------------------------------------------------------------

_CLEAR_ALIGNMENT_TEXT = textwrap.dedent("""\
    Okay so I looked at the section and the proposal and honestly
    everything looks fine. The implementation follows the proposal
    exactly - the rate limiter was added to the middleware chain,
    the Redis sliding-window counter is implemented as described,
    and the graceful degradation path works when Redis is down.

    No issues found. The framing matches what was specified in the
    section spec. All requirements (REQ-01 through REQ-04) are
    addressed. The integration points are correct - middleware chain
    insertion and RateLimitConfig schema are both resolved.

    I checked the verification surface too - tests cover the happy
    path, the rate-exceeded path, and the Redis-down fallback.

    Overall: aligned, no problems.
""")

_AMBIGUOUS_ALIGNMENT_TEXT = textwrap.dedent("""\
    Looking at section 06 implementation against the proposal...

    The webhook dispatcher is implemented and delivers POST callbacks
    as specified. HMAC-SHA256 signing looks correct. So far aligned.

    However there are some concerning things:
    - The retry logic uses fixed delays instead of exponential backoff
      as specified in REQ-03. The proposal says exponential backoff
      with max 5 retries but the implementation does linear 5-second
      retries.
    - The subscription registry doesn't persist inactive status after
      exhausted retries. The proposal's failure mode section says
      subscriptions should be marked inactive after 3 consecutive
      failed events, but this isn't implemented.

    On the other hand the core delivery works, the signing is correct,
    the CRUD API is complete, and 4xx responses correctly do not
    trigger retries.

    The framing is right - this IS a webhook delivery system as
    specified. But the retry behavior and failure-mode handling
    deviate from the proposal. Not sure if these are blockers or
    just debt. The core intent is met but details are off.

    I'd say... partially aligned? The main functionality works but
    two requirements are only partially satisfied.
""")


# ---------------------------------------------------------------------------
# Setup: clear scenario
# ---------------------------------------------------------------------------

def _setup_clear(planspace: Path, codespace: Path) -> Path:
    """Seed a clear, unambiguous alignment text for adjudication."""
    del codespace
    artifacts = planspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / "alignment-adjudicator-clear-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Classify Alignment Output

        The primary alignment check produced the following text output
        that could not be parsed as JSON.  Read it and classify it into
        a structured verdict.

        ## Raw Alignment Output

        ```
        {_CLEAR_ALIGNMENT_TEXT}
        ```

        ## Instructions

        Classify this output into the structured verdict format:

        ```json
        {{
          "aligned": true|false|null,
          "frame_ok": true|false|null,
          "problems": [],
          "confidence": "high"|"medium"|"low"|"none",
          "raw_signal": "brief quote supporting classification"
        }}
        ```

        Do NOT re-run the alignment check.  Classify what is written.
        Output JSON only.
    """), encoding="utf-8")
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: ambiguous scenario
# ---------------------------------------------------------------------------

def _setup_ambiguous(planspace: Path, codespace: Path) -> Path:
    """Seed an ambiguous alignment text with conflicting signals."""
    del codespace
    artifacts = planspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    prompt_path = artifacts / "alignment-adjudicator-ambiguous-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Classify Alignment Output

        The primary alignment check produced the following text output
        that could not be parsed as JSON.  Read it and classify it into
        a structured verdict.

        ## Raw Alignment Output

        ```
        {_AMBIGUOUS_ALIGNMENT_TEXT}
        ```

        ## Instructions

        Classify this output into the structured verdict format:

        ```json
        {{
          "aligned": true|false|null,
          "frame_ok": true|false|null,
          "problems": [],
          "confidence": "high"|"medium"|"low"|"none",
          "raw_signal": "brief quote supporting classification"
        }}
        ```

        Do NOT re-run the alignment check.  Classify what is written.
        The output contains mixed signals -- some things are aligned,
        some are not.  Produce a definitive classification.
        Output JSON only.
    """), encoding="utf-8")
    return prompt_path


# ---------------------------------------------------------------------------
# Check functions: clear scenario
# ---------------------------------------------------------------------------

def _check_clear_has_verdict_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a normalized verdict JSON."""
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "Agent output did not contain a verdict JSON object"
    if "aligned" not in data:
        return False, f"JSON missing 'aligned' key. Keys found: {sorted(data.keys())}"
    return True, f"Verdict JSON found: aligned={data.get('aligned')}"


def _check_clear_aligned_true(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the normalized verdict has aligned=true."""
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    aligned = data.get("aligned")
    if aligned is True:
        return True, "aligned=true (correct for unambiguously aligned text)"
    return False, f"Expected aligned=true, got {aligned}"


def _check_clear_high_confidence(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify confidence is high for clear, unambiguous input."""
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    confidence = data.get("confidence")
    if confidence == "high":
        return True, "confidence='high' (correct for unambiguous input)"
    # Medium is also acceptable -- the text is informal but clear
    if confidence == "medium":
        return True, "confidence='medium' (acceptable for informal but clear input)"
    return False, f"Expected confidence='high' or 'medium', got '{confidence}'"


# ---------------------------------------------------------------------------
# Check functions: ambiguous scenario
# ---------------------------------------------------------------------------

def _check_ambiguous_has_verdict_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a normalized verdict JSON."""
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "Agent output did not contain a verdict JSON object"
    if "aligned" not in data:
        return False, f"JSON missing 'aligned' key. Keys found: {sorted(data.keys())}"
    return True, f"Verdict JSON found: aligned={data.get('aligned')}"


def _check_ambiguous_verdict_definitive(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the verdict is definitive (true or false), not null/hedging.

    The agent spec says: if the output is contradictory, flag low confidence
    but still produce a classification.  The ambiguous fixture has real
    problems (retry logic, failure mode), so aligned=false is the expected
    classification, but aligned=true with problems listed is also acceptable
    as long as the agent commits to a boolean.
    """
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    aligned = data.get("aligned")
    if aligned is True or aligned is False:
        return True, (
            f"aligned={aligned} (definitive verdict, not hedging with null)"
        )
    return False, (
        f"Expected a definitive verdict (true or false), got aligned={aligned}. "
        f"The adjudicator should commit to a classification even for "
        f"ambiguous input."
    )


def _check_ambiguous_handles_gracefully(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent handles the ambiguity gracefully.

    Graceful handling means: identifies the mixed signals (some aligned,
    some not), produces a problems list or notes the deviations, and
    includes a raw_signal excerpt.  The agent should not ignore the
    problems or pretend everything is fine.
    """
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    # Check for problems or raw_signal that acknowledges the ambiguity
    problems = data.get("problems", [])
    raw_signal = data.get("raw_signal", "")
    confidence = data.get("confidence", "")
    signals_found = []
    # Problems list should be non-empty (the text mentions deviations)
    if isinstance(problems, list) and problems:
        signals_found.append(f"problems={len(problems)}")
    # raw_signal should be present
    if isinstance(raw_signal, str) and len(raw_signal) > 10:
        signals_found.append("raw_signal present")
    # Confidence should reflect the ambiguity (medium or low)
    if confidence in ("medium", "low"):
        signals_found.append(f"confidence='{confidence}'")
    if len(signals_found) >= 2:
        return True, f"Ambiguity handled gracefully: {', '.join(signals_found)}"
    # Also accept if aligned=false (correct) even with fewer signals
    aligned = data.get("aligned")
    if aligned is False:
        return True, (
            f"aligned=false correctly identifies misalignment. "
            f"Signals: {', '.join(signals_found) or 'none'}"
        )
    return False, (
        f"Expected at least 2 of [non-empty problems, raw_signal, "
        f"medium/low confidence] or aligned=false, found: "
        f"{', '.join(signals_found) or 'none'}"
    )


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="alignment_adjudicator_clear",
        agent_file="alignment-output-adjudicator.md",
        model_policy_key="adjudicator",
        setup=_setup_clear,
        checks=[
            Check(
                description="Clear alignment text produces normalized verdict JSON",
                verify=_check_clear_has_verdict_json,
            ),
            Check(
                description="Clear alignment text classified as aligned=true",
                verify=_check_clear_aligned_true,
            ),
            Check(
                description="Clear alignment text gets high or medium confidence",
                verify=_check_clear_high_confidence,
            ),
        ],
    ),
    Scenario(
        name="alignment_adjudicator_ambiguous",
        agent_file="alignment-output-adjudicator.md",
        model_policy_key="adjudicator",
        setup=_setup_ambiguous,
        checks=[
            Check(
                description="Ambiguous alignment text produces normalized verdict JSON",
                verify=_check_ambiguous_has_verdict_json,
            ),
            Check(
                description="Ambiguous input gets definitive verdict (not null)",
                verify=_check_ambiguous_verdict_definitive,
            ),
            Check(
                description="Agent handles ambiguity gracefully (problems, confidence, raw_signal)",
                verify=_check_ambiguous_handles_gracefully,
            ),
        ],
    ),
]
