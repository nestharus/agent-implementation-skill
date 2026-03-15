"""Implementation-strategist scenario evals.

Tests that the implementation-strategist agent correctly handles
single-file and multi-file implementation tasks: produces non-empty
output, mentions the target files from the proposal, and does not
invent files or architecture outside the proposal scope.

Scenarios:
  implementation_strategist_simple: Single-file brownfield change
  implementation_strategist_complex: Multi-file cross-module implementation
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures — simple (single-file)
# ---------------------------------------------------------------------------

_SIMPLE_SECTION_SPEC = textwrap.dedent("""\
    # Section 05: Configuration Validation

    ## Problem
    The YAML configuration loader accepts any input without validation.
    Malformed or missing keys cause cryptic runtime errors deep in the
    application. We need a validation layer that checks required keys,
    types, and value ranges at load time and reports clear errors.

    ## Requirements
    - REQ-01: Validate required top-level keys at config load time
    - REQ-02: Type-check values against a schema definition
    - REQ-03: Report all validation errors at once (not fail-fast)

    ## Constraints
    - Must integrate with the existing `load_config()` function
    - Must not change the return type of `load_config()`

    ## Related Files

    ### utils/config.py
    Existing configuration loader — validation layer plugs in here.
""")

_SIMPLE_INTEGRATION_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 05

    ## Problem
    Configuration loading accepts unvalidated input, causing late
    runtime errors. A validation step is needed at load time.

    ## Resolved Anchors
    - `utils/config.py:load_config` — entry point for adding validation

    ## Proposed Changes

    ### File: `utils/config.py`
    Add a `_validate_config(data: dict) -> list[str]` private function
    that checks required keys (`database`, `server`, `logging`) and
    their types. Call `_validate_config` from `load_config` after YAML
    parsing. If errors are non-empty, raise `ConfigValidationError`
    with a concatenated error message.

    Add a `ConfigValidationError(Exception)` class in the same file.

    ## Impact Assessment
    - Single file modification (`utils/config.py`)
    - No interface changes (return type of `load_config` unchanged)
    - No cross-section dependencies
    - No database migrations
""")

_SIMPLE_MICROSTRATEGY = textwrap.dedent("""\
    # Microstrategy: Section 05

    ## Steps
    1. Add `ConfigValidationError` exception class and `_validate_config`
       helper to `utils/config.py`, then call it from `load_config`.
""")

_SIMPLE_CONFIG_PY = textwrap.dedent("""\
    import yaml
    from pathlib import Path


    def load_config(path: str = "config.yaml") -> dict:
        \"\"\"Load application configuration from a YAML file.\"\"\"
        with open(path) as f:
            return yaml.safe_load(f)
""")

_SIMPLE_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## utils/
    - `utils/config.py` - YAML configuration loader
    - `utils/logging.py` - Structured logging setup

    ## api/
    - `api/routes.py` - HTTP route definitions
""")


# ---------------------------------------------------------------------------
# Fixtures — complex (multi-file cross-module)
# ---------------------------------------------------------------------------

_COMPLEX_SECTION_SPEC = textwrap.dedent("""\
    # Section 06: Order Processing Pipeline

    ## Problem
    The e-commerce system needs an order processing pipeline that
    validates inventory, calculates totals with tax and discount rules,
    persists orders, and emits events for downstream fulfilment.
    Currently these concerns are scattered across ad-hoc scripts.

    ## Requirements
    - REQ-01: Validate inventory availability before accepting an order
    - REQ-02: Calculate order totals with configurable tax and discount rules
    - REQ-03: Persist orders atomically via the existing repository layer
    - REQ-04: Emit order-lifecycle events through the existing event bus
    - REQ-05: Expose a unified `submit_order` entry point for the API layer

    ## Constraints
    - Must reuse the existing `InventoryService` for stock checks
    - Must reuse the existing `OrderRepository` for persistence
    - Must reuse the existing `EventBus` for event emission
    - Must not bypass the `PricingEngine` for tax/discount calculations

    ## Related Files

    ### inventory/service.py
    Existing inventory service — stock availability checks.

    ### orders/repository.py
    Existing order persistence layer — atomic save.

    ### events/bus.py
    Existing event bus — order lifecycle events.

    ### pricing/engine.py
    Existing pricing engine — tax and discount calculation.

    ### api/order_routes.py
    Existing API routes — needs new `submit_order` endpoint wiring.

    ### orders/models.py
    Existing order data models — may need a status enum extension.
