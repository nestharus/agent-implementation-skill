"""Microstrategy decider scenario evals.

Tests that the microstrategy-decider agent correctly classifies
proposals as needing or not needing a microstrategy breakdown.

Scenarios:
  microstrategy_simple: 1-file change -> needs_microstrategy=false
  microstrategy_complex: 6-file cross-module change -> needs_microstrategy=true
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SIMPLE_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 03

    ## Problem
    The configuration loader needs to support environment-specific
    overrides from `.env` files in addition to the existing YAML config.

    ## Proposed Changes

    ### File: `utils/config.py`
    Add a `load_env_overrides()` method that reads `.env` files and
    merges them into the existing config dict. Environment variables
    take precedence over YAML values.

    ## Impact Assessment
    - Single file modification
    - No interface changes (existing `load_config()` return type unchanged)
    - No cross-section dependencies
    - No database migrations
    - No ordering constraints
""")

_COMPLEX_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 04

    ## Problem
    The payment processing system needs to support multi-currency
    transactions with real-time exchange rate lookup, split payments
    across multiple funding sources, and regulatory compliance logging.

    ## Proposed Changes

    ### File: `payments/processor.py`
    Refactor `PaymentProcessor.process()` to accept a `CurrencyContext`
    parameter. Add multi-currency conversion using the exchange rate
    service. Split payment amounts across funding sources proportionally.

    ### File: `payments/exchange.py` (NEW)
    Create exchange rate service that caches rates from external API.
    Implements circuit breaker pattern for API failures with fallback
    to last-known rates.

    ### File: `payments/models.py`
    Add `SplitPayment` model with `funding_sources: list[FundingSource]`
    and `currency_context: CurrencyContext`. Update `Transaction` model
    to reference optional `SplitPayment`.

    ### File: `compliance/audit_log.py`
    Add payment-specific audit entries with currency conversion details,
    split ratios, and regulatory jurisdiction tags. Must be atomic with
    the payment transaction.

    ### File: `api/payment_routes.py`
    Update payment endpoints to accept multi-currency parameters.
    Add new `/api/payments/split` endpoint for split payments.
    Validate currency codes against supported currencies list.

    ### File: `migrations/004_split_payments.py` (NEW)
    Database migration adding split_payments table, currency_contexts
    table, and foreign keys from transactions. Must run BEFORE code
    deployment.

    ## Impact Assessment
    - 6 files across 3 modules (payments, compliance, api) + migration
    - New cross-section interface: compliance audit log consumed by section 07
    - Database migration with ordering constraint (must run before deploy)
    - External API dependency (exchange rate service) with failure modes
    - Split payment atomicity requirement across multiple DB operations
""")

_SIMPLE_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## utils/
    - `utils/config.py` - Configuration loader (YAML-based)
    - `utils/logging.py` - Structured logging

    ## api/
    - `api/routes.py` - HTTP routes
""")

_COMPLEX_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## payments/
    - `payments/processor.py` - Payment processing core
    - `payments/models.py` - Payment data models

    ## compliance/
    - `compliance/audit_log.py` - Regulatory audit logging

    ## api/
    - `api/payment_routes.py` - Payment API endpoints
    - `api/routes.py` - General HTTP routes

    ## migrations/
    - `migrations/001_initial.py` - Initial schema
    - `migrations/002_users.py` - User tables
    - `migrations/003_payments.py` - Payment tables
""")


# ---------------------------------------------------------------------------
# Setup functions
# ---------------------------------------------------------------------------

def _setup_simple(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for simple (no microstrategy) scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-03.md"
    section_path.write_text(textwrap.dedent("""\
        # Section 03: Environment Configuration Overrides

        ## Problem
        Support .env file overrides for the YAML configuration system.

        ## Requirements
        - REQ-01: Read .env files and merge into config dict
        - REQ-02: Environment variables take precedence over YAML
    """), encoding="utf-8")

    # Proposal
    proposal_path = proposals / "section-03-integration-proposal.md"
    proposal_path.write_text(_SIMPLE_PROPOSAL, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_SIMPLE_CODEMAP, encoding="utf-8")

    # Mode signal (brownfield)
    mode_signal = signals / "section-03-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "brownfield", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Codespace
    utils_dir = codespace / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "config.py").write_text(textwrap.dedent("""\
        import yaml
        from pathlib import Path

        def load_config(path: str = "config.yaml") -> dict:
            with open(path) as f:
                return yaml.safe_load(f)
    """), encoding="utf-8")

    # Write the prompt (mirrors _check_needs_microstrategy prompt)
    prompt_path = artifacts / "microstrategy-decider-03-prompt.md"
    signal_path = signals / "proposal-03-microstrategy.json"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Microstrategy Decision for Section 03

        ## Files to Read
        1. Integration proposal: `{proposal_path}`

        ## Complexity Signals (mechanically gathered)
        - Related file count: 1
        - Cross-section notes: 0
        - Cross-section decisions: 0
        - TODO extraction exists: False
        - Previous proposal attempts: 0
        - Section mode: brownfield

        ## Instructions
        Read the integration proposal and the complexity signals above. Apply your
        decision method to determine whether this section needs a microstrategy.

        Write a JSON signal to: `{signal_path}`
        ```json
        {{"needs_microstrategy": true|false, "reason": "..."}}
        ```
    """), encoding="utf-8")

    return prompt_path


