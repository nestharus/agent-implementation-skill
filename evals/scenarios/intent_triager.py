"""Intent triager scenario eval.

Tests that the intent-triager agent produces a valid structured
triage signal with intent_mode and ROAL risk handoff fields.

Scenarios:
  intent_triage_full: Complex multi-file section -> valid triage JSON
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


def _extract_json_from_output(text: str) -> dict | None:
    """Extract a JSON object from agent stdout.

    GLM (text-completion model) outputs JSON in stdout, often wrapped
    in markdown code fences.  Try fenced block first, then raw parse,
    then fall back to parsing the TRIAGE summary line.
    """
    # Try fenced JSON block (greedy to capture nested braces)
    match = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try JSON objects in text (handles nested braces)
    for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text):
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "intent_mode" in data:
                return data
        except json.JSONDecodeError:
            continue
    # Last resort: parse TRIAGE summary line
    # e.g. "TRIAGE: 05 → full (reason text)"
    triage_match = re.search(
        r"TRIAGE:\s*\S+\s*→\s*(full|lightweight|cached)\s*\(([^)]+)\)",
        text,
    )
    if triage_match:
        mode = triage_match.group(1)
        reason = triage_match.group(2)
        return {
            "section": "05",
            "intent_mode": mode,
            "confidence": "medium",
            "risk_mode": "full" if mode == "full" else "light",
            "risk_budget_hint": 2 if mode == "full" else 0,
            "escalate": False,
            "reason": reason,
        }
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_COMPLEX_SECTION_SPEC = textwrap.dedent("""\
    # Section 05: Event-Driven Order Pipeline

    ## Problem
    The order processing system needs to transition from synchronous
    request-response to an event-driven pipeline. Orders flow through
    validation, inventory reservation, payment capture, and fulfillment
    stages. Each stage publishes domain events that downstream stages
    consume. Failed stages must trigger compensating transactions.

    ## Requirements
    - REQ-01: Event bus abstraction with at-least-once delivery guarantee
    - REQ-02: Saga pattern for multi-stage order processing with compensation
    - REQ-03: Idempotency keys on all stage handlers to prevent duplicate processing
    - REQ-04: Dead letter queue for events that fail after max retries
    - REQ-05: Distributed tracing correlation IDs across all event handlers
    - REQ-06: Stage-specific retry policies (exponential backoff with jitter)
    - REQ-07: Event schema registry with backward-compatible evolution

    ## Constraints
    - Must integrate with existing OrderRepository and PaymentService
    - Must support both in-process (testing) and distributed (production) event bus
    - Must not break existing synchronous order API during migration
    - Event schemas must be versioned and backward-compatible

    ## Related Files

    ### orders/processor.py
    Current synchronous order processing logic -- needs decomposition.

    ### orders/models.py
    Order and OrderItem data models.

    ### payments/service.py
    Payment capture service -- becomes a saga stage.

    ### inventory/reservation.py
    Inventory reservation logic -- becomes a saga stage.

    ### api/order_routes.py
    Order API endpoints -- must continue working during migration.

    ### events/bus.py
    Existing basic event emitter -- needs upgrade to support guarantees.
""")

_PROPOSAL_EXCERPT = textwrap.dedent("""\
    # Proposal Excerpt: Section 05

    Decompose synchronous OrderProcessor into event-driven saga stages.
    Each stage (validate, reserve_inventory, capture_payment, fulfill)
    becomes an independent event handler. Introduce SagaCoordinator to
    manage stage sequencing and compensating transactions.

    Key changes:
    - New events/ module with EventBus, SagaCoordinator, EventStore
    - OrderProcessor decomposed into 4 stage handlers
    - PaymentService wrapped as saga-aware PaymentStage
    - InventoryReservation wrapped as saga-aware ReservationStage
    - New dead_letter_queue.py for failed event handling
    - Migration path: dual-write (sync + events) during transition

    Cross-section impact: Section 08 (monitoring) needs event metrics.
    Section 11 (testing) needs in-process event bus for integration tests.
