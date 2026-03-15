"""Alignment judge scenario evals.

Tests that the alignment-judge agent correctly detects alignment and
misalignment between proposals and implementation output.

The alignment-judge reads proposal/alignment excerpts and implementation
work, then produces a narrative verdict (ALIGNED / PROBLEMS /
UNDERSPECIFIED) plus a structured JSON block:
    {"frame_ok": true, "aligned": true, "problems": []}

These scenarios dispatch the real agent with pre-seeded artifacts and
check the structured JSON verdict and narrative output.

Scenarios:
  alignment_judge_aligned: Well-aligned implementation -> aligned=true
  alignment_judge_misaligned: Scope-drifted implementation -> aligned=false
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_verdict_json(text: str) -> dict | None:
    """Extract the structured verdict JSON block from agent output.

    The agent is instructed to emit a fenced JSON block at the end of
    its response.  Try fenced block first, then raw JSON with the
    expected keys.
    """
    # Try fenced JSON block (```json ... ```)
    match = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if "aligned" in data:
                return data
        except json.JSONDecodeError:
            pass
    # Fallback: find any JSON object containing "aligned" key
    for m in re.finditer(r"\{[^{}]*\"aligned\"[^{}]*\}", text):
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ALIGNED_SECTION_SPEC = textwrap.dedent("""\
    # Section 05: Rate Limiter Middleware

    ## Problem
    The API server needs per-endpoint rate limiting to prevent abuse.
    Existing endpoints have no throttling; heavy callers can exhaust
    backend resources.  The rate limiter must be configurable per route
    and use a sliding-window algorithm backed by Redis.

    ## Requirements
    - REQ-01: Sliding-window rate limiting per IP per endpoint
    - REQ-02: Configurable limits via route decorator or config file
    - REQ-03: Return 429 Too Many Requests with Retry-After header
    - REQ-04: Graceful degradation when Redis is unavailable (allow traffic)

    ## Constraints
    - Must integrate with the existing middleware chain in api/middleware.py
    - Must not add latency > 5ms per request under normal operation
""")

_ALIGNED_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 05

    ## Problem
    API endpoints lack rate limiting.  Heavy callers exhaust backend
    resources.  A sliding-window rate limiter backed by Redis is needed.

    ## Proposed Changes

    ### File: `api/middleware.py`
    Add a `RateLimitMiddleware` class that hooks into the existing
    middleware chain.  On each request it increments a sliding-window
    counter in Redis keyed by `(client_ip, endpoint)`.  If the count
    exceeds the configured limit, return 429 with a Retry-After header.

    ### File: `api/rate_config.py` (NEW)
    Configuration loader for per-route rate limits.  Reads from a YAML
    config file or accepts decorator-based overrides.

    ### File: `utils/redis_client.py`
    Add a `SlidingWindowCounter` helper encapsulating the MULTI/EXEC
    Redis pipeline for atomic increment-and-expire.

    ## Integration Points
    - Resolved anchor: `api.middleware` chain insertion point
    - Resolved contract: `RateLimitConfig` schema consumed by middleware

    ## Failure Modes
    - Redis unavailable: middleware falls back to allow-all (graceful degradation)
""")

_ALIGNED_IMPLEMENTATION = textwrap.dedent("""\
    # Implementation Output: Section 05

    ## Changes Made

    ### api/middleware.py
    Added `RateLimitMiddleware` to the middleware chain.  The class
    reads rate-limit configuration from `RateLimitConfig`, resolves
    the client IP from the request, and queries a Redis sliding-window
    counter.  When the limit is exceeded it returns a 429 response with
    a `Retry-After` header computed from the window expiry.

    When Redis is unavailable (connection error or timeout), the
    middleware logs a warning and allows the request through, satisfying
    the graceful-degradation requirement.

    ### api/rate_config.py (NEW)
    Created `RateLimitConfig` that loads per-route limits from
    `rate_limits.yaml`.  Supports decorator-based overrides via
    `@rate_limit(max_requests=100, window_seconds=60)`.

    ### utils/redis_client.py
    Added `SlidingWindowCounter` using a Redis sorted-set with
    MULTI/EXEC for atomic increment-and-expire.  Includes a
    `CircuitBreaker` wrapper that opens after 3 consecutive Redis
    failures and retries after 30 seconds.

    ## Verification
    - Unit tests for SlidingWindowCounter with mock Redis
    - Integration test for middleware chain with rate limiting
    - Verified 429 response includes correct Retry-After value
    - Verified graceful degradation when Redis is stopped
""")