def _setup_complex(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for complex (needs microstrategy) scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-04.md"
    section_path.write_text(textwrap.dedent("""\
        # Section 04: Multi-Currency Payment Processing

        ## Problem
        Support multi-currency transactions with exchange rates,
        split payments, and compliance audit logging.

        ## Requirements
        - REQ-01: Multi-currency conversion with cached exchange rates
        - REQ-02: Split payments across multiple funding sources
        - REQ-03: Atomic compliance audit logging with payment transactions
        - REQ-04: Database migration for split payment tables
        - REQ-05: API endpoint updates for multi-currency parameters
    """), encoding="utf-8")

    # Proposal
    proposal_path = proposals / "section-04-integration-proposal.md"
    proposal_path.write_text(_COMPLEX_PROPOSAL, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_COMPLEX_CODEMAP, encoding="utf-8")

    # Mode signal (brownfield)
    mode_signal = signals / "section-04-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "brownfield", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Codespace with existing payment code
    payments_dir = codespace / "payments"
    payments_dir.mkdir(parents=True, exist_ok=True)
    (payments_dir / "__init__.py").write_text("", encoding="utf-8")
    (payments_dir / "processor.py").write_text(textwrap.dedent("""\
        from .models import Transaction

        class PaymentProcessor:
            def process(self, amount: float, currency: str = "USD") -> Transaction:
                txn = Transaction(amount=amount, currency=currency)
                txn.status = "completed"
                return txn
    """), encoding="utf-8")
    (payments_dir / "models.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass, field

        @dataclass
        class Transaction:
            amount: float
            currency: str = "USD"
            status: str = "pending"
    """), encoding="utf-8")

    compliance_dir = codespace / "compliance"
    compliance_dir.mkdir(parents=True, exist_ok=True)
    (compliance_dir / "audit_log.py").write_text(textwrap.dedent("""\
        import json
        from pathlib import Path

        class AuditLog:
            def __init__(self, log_dir: str = "audit"):
                self.log_dir = Path(log_dir)

            def log_event(self, event_type: str, data: dict) -> None:
                entry = {"type": event_type, "data": data}
                print(json.dumps(entry))
    """), encoding="utf-8")

    # Cross-section decision referencing section 07
    decisions_dir = artifacts / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / "section-04.json").write_text(json.dumps([{
        "id": "d-004-01",
        "scope": "section",
        "section": "04",
        "concern_scope": "audit-interface",
        "proposal_summary": "Compliance audit log interface shared with section 07",
        "status": "decided",
    }]) + "\n", encoding="utf-8")

    # Write the prompt
    prompt_path = artifacts / "microstrategy-decider-04-prompt.md"
    signal_path = signals / "proposal-04-microstrategy.json"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Microstrategy Decision for Section 04

        ## Files to Read
        1. Integration proposal: `{proposal_path}`

        ## Complexity Signals (mechanically gathered)
        - Related file count: 6
        - Cross-section notes: 1
        - Cross-section decisions: 1
        - TODO extraction exists: True
        - Previous proposal attempts: 0
        - Section mode: brownfield

        ## Instructions
        Read the integration proposal and the complexity signals above. Apply your
        decision method to determine whether this section needs a microstrategy.

        Write a JSON signal to: `{signal_path}`
        ```json
        {{"needs_microstrategy": true|false, "reason": "..."}}
        ```
    """), encoding="utf-8")

    return prompt_path


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _check_simple_no_microstrategy(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify needs_microstrategy=false for simple proposal."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "proposal-03-microstrategy.json")
    if not signal_path.exists():
        return False, f"Signal file not written: {signal_path}"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Signal file is not valid JSON: {exc}"
    needs = data.get("needs_microstrategy")
    if needs is False:
        return True, "needs_microstrategy=false"
    return False, f"Expected needs_microstrategy=false, got {needs}"


def _check_simple_has_reason(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify signal has a non-empty reason field."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "proposal-03-microstrategy.json")
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


def _check_complex_needs_microstrategy(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify needs_microstrategy=true for complex proposal."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "proposal-04-microstrategy.json")
    if not signal_path.exists():
        return False, f"Signal file not written: {signal_path}"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Signal file is not valid JSON: {exc}"
    needs = data.get("needs_microstrategy")
    if needs is True:
        return True, "needs_microstrategy=true"
    return False, f"Expected needs_microstrategy=true, got {needs}"


def _check_complex_has_reason(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify signal has a non-empty reason field."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "proposal-04-microstrategy.json")
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
        name="microstrategy_simple",
        agent_file="microstrategy-decider.md",
        model_policy_key="microstrategy_decider",
        setup=_setup_simple,
        checks=[
            Check(
                description="Signal JSON has needs_microstrategy=false",
                verify=_check_simple_no_microstrategy,
            ),
            Check(
                description="Signal has non-empty reason",
                verify=_check_simple_has_reason,
            ),
        ],
    ),
    Scenario(
        name="microstrategy_complex",
        agent_file="microstrategy-decider.md",
        model_policy_key="microstrategy_decider",
        setup=_setup_complex,
        checks=[
            Check(
                description="Signal JSON has needs_microstrategy=true",
                verify=_check_complex_needs_microstrategy,
            ),
            Check(
                description="Signal has non-empty reason",
                verify=_check_complex_has_reason,
            ),
        ],
    ),
]