""")

_COMPLEX_INTEGRATION_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 06

    ## Problem
    Order processing logic is scattered across ad-hoc scripts.
    A unified pipeline is needed that validates, prices, persists,
    and emits events for each order.

    ## Resolved Anchors
    - `inventory/service.py:InventoryService.check_availability` — stock check
    - `orders/repository.py:OrderRepository.save` — atomic persistence
    - `events/bus.py:EventBus.publish` — lifecycle event emission
    - `pricing/engine.py:PricingEngine.calculate` — tax and discounts
    - `api/order_routes.py:router` — API endpoint registration
    - `orders/models.py:OrderStatus` — status enum (extend with VALIDATED)

    ## Resolved Contracts
    - `InventoryService.check_availability(sku, qty) -> bool`
    - `OrderRepository.save(order) -> str` (returns order_id)
    - `EventBus.publish(event_type, payload) -> None`
    - `PricingEngine.calculate(items, rules) -> PricingResult`

    ## Proposed Changes

    ### File: `orders/pipeline.py` (NEW)
    Create `OrderPipeline` class that orchestrates the submit flow:
    1. Validate inventory via `InventoryService.check_availability`
    2. Calculate totals via `PricingEngine.calculate`
    3. Persist via `OrderRepository.save`
    4. Emit `order.created` via `EventBus.publish`

    Inject all dependencies through the constructor.

    ### File: `orders/models.py`
    Add `VALIDATED` value to the `OrderStatus` enum. No existing
    values are changed.

    ### File: `api/order_routes.py`
    Add a `POST /orders/submit` endpoint that instantiates
    `OrderPipeline` with the application's service instances and
    calls `pipeline.submit(order_request)`.

    ### File: `orders/repository.py`
    No changes to the repository itself. Listed for context — the
    pipeline calls `save` through the existing interface.

    ## Impact Assessment
    - 3 files modified, 1 new file
    - Cross-module: orders, inventory, pricing, events, api
    - No database migrations (OrderStatus is application-level enum)
    - No breaking interface changes
""")

_COMPLEX_MICROSTRATEGY = textwrap.dedent("""\
    # Microstrategy: Section 06

    ## Steps
    1. Extend `OrderStatus` enum in `orders/models.py` with VALIDATED.
    2. Create `orders/pipeline.py` with `OrderPipeline` class that
       orchestrates inventory check, pricing, persistence, and event
       emission through constructor-injected dependencies.
    3. Wire `POST /orders/submit` endpoint in `api/order_routes.py`
       to call `OrderPipeline.submit`.
""")

_COMPLEX_INVENTORY_SERVICE_PY = textwrap.dedent("""\
    class InventoryService:
        \"\"\"Manages product inventory levels.\"\"\"

        def check_availability(self, sku: str, quantity: int) -> bool:
            \"\"\"Return True if sufficient stock exists for the given SKU.\"\"\"
            # Real implementation queries inventory DB
            return True

        def reserve(self, sku: str, quantity: int) -> str:
            \"\"\"Reserve stock and return reservation ID.\"\"\"
            return f"res-{sku}-{quantity}"
""")

_COMPLEX_ORDER_REPO_PY = textwrap.dedent("""\
    from .models import Order


    class OrderRepository:
        \"\"\"Persistence layer for orders.\"\"\"

        def save(self, order: Order) -> str:
            \"\"\"Persist an order and return its ID.\"\"\"
            # Real implementation writes to DB
            return f"ord-{id(order)}"

        def find_by_id(self, order_id: str) -> Order | None:
            return None
""")