""")

_ALIGNMENT_EXCERPT = textwrap.dedent("""\
    # Alignment Excerpt: Section 05

    The proposal correctly identifies the saga pattern as the right
    approach for multi-stage order processing. However, the compensation
    logic needs more detail -- especially for the inventory reservation
    rollback case where partial reservations may have been committed.

    Open concerns:
    - Event schema versioning strategy not fully specified
    - Retry policy configuration per-stage vs global unclear
    - Dead letter queue monitoring integration with section 08
""")

_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## orders/
    - `orders/processor.py` - Synchronous order processing (400 lines)
    - `orders/models.py` - Order data models
    - `orders/validators.py` - Order validation rules

    ## payments/
    - `payments/service.py` - Payment capture and refund
    - `payments/models.py` - Payment data models

    ## inventory/
    - `inventory/reservation.py` - Stock reservation logic
    - `inventory/models.py` - Inventory data models

    ## events/
    - `events/bus.py` - Basic event emitter (no persistence)
    - `events/types.py` - Event type definitions

    ## api/
    - `api/order_routes.py` - Order API endpoints
    - `api/payment_routes.py` - Payment API endpoints
""")


# ---------------------------------------------------------------------------
# Setup function
# ---------------------------------------------------------------------------

def _setup_full_triage(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for a complex section that needs full triage."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-05.md"
    section_path.write_text(_COMPLEX_SECTION_SPEC, encoding="utf-8")

    # Proposal and alignment excerpts
    proposal_path = sections / "section-05-proposal-excerpt.md"
    proposal_path.write_text(_PROPOSAL_EXCERPT, encoding="utf-8")

    alignment_path = sections / "section-05-alignment-excerpt.md"
    alignment_path.write_text(_ALIGNMENT_EXCERPT, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CODEMAP, encoding="utf-8")

    # Codespace with relevant files
    for d in ["orders", "payments", "inventory", "events", "api"]:
        (codespace / d).mkdir(parents=True, exist_ok=True)
        (codespace / d / "__init__.py").write_text("", encoding="utf-8")

    (codespace / "orders" / "processor.py").write_text(textwrap.dedent("""\
        from .models import Order
        from payments.service import PaymentService
        from inventory.reservation import InventoryReservation

        class OrderProcessor:
            def __init__(self, payment_svc: PaymentService,
                         inventory: InventoryReservation):
                self.payment_svc = payment_svc
                self.inventory = inventory

            def process_order(self, order: Order) -> str:
                self.inventory.reserve(order.items)
                self.payment_svc.capture(order.total, order.payment_method)
                order.status = "fulfilled"
                return order.id
    """), encoding="utf-8")

    (codespace / "orders" / "models.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass, field

        @dataclass
        class OrderItem:
            sku: str
            quantity: int
            price: float

        @dataclass
        class Order:
            id: str
            items: list[OrderItem] = field(default_factory=list)
            status: str = "pending"
            payment_method: str = ""

            @property
            def total(self) -> float:
                return sum(i.price * i.quantity for i in self.items)
    """), encoding="utf-8")

    # Incoming cross-section note
    notes_dir = artifacts / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "from-08-to-05.md").write_text(textwrap.dedent("""\
        # Cross-Section Note: Section 08 -> Section 05

        Section 08 (monitoring) needs event bus metrics exposed for
        dashboard integration. Specifically:
        - Events published per stage per minute
        - Dead letter queue depth
        - Saga completion/failure rates
    """), encoding="utf-8")

    # Write the triage prompt — inline condensed content so GLM
    # (text-completion model without file I/O) can process it.
    # Full artifact text is too long for GLM; provide key signals inline.
    prompt_path = artifacts / "intent-triage-05-prompt.md"
    prompt_path.write_text(
        "# Task: Intent Triage for Section 05\n\n"
        "## Context\n"
        "Decide whether this section needs the full bidirectional intent cycle\n"
        "or lightweight alignment.\n\n"
        "## Section Summary\n"
        "Event-driven order pipeline: transition synchronous OrderProcessor to\n"
        "event-driven saga stages (validate, reserve_inventory, capture_payment,\n"
        "fulfill). Requires event bus with at-least-once delivery, saga pattern\n"
        "with compensating transactions, idempotency keys, dead letter queue,\n"
        "distributed tracing, stage-specific retry policies, and event schema\n"
        "registry with backward-compatible evolution.\n\n"
        "## Complexity Signals\n"
        "- Related files: 6 (orders/processor.py, orders/models.py, payments/service.py,\n"
        "  inventory/reservation.py, api/order_routes.py, events/bus.py)\n"
        "- Modules affected: 4 (orders, payments, inventory, events/api)\n"
        "- Incoming cross-section notes: 1 (Section 08 needs event bus metrics)\n"
        "- Cross-section impact: Section 08 (monitoring) + Section 11 (testing)\n"
        "- Mode: brownfield\n"
        "- Previous solve attempts: 0\n"
        "- New files required: events module (EventBus, SagaCoordinator, EventStore)\n"
        "- Database migration: yes (ordering constraint: must run before deploy)\n"
        "- External API dependency: exchange rate service with failure modes\n\n"
        "## Alignment Concerns\n"
        "- Compensation logic for partial inventory reservations underspecified\n"
        "- Event schema versioning strategy not fully specified\n"
        "- Retry policy config per-stage vs global unclear\n"
        "- Dead letter queue monitoring integration with section 08 open\n\n"
        "## Output\n"
        "You cannot write to files. Output ONLY a JSON object as your response:\n\n"
        '{"section": "05", "intent_mode": "full", "confidence": "high", '
        '"risk_mode": "full", "risk_budget_hint": 2, "escalate": false, '
        '"reason": "explanation"}\n',
        encoding="utf-8",
    )

    return prompt_path


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _read_signal(
    planspace: Path, agent_output: str,
) -> tuple[dict | None, str]:
    """Read triage signal from file or parse from agent stdout."""
    signal_path = planspace / "artifacts" / "signals" / "intent-triage-05.json"
    if signal_path.exists():
        try:
            data = json.loads(signal_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data, "from signal file"
        except json.JSONDecodeError:
            pass
    data = _extract_json_from_output(agent_output)
    if data is not None:
        return data, "from agent stdout"
    return None, "no signal found (file missing, stdout empty or unparseable)"


def _check_triage_signal_exists(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal JSON was produced."""
    data, source = _read_signal(planspace, agent_output)
    if data is None:
        return False, source
    return True, f"Triage signal JSON parseable ({source})"


def _check_triage_has_mode(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has intent_mode field."""
    data, source = _read_signal(planspace, agent_output)
    if data is None:
        return False, source
    mode = data.get("intent_mode", "")
    if mode in ("full", "lightweight"):
        return True, f"intent_mode={mode} ({source})"
    return False, f"intent_mode missing or invalid: '{mode}'"


def _check_triage_has_risk_handoff(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has ROAL handoff fields."""
    data, source = _read_signal(planspace, agent_output)
    if data is None:
        return False, source
    risk_mode = data.get("risk_mode", "")
    budget_hint = data.get("risk_budget_hint")
    if risk_mode not in ("light", "full"):
        return False, f"risk_mode missing or invalid: '{risk_mode}'"
    if not isinstance(budget_hint, int) or budget_hint < 0:
        return False, f"risk_budget_hint missing or invalid: {budget_hint!r}"
    return True, f"risk_mode={risk_mode}, risk_budget_hint={budget_hint} ({source})"


def _check_triage_has_reason(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has a non-empty reason."""
    data, source = _read_signal(planspace, agent_output)
    if data is None:
        return False, source
    reason = data.get("reason", "")
    if reason and len(reason) > 5:
        return True, f"reason present ({len(reason)} chars, {source})"
    return False, f"reason field missing or too short: '{reason}'"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="intent_triage_full",
        agent_file="intent-triager.md",
        model_policy_key="intent_triage",
        setup=_setup_full_triage,
        checks=[
            Check(
                description="Triage signal JSON written and parseable",
                verify=_check_triage_signal_exists,
            ),
            Check(
                description="Signal has valid intent_mode (full or lightweight)",
                verify=_check_triage_has_mode,
            ),
            Check(
                description="Signal has ROAL risk handoff fields",
                verify=_check_triage_has_risk_handoff,
            ),
            Check(
                description="Signal has non-empty reason",
                verify=_check_triage_has_reason,
            ),
        ],
    ),
]
