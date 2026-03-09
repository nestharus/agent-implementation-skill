"""Intent triager scenario eval.

Tests that the intent-triager agent produces a valid structured
triage signal with intent_mode, ROAL risk handoff, and budgets fields.

Scenarios:
  intent_triage_full: Complex multi-file section -> valid triage JSON
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


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

    # Write the triage prompt (mirrors triage.py prompt structure)
    prompt_path = artifacts / "intent-triage-05-prompt.md"
    triage_signal_path = signals / "intent-triage-05.json"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Intent Triage for Section 05

        ## Context
        Decide whether this section needs the full bidirectional intent cycle
        (problem + philosophy alignment with surface discovery and expansion)
        or lightweight alignment (no fresh intent expansion this cycle; if valid
        intent artifacts already exist, alignment may still use intent-judge,
        otherwise it falls back to alignment-judge).

        ## Section Artifacts (read these for grounded assessment)
        - Section spec: `{section_path}`
        - Proposal excerpt: `{proposal_path}`
        - Alignment excerpt: `{alignment_path}`
        - Codemap summary: `{codemap_path}`

        ## Section Characteristics
        - Related files: 6
        - Incoming cross-section notes: 1
        - Mode: brownfield
        - Previous solve attempts: 0
        - Summary: Event-driven order pipeline with saga pattern, multi-stage processing, and compensation logic

        ## Decision Factors

        Consider these factors when choosing intent mode:

        - **Integration breadth**: How many files and modules does this section touch?
        - **Cross-section coupling**: Are there incoming notes or dependencies from other sections?
        - **Environment uncertainty**: Is this greenfield, hybrid, or pure modification?
        - **Failure history**: Have prior attempts at this section failed?
        - **Risk of hidden constraints**: Does the summary suggest architectural complexity?

        Weigh these factors heuristically. Sections that are narrow, well-understood,
        and have no failure history lean lightweight. Sections with broad integration,
        uncertainty, or prior failures lean full.

        ## Output
        Write a JSON signal to: `{triage_signal_path}`

        ```json
        {{
          "section": "05",
          "intent_mode": "full"|"lightweight",
          "confidence": "high"|"medium"|"low",
          "risk_mode": "light"|"full",
          "risk_budget_hint": 0,
          "escalate": false,
          "budgets": {{
            "proposal_max": 5,
            "implementation_max": 5,
            "intent_expansion_max": 2,
            "max_new_surfaces_per_cycle": 8,
            "max_new_axes_total": 6
          }},
          "reason": "<why this mode was chosen>"
        }}
        ```
    """), encoding="utf-8")

    return prompt_path


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _check_triage_signal_exists(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal JSON was written."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "intent-triage-05.json")
    if not signal_path.exists():
        return False, f"Signal file not written: {signal_path}"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Signal file is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return False, f"Signal is not a JSON object: {type(data)}"
    return True, "Triage signal JSON written and parseable"


def _check_triage_has_mode(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has intent_mode field."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "intent-triage-05.json")
    if not signal_path.exists():
        return False, "Signal file not written"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Signal file is not valid JSON"
    mode = data.get("intent_mode", "")
    if mode in ("full", "lightweight"):
        return True, f"intent_mode={mode}"
    return False, f"intent_mode missing or invalid: '{mode}'"


def _check_triage_has_budgets(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has budgets object."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "intent-triage-05.json")
    if not signal_path.exists():
        return False, "Signal file not written"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Signal file is not valid JSON"
    budgets = data.get("budgets")
    if not isinstance(budgets, dict):
        return False, f"budgets field missing or not a dict: {budgets}"
    # Check for at least one expected budget key
    expected_keys = {"proposal_max", "implementation_max"}
    present = expected_keys & set(budgets.keys())
    if present:
        return True, f"budgets has keys: {sorted(present)}"
    return False, f"budgets missing expected keys, has: {list(budgets.keys())}"


def _check_triage_has_risk_handoff(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has ROAL handoff fields."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "intent-triage-05.json")
    if not signal_path.exists():
        return False, "Signal file not written"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Signal file is not valid JSON"
    risk_mode = data.get("risk_mode", "")
    budget_hint = data.get("risk_budget_hint")
    if risk_mode not in ("light", "full"):
        return False, f"risk_mode missing or invalid: '{risk_mode}'"
    if not isinstance(budget_hint, int) or budget_hint < 0:
        return False, f"risk_budget_hint missing or invalid: {budget_hint!r}"
    return True, f"risk_mode={risk_mode}, risk_budget_hint={budget_hint}"


def _check_triage_has_reason(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify triage signal has a non-empty reason."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "intent-triage-05.json")
    if not signal_path.exists():
        return False, "Signal file not written"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Signal file is not valid JSON"
    reason = data.get("reason", "")
    if reason and len(reason) > 5:
        return True, f"reason present ({len(reason)} chars)"
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
                description="Signal has budgets object with expected keys",
                verify=_check_triage_has_budgets,
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