_MISALIGNED_SECTION_SPEC = textwrap.dedent("""\
    # Section 06: Webhook Delivery System

    ## Problem
    External integrations need to be notified when domain events occur
    (order placed, payment completed, user registered).  The system must
    deliver HTTP POST callbacks to registered webhook endpoints with
    retry logic and delivery receipts.

    ## Requirements
    - REQ-01: Register webhook URLs per event type
    - REQ-02: Deliver POST callbacks with signed payloads (HMAC-SHA256)
    - REQ-03: Retry failed deliveries with exponential backoff (max 5 retries)
    - REQ-04: Record delivery status (success, failed, pending) per event
    - REQ-05: Provide a webhook management API (CRUD for subscriptions)

    ## Constraints
    - Deliveries must be async (not block the triggering request)
    - Must not retry on 4xx responses (only 5xx and network errors)
""")

_MISALIGNED_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 06

    ## Problem
    External integrations require webhook notifications for domain events.
    The system must deliver signed HTTP POST callbacks with retry logic.

    ## Proposed Changes

    ### File: `webhooks/dispatcher.py` (NEW)
    Create a `WebhookDispatcher` that accepts domain events and delivers
    them to registered endpoints.  Uses HMAC-SHA256 signing of the
    payload body.  Retries on 5xx / network errors with exponential
    backoff (max 5 attempts).  Does NOT retry on 4xx.

    ### File: `webhooks/registry.py` (NEW)
    Subscription registry backed by the database.  Stores webhook URL,
    event types, signing secret, and active/inactive status.

    ### File: `webhooks/models.py` (NEW)
    Data models: `WebhookSubscription`, `DeliveryAttempt`, `DeliveryReceipt`.

    ### File: `api/webhook_routes.py` (NEW)
    CRUD API for webhook subscriptions.  Endpoints: POST /webhooks,
    GET /webhooks, PUT /webhooks/:id, DELETE /webhooks/:id.

    ## Integration Points
    - Resolved anchor: `events.bus` for subscribing to domain events
    - Resolved contract: `WebhookSubscription` schema

    ## Failure Modes
    - Target endpoint permanently down: mark subscription inactive after
      exhausting retries for 3 consecutive events
""")

_MISALIGNED_IMPLEMENTATION = textwrap.dedent("""\
    # Implementation Output: Section 06

    ## Changes Made

    ### notifications/email_sender.py (NEW)
    Created an `EmailNotificationService` that sends email notifications
    to users when domain events occur.  Uses SMTP with TLS for secure
    delivery.  Supports HTML and plain-text templates with variable
    substitution.

    ### notifications/templates.py (NEW)
    Template engine for email notifications.  Loads Jinja2 templates
    from a `templates/` directory.  Supports per-event-type templates
    with fallback to a default template.

    ### notifications/models.py (NEW)
    Data models: `EmailNotification`, `NotificationTemplate`,
    `DeliveryStatus`.  Tracks which users have been notified and
    delivery success/failure.

    ### api/notification_routes.py (NEW)
    API for managing email notification preferences.  Endpoints:
    GET /notifications/preferences, PUT /notifications/preferences.

    ## Verification
    - Unit tests for template rendering with sample events
    - Integration test for SMTP delivery with mock server
    - Verified HTML and plain-text template fallback