_COMPLEX_ORDER_MODELS_PY = textwrap.dedent("""\
    from dataclasses import dataclass, field
    from enum import Enum
    from typing import List


    class OrderStatus(Enum):
        PENDING = "pending"
        CONFIRMED = "confirmed"
        SHIPPED = "shipped"
        CANCELLED = "cancelled"


    @dataclass
    class OrderItem:
        sku: str
        quantity: int
        unit_price: float


    @dataclass
    class Order:
        items: List[OrderItem] = field(default_factory=list)
        status: OrderStatus = OrderStatus.PENDING
        total: float = 0.0
""")

_COMPLEX_EVENT_BUS_PY = textwrap.dedent("""\
    from typing import Callable


    class EventBus:
        \"\"\"Simple in-process event emitter.\"\"\"

        def __init__(self):
            self._handlers: dict[str, list[Callable]] = {}

        def subscribe(self, event_type: str, handler: Callable) -> None:
            self._handlers.setdefault(event_type, []).append(handler)

        def publish(self, event_type: str, payload: dict) -> None:
            for handler in self._handlers.get(event_type, []):
                handler(payload)
""")

_COMPLEX_PRICING_ENGINE_PY = textwrap.dedent("""\
    from dataclasses import dataclass


    @dataclass
    class PricingResult:
        subtotal: float
        tax: float
        discount: float
        total: float


    class PricingEngine:
        \"\"\"Calculates order totals with tax and discount rules.\"\"\"

        def __init__(self, tax_rate: float = 0.08, discount_pct: float = 0.0):
            self.tax_rate = tax_rate
            self.discount_pct = discount_pct

        def calculate(self, items: list, rules: dict | None = None) -> PricingResult:
            subtotal = sum(i.unit_price * i.quantity for i in items)
            discount = subtotal * self.discount_pct
            taxable = subtotal - discount
            tax = taxable * self.tax_rate
            return PricingResult(
                subtotal=subtotal,
                tax=tax,
                discount=discount,
                total=taxable + tax,
            )
""")

_COMPLEX_ORDER_ROUTES_PY = textwrap.dedent("""\
    from dataclasses import asdict


    # Minimal route stub for eval purposes
    _routes: list[tuple[str, str, object]] = []


    def route(method: str, path: str):
        def decorator(fn):
            _routes.append((method, path, fn))
            return fn
        return decorator


    @route("GET", "/orders")
    def list_orders():
        return []


    @route("GET", "/orders/<order_id>")
    def get_order(order_id: str):
        return {"order_id": order_id}
""")

_COMPLEX_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## inventory/
    - `inventory/service.py` - Product inventory management

    ## orders/
    - `orders/models.py` - Order data models and status enum
    - `orders/repository.py` - Order persistence layer

    ## events/
    - `events/bus.py` - In-process event emitter

    ## pricing/
    - `pricing/engine.py` - Tax and discount calculation

    ## api/
    - `api/order_routes.py` - Order-related HTTP endpoints
    - `api/routes.py` - General HTTP routes

    ## utils/
    - `utils/config.py` - Configuration loader