""")


# ---------------------------------------------------------------------------
# Setup: aligned scenario
# ---------------------------------------------------------------------------

def _setup_aligned(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for a well-aligned implementation."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    signals = artifacts / "signals"
    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-05.md"
    section_path.write_text(_ALIGNED_SECTION_SPEC, encoding="utf-8")

    # Integration proposal
    proposal_path = proposals / "section-05-integration-proposal.md"
    proposal_path.write_text(_ALIGNED_PROPOSAL, encoding="utf-8")

    # Implementation output
    impl_path = proposals / "section-05-implementation-output.md"
    impl_path.write_text(_ALIGNED_IMPLEMENTATION, encoding="utf-8")

    # Minimal codespace
    api_dir = codespace / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "middleware.py").write_text(textwrap.dedent("""\
        class MiddlewareChain:
            def __init__(self):
                self.middlewares = []
            def add(self, mw):
                self.middlewares.append(mw)
    """), encoding="utf-8")
    utils_dir = codespace / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "__init__.py").write_text("", encoding="utf-8")
    (utils_dir / "redis_client.py").write_text(textwrap.dedent("""\
        class RedisClient:
            def __init__(self, host="localhost", port=6379):
                self.host = host
                self.port = port
    """), encoding="utf-8")

    # Build the prompt -- inline the artifacts so the agent can evaluate
    # alignment without needing to read files (works for both agentic
    # and text-completion models).
    prompt_path = artifacts / "alignment-judge-05-prompt.md"
    prompt_path.write_text(
        "# Task: Alignment Check for Section 05\n\n"
        "## Alignment Excerpt (Problem Definition)\n\n"
        f"{_ALIGNED_SECTION_SPEC}\n\n"
        "## Integration Proposal\n\n"
        f"{_ALIGNED_PROPOSAL}\n\n"
        "## Implementation Output (Work Product)\n\n"
        f"{_ALIGNED_IMPLEMENTATION}\n\n"
        "## Instructions\n\n"
        "You are the alignment judge.  Read the alignment excerpt and\n"
        "proposal to understand the PROBLEM and CONSTRAINTS.  Then read\n"
        "the implementation output to determine whether it is directionally\n"
        "coherent with the proposal.\n\n"
        "Produce your verdict: ALIGNED, PROBLEMS, or UNDERSPECIFIED.\n\n"
        "Then include a structured JSON verdict block:\n"
        "```json\n"
        '{"frame_ok": true, "aligned": true|false, "problems": []}\n'
        "```\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: misaligned scenario
# ---------------------------------------------------------------------------

def _setup_misaligned(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for a scope-drifted implementation."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    signals = artifacts / "signals"
    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-06.md"
    section_path.write_text(_MISALIGNED_SECTION_SPEC, encoding="utf-8")

    # Integration proposal (about webhooks)
    proposal_path = proposals / "section-06-integration-proposal.md"
    proposal_path.write_text(_MISALIGNED_PROPOSAL, encoding="utf-8")

    # Implementation output (about email notifications -- wrong concern)
    impl_path = proposals / "section-06-implementation-output.md"
    impl_path.write_text(_MISALIGNED_IMPLEMENTATION, encoding="utf-8")

    # Minimal codespace
    events_dir = codespace / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "__init__.py").write_text("", encoding="utf-8")
    (events_dir / "bus.py").write_text(textwrap.dedent("""\
        class EventBus:
            def __init__(self):
                self.subscribers = {}
            def subscribe(self, event_type, handler):
                self.subscribers.setdefault(event_type, []).append(handler)
    """), encoding="utf-8")

    # Build the prompt -- proposal is about webhooks, implementation is
    # about email notifications (clear scope drift).
    prompt_path = artifacts / "alignment-judge-06-prompt.md"
    prompt_path.write_text(
        "# Task: Alignment Check for Section 06\n\n"
        "## Alignment Excerpt (Problem Definition)\n\n"
        f"{_MISALIGNED_SECTION_SPEC}\n\n"
        "## Integration Proposal\n\n"
        f"{_MISALIGNED_PROPOSAL}\n\n"
        "## Implementation Output (Work Product)\n\n"
        f"{_MISALIGNED_IMPLEMENTATION}\n\n"
        "## Instructions\n\n"
        "You are the alignment judge.  Read the alignment excerpt and\n"
        "proposal to understand the PROBLEM and CONSTRAINTS.  Then read\n"
        "the implementation output to determine whether it is directionally\n"
        "coherent with the proposal.\n\n"
        "Produce your verdict: ALIGNED, PROBLEMS, or UNDERSPECIFIED.\n\n"
        "Then include a structured JSON verdict block:\n"
        "```json\n"
        '{"frame_ok": true, "aligned": true|false, "problems": []}\n'
        "```\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Check functions: aligned scenario
# ---------------------------------------------------------------------------

def _check_aligned_has_verdict_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a structured JSON verdict block."""
    data = _extract_verdict_json(agent_output)
    if data is not None:
        return True, f"Verdict JSON found: {json.dumps(data)}"
    return False, "No structured verdict JSON block found in agent output"