""")


# ---------------------------------------------------------------------------
# Setup functions
# ---------------------------------------------------------------------------

def _setup_simple(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for single-file implementation scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-05.md"
    section_path.write_text(_SIMPLE_SECTION_SPEC, encoding="utf-8")

    # Integration proposal (aligned)
    proposal_path = proposals / "section-05-integration-proposal.md"
    proposal_path.write_text(_SIMPLE_INTEGRATION_PROPOSAL, encoding="utf-8")

    # Microstrategy
    micro_path = proposals / "section-05-microstrategy.md"
    micro_path.write_text(_SIMPLE_MICROSTRATEGY, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_SIMPLE_CODEMAP, encoding="utf-8")

    # Mode signal (brownfield)
    mode_signal = signals / "section-05-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "brownfield", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Codespace with existing config file
    utils_dir = codespace / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "__init__.py").write_text("", encoding="utf-8")
    (utils_dir / "config.py").write_text(_SIMPLE_CONFIG_PY, encoding="utf-8")
    (utils_dir / "logging.py").write_text(
        "import logging\n\ndef setup_logging():\n    pass\n",
        encoding="utf-8",
    )

    api_dir = codespace / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "routes.py").write_text(
        "def index():\n    return 'ok'\n", encoding="utf-8",
    )

    # Paths for agent outputs
    modified_report = artifacts / "impl-05-modified.txt"
    task_submission_path = signals / "impl-05-task.json"

    # Write the prompt (mirrors strategic-implementation.md template shape)
    prompt_path = artifacts / "impl-05-prompt.md"
    prompt_path.write_text(
        "# Task: Strategic Implementation for Section 05\n\n"
        "## Summary\n"
        "Add configuration validation to the YAML config loader.\n\n"
        "## Files to Read\n"
        f"1. Integration proposal (ALIGNED): `{proposal_path}`\n"
        f"2. Section specification: `{section_path}`\n"
        f"3. Microstrategy: `{micro_path}`\n"
        f"4. Codemap: `{codemap_path}`\n"
        "5. Related source files:\n"
        f"   - `{codespace / 'utils' / 'config.py'}`\n\n"
        "## Instructions\n\n"
        "You are implementing the changes described in the integration\n"
        "proposal. The proposal has been alignment-checked and approved.\n\n"
        "### How to Work\n"
        "Read the integration proposal. Read the target file. Implement\n"
        "the changes described in the proposal. Follow the microstrategy\n"
        "steps.\n\n"
        "### Implementation Guidelines\n"
        "1. Follow the integration proposal's strategy\n"
        "2. Do not invent files or architecture not in the proposal\n"
        "3. Ensure imports and references are consistent\n"
        "4. Update docstrings and comments to reflect changes\n\n"
        "### Report Modified Files\n"
        f"After implementation, write modified file paths to:\n"
        f"`{modified_report}`\n\n"
        "One file path per line (relative to codespace root "
        f"`{codespace}`).\n\n"
        "### Task Submission\n"
        "If you need follow-up work, write a task request to:\n"
        f"`{task_submission_path}`\n",
        encoding="utf-8",
    )

    return prompt_path


def _setup_complex(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for multi-file cross-module implementation scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-06.md"
    section_path.write_text(_COMPLEX_SECTION_SPEC, encoding="utf-8")

    # Integration proposal (aligned)
    proposal_path = proposals / "section-06-integration-proposal.md"
    proposal_path.write_text(_COMPLEX_INTEGRATION_PROPOSAL, encoding="utf-8")

    # Microstrategy
    micro_path = proposals / "section-06-microstrategy.md"
    micro_path.write_text(_COMPLEX_MICROSTRATEGY, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_COMPLEX_CODEMAP, encoding="utf-8")

    # Mode signal (brownfield)
    mode_signal = signals / "section-06-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "brownfield", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Cross-section decision
    decisions_dir = artifacts / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / "section-06.json").write_text(json.dumps([{
        "id": "d-006-01",
        "scope": "section",
        "section": "06",
        "concern_scope": "order-events",
        "proposal_summary": "Order lifecycle events consumed by fulfilment section",
        "status": "decided",
    }]) + "\n", encoding="utf-8")

    # Codespace with existing cross-module code
    inventory_dir = codespace / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    (inventory_dir / "__init__.py").write_text("", encoding="utf-8")
    (inventory_dir / "service.py").write_text(
        _COMPLEX_INVENTORY_SERVICE_PY, encoding="utf-8",
    )

    orders_dir = codespace / "orders"
    orders_dir.mkdir(parents=True, exist_ok=True)
    (orders_dir / "__init__.py").write_text("", encoding="utf-8")
    (orders_dir / "repository.py").write_text(
        _COMPLEX_ORDER_REPO_PY, encoding="utf-8",
    )
    (orders_dir / "models.py").write_text(
        _COMPLEX_ORDER_MODELS_PY, encoding="utf-8",
    )

    events_dir = codespace / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "__init__.py").write_text("", encoding="utf-8")
    (events_dir / "bus.py").write_text(
        _COMPLEX_EVENT_BUS_PY, encoding="utf-8",
    )

    pricing_dir = codespace / "pricing"
    pricing_dir.mkdir(parents=True, exist_ok=True)
    (pricing_dir / "__init__.py").write_text("", encoding="utf-8")
    (pricing_dir / "engine.py").write_text(
        _COMPLEX_PRICING_ENGINE_PY, encoding="utf-8",
    )

    api_dir = codespace / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "order_routes.py").write_text(
        _COMPLEX_ORDER_ROUTES_PY, encoding="utf-8",
    )

    # Paths for agent outputs
    modified_report = artifacts / "impl-06-modified.txt"
    task_submission_path = signals / "impl-06-task.json"

    # Write the prompt (mirrors strategic-implementation.md template shape)
    prompt_path = artifacts / "impl-06-prompt.md"
    prompt_path.write_text(
        "# Task: Strategic Implementation for Section 06\n\n"
        "## Summary\n"
        "Implement a unified order processing pipeline that validates\n"
        "inventory, calculates pricing, persists orders, and emits\n"
        "lifecycle events.\n\n"
        "## Files to Read\n"
        f"1. Integration proposal (ALIGNED): `{proposal_path}`\n"
        f"2. Section specification: `{section_path}`\n"
        f"3. Microstrategy: `{micro_path}`\n"
        f"4. Codemap: `{codemap_path}`\n"
        "5. Related source files:\n"
        f"   - `{codespace / 'inventory' / 'service.py'}`\n"
        f"   - `{codespace / 'orders' / 'repository.py'}`\n"
        f"   - `{codespace / 'orders' / 'models.py'}`\n"
        f"   - `{codespace / 'events' / 'bus.py'}`\n"
        f"   - `{codespace / 'pricing' / 'engine.py'}`\n"
        f"   - `{codespace / 'api' / 'order_routes.py'}`\n\n"
        "## Instructions\n\n"
        "You are implementing the changes described in the integration\n"
        "proposal. The proposal has been alignment-checked and approved.\n\n"
        "### How to Work\n"
        "Read the integration proposal and understand the SHAPE of the\n"
        "changes. Then tackle them holistically — multiple files at once,\n"
        "coordinated changes. Use the codemap to understand how your\n"
        "changes fit into the broader project structure.\n\n"
        "### Implementation Guidelines\n"
        "1. Follow the integration proposal's strategy\n"
        "2. Make coordinated changes across files\n"
        "3. If you discover the proposal missed something structural,\n"
        "   emit a blocker signal — do NOT invent missing structure\n"
        "4. Local mechanical necessities (imports, glue code, docstrings)\n"
        "   are your responsibility\n"
        "5. Do not add files or architecture not described in the proposal\n"
        "6. Ensure imports and references are consistent across modified files\n\n"
        "### Report Modified Files\n"
        f"After implementation, write modified file paths to:\n"
        f"`{modified_report}`\n\n"
        "One file path per line (relative to codespace root "
        f"`{codespace}`).\n\n"
        "### Task Submission\n"
        "If you need follow-up work, write a task request to:\n"
        f"`{task_submission_path}`\n",
        encoding="utf-8",
    )

    return prompt_path


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _check_simple_output_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify agent produced non-empty output."""
    if agent_output and len(agent_output.strip()) > 20:
        return True, f"Output is non-empty ({len(agent_output)} chars)"
    return False, f"Output is empty or trivially short ({len(agent_output)} chars)"