def _check_aligned_verdict_true(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the verdict JSON has aligned=true."""
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    aligned = data.get("aligned")
    if aligned is True:
        return True, "aligned=true (correct for well-aligned implementation)"
    return False, f"Expected aligned=true, got {aligned}"


def _check_aligned_has_rationale(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the narrative output contains ALIGNED verdict text."""
    upper = agent_output.upper()
    if "ALIGNED" in upper:
        # Make sure it is the ALIGNED verdict, not just PROBLEMS or
        # UNDERSPECIFIED containing the word
        if "PROBLEMS:" in upper or "UNDERSPECIFIED:" in upper:
            return False, (
                "Output contains PROBLEMS or UNDERSPECIFIED verdict, "
                "expected ALIGNED"
            )
        return True, "Narrative contains ALIGNED verdict"
    return False, "Narrative does not contain ALIGNED verdict keyword"


# ---------------------------------------------------------------------------
# Check functions: misaligned scenario
# ---------------------------------------------------------------------------

def _check_misaligned_has_verdict_json(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a structured JSON verdict block."""
    data = _extract_verdict_json(agent_output)
    if data is not None:
        return True, f"Verdict JSON found: {json.dumps(data)}"
    return False, "No structured verdict JSON block found in agent output"


def _check_misaligned_verdict_false(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the verdict JSON has aligned=false."""
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    aligned = data.get("aligned")
    if aligned is False:
        return True, "aligned=false (correct for scope-drifted implementation)"
    return False, f"Expected aligned=false, got {aligned}"


def _check_misaligned_identifies_drift(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent identifies the specific misalignment (scope drift).

    The proposal is about webhook delivery; the implementation built
    email notifications instead.  The agent should mention this
    divergence.
    """
    lower = agent_output.lower()
    # The agent should reference the mismatch between webhook and email
    webhook_mentioned = any(
        term in lower for term in ["webhook", "http post callback", "http callback"]
    )
    email_mentioned = any(
        term in lower for term in ["email", "smtp", "notification"]
    )
    if webhook_mentioned and email_mentioned:
        return True, (
            "Agent mentions both webhook (expected) and email (actual), "
            "identifying the scope drift"
        )
    if not webhook_mentioned:
        return False, "Agent does not mention webhooks (the expected concern)"
    return False, (
        "Agent does not mention email/SMTP/notifications (the drifted concern)"
    )


def _check_misaligned_no_hallucinated_problems(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent does not hallucinate problems beyond the scope drift.

    The primary issue is clear scope drift (webhook vs email).  The
    agent should not invent additional problems unrelated to this
    divergence (e.g., code style, naming, performance concerns not in
    the alignment).
    """
    data = _extract_verdict_json(agent_output)
    if data is None:
        return False, "No verdict JSON found"
    problems = data.get("problems", [])
    if not problems:
        # No problems listed -- the agent used narrative-only PROBLEMS
        # verdict.  That is acceptable; we cannot check for hallucinations
        # in the JSON if no problems array is populated.
        return True, "No problems array to check for hallucinations"
    # All listed problems should relate to the webhook/email divergence
    # or dropped requirements.  Flag any that seem unrelated.
    unrelated = []
    drift_keywords = [
        "webhook", "email", "notification", "smtp", "scope",
        "drift", "diverge", "wrong", "different", "mismatch",
        "http post", "callback", "delivery", "registry",
        "subscription", "hmac", "signing", "retry", "backoff",
        "dropped", "missing", "not addressed", "silently",
    ]
    for problem in problems:
        problem_lower = problem.lower()
        if not any(kw in problem_lower for kw in drift_keywords):
            unrelated.append(problem)
    if unrelated:
        return False, (
            f"Potentially hallucinated problems unrelated to scope drift: "
            f"{unrelated}"
        )
    return True, f"All {len(problems)} listed problems relate to the scope drift"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="alignment_judge_aligned",
        agent_file="alignment-judge.md",
        model_policy_key="alignment",
        setup=_setup_aligned,
        checks=[
            Check(
                description="Output contains structured verdict JSON",
                verify=_check_aligned_has_verdict_json,
            ),
            Check(
                description="Verdict JSON has aligned=true",
                verify=_check_aligned_verdict_true,
            ),
            Check(
                description="Narrative contains ALIGNED verdict",
                verify=_check_aligned_has_rationale,
            ),
        ],
    ),
    Scenario(
        name="alignment_judge_misaligned",
        agent_file="alignment-judge.md",
        model_policy_key="alignment",
        setup=_setup_misaligned,
        checks=[
            Check(
                description="Output contains structured verdict JSON",
                verify=_check_misaligned_has_verdict_json,
            ),
            Check(
                description="Verdict JSON has aligned=false",
                verify=_check_misaligned_verdict_false,
            ),
            Check(
                description="Agent identifies webhook-vs-email scope drift",
                verify=_check_misaligned_identifies_drift,
            ),
            Check(
                description="No hallucinated problems beyond scope drift",
                verify=_check_misaligned_no_hallucinated_problems,
            ),
        ],
    ),
]