def _check_simple_mentions_config(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output mentions the target file utils/config.py."""
    lower = agent_output.lower()
    if "config.py" in lower or "utils/config" in lower:
        return True, "Output mentions utils/config.py"
    return False, "Output does not mention config.py or utils/config"


def _check_simple_no_invented_files(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent did not create files outside the proposal scope.

    The proposal only touches utils/config.py. The agent should not
    create new modules, new directories, or new files beyond what the
    proposal specifies.
    """
    lower = agent_output.lower()
    invention_phrases = [
        "new module",
        "new package",
        "new directory",
        "proposed directory structure",
        "file layout",
        "create a new file called",
    ]
    found = [p for p in invention_phrases if p in lower]
    if found:
        return False, f"Output invents files outside scope: {found}"

    # Also check codespace — only utils/ and api/ should exist
    top_level = {d.name for d in codespace.iterdir() if d.is_dir()}
    expected = {"utils", "api"}
    unexpected = top_level - expected
    if unexpected:
        return False, f"Unexpected directories created in codespace: {unexpected}"

    return True, "No files invented outside proposal scope"


def _check_complex_output_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify agent produced non-empty output."""
    if agent_output and len(agent_output.strip()) > 20:
        return True, f"Output is non-empty ({len(agent_output)} chars)"
    return False, f"Output is empty or trivially short ({len(agent_output)} chars)"


def _check_complex_mentions_multiple_files(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output mentions multiple target files from the proposal.

    The proposal touches: orders/pipeline.py, orders/models.py,
    api/order_routes.py, and references inventory/service.py,
    events/bus.py, pricing/engine.py.
    """
    lower = agent_output.lower()
    target_markers = [
        ("pipeline", "orders/pipeline"),
        ("models", "orders/models"),
        ("order_routes", "api/order_routes"),
    ]
    mentioned = [
        label for label, marker in target_markers
        if marker in lower or label in lower
    ]
    if len(mentioned) >= 2:
        return True, f"Output mentions {len(mentioned)} target areas: {mentioned}"
    return False, (
        f"Expected mentions of multiple target files, found {len(mentioned)}: "
        f"{mentioned}"
    )


def _check_complex_addresses_dependencies(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output addresses cross-file dependencies.

    The proposal requires coordinating across inventory, pricing,
    events, and orders. The output should mention at least two of
    these dependency surfaces.
    """
    lower = agent_output.lower()
    dependency_markers = [
        ("inventory", ["inventoryservice", "inventory_service",
                       "check_availability", "inventory/service"]),
        ("pricing", ["pricingengine", "pricing_engine",
                     "pricing/engine", "calculate"]),
        ("events", ["eventbus", "event_bus", "events/bus", "publish"]),
        ("repository", ["orderrepository", "order_repository",
                        "orders/repository", ".save"]),
    ]
    addressed = []
    for label, markers in dependency_markers:
        if any(m in lower for m in markers):
            addressed.append(label)
    if len(addressed) >= 2:
        return True, (
            f"Output addresses {len(addressed)} dependency surfaces: "
            f"{addressed}"
        )
    return False, (
        f"Expected at least 2 dependency surfaces addressed, "
        f"found {len(addressed)}: {addressed}"
    )


def _check_complex_no_invented_architecture(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent does not invent architecture beyond the proposal.

    The proposal creates orders/pipeline.py and modifies orders/models.py
    and api/order_routes.py. The agent should not invent new abstraction
    layers, new modules, or new architectural patterns not in the proposal.
    """
    lower = agent_output.lower()
    invention_phrases = [
        "abstract base class",
        "new abstraction layer",
        "proposed directory structure",
        "create a service layer",
        "add a middleware",
        "create a new module for",
        "introduce a facade",
    ]
    found = [p for p in invention_phrases if p in lower]
    if found:
        return False, f"Output invents architecture beyond proposal: {found}"
    return True, "No architecture invention detected"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="implementation_strategist_simple",
        agent_file="implementation-strategist.md",
        model_policy_key="implementation",
        setup=_setup_simple,
        checks=[
            Check(
                description="Agent output is non-empty",
                verify=_check_simple_output_nonempty,
            ),
            Check(
                description="Output mentions target file utils/config.py",
                verify=_check_simple_mentions_config,
            ),
            Check(
                description="No files invented outside proposal scope",
                verify=_check_simple_no_invented_files,
            ),
        ],
    ),
    Scenario(
        name="implementation_strategist_complex",
        agent_file="implementation-strategist.md",
        model_policy_key="implementation",
        setup=_setup_complex,
        checks=[
            Check(
                description="Agent output is non-empty",
                verify=_check_complex_output_nonempty,
            ),
            Check(
                description="Output mentions multiple target files",
                verify=_check_complex_mentions_multiple_files,
            ),
            Check(
                description="Output addresses cross-file dependencies",
                verify=_check_complex_addresses_dependencies,
            ),
            Check(
                description="No architecture invention beyond proposal",
                verify=_check_complex_no_invented_architecture,
            ),
        ],
    ),
]
