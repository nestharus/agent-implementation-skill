"""Microstrategy writer scenario evals.

Tests that the microstrategy-writer agent produces tactical per-file
breakdowns from aligned integration proposals.

Scenarios:
  microstrategy_writer_simple: 2-3 file change -> ordered steps with file refs
  microstrategy_writer_complex: 5+ file cross-module change -> cross-dep steps + verification
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SIMPLE_SECTION_SPEC = textwrap.dedent("""\
    # Section 05: CLI Argument Validation

    ## Problem
    The CLI entry point accepts raw user input without validation.
    Invalid arguments cause cryptic tracebacks instead of helpful error
    messages.  The config loader also lacks fallback defaults when optional
    YAML keys are missing.

    ## Requirements
    - REQ-01: Validate all CLI arguments before dispatching to subcommands
    - REQ-02: Print user-friendly error messages for invalid input
    - REQ-03: Config loader returns sensible defaults for missing optional keys
""")

_SIMPLE_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 05

    ## Problem
    CLI arguments are passed directly to subcommand handlers without
    validation, leading to confusing errors deep in the call stack.
    The config loader does not handle missing optional keys gracefully.

    ## Proposed Changes

    ### File: `cli/parser.py` (modified)
    Add a `validate_args()` function that checks required arguments and
    type-validates optional ones before dispatching.  Return a structured
    `ValidationResult` instead of raising raw exceptions.

    ### File: `cli/errors.py` (new)
    Create a small module with user-facing error formatters.  Each
    validation failure maps to a one-line error message with a suggested
    fix hint.

    ### File: `utils/config.py` (modified)
    Add a `_apply_defaults()` helper called inside `load_config()` that
    fills in missing optional keys from a `DEFAULTS` dict before the
    config dict is returned.

    ## Impact Assessment
    - 3 files, single module + utility
    - No cross-section dependencies
    - No database changes
    - No shared contracts
""")

_SIMPLE_ALIGNMENT_EXCERPT = textwrap.dedent("""\
    # Alignment Excerpt: Section 05

    ## Constraints
    - Do not change the public signature of `load_config()`
    - Error messages must be printed to stderr, not stdout
    - No third-party validation libraries
""")

_SIMPLE_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## cli/
    - `cli/main.py` - Entry point, dispatches subcommands
    - `cli/parser.py` - Argument parsing with argparse

    ## utils/
    - `utils/config.py` - YAML configuration loader
    - `utils/logging.py` - Structured logging
""")

_COMPLEX_SECTION_SPEC = textwrap.dedent("""\
    # Section 06: Event-Driven Notification Pipeline

    ## Problem
    The system needs an event-driven notification pipeline that listens
    for domain events, applies per-user delivery preferences, renders
    templates, and dispatches through multiple channels (in-app, email,
    webhook).  Delivery must be idempotent and observable.

    ## Requirements
    - REQ-01: Event bus listener that routes domain events to handlers
    - REQ-02: Per-user delivery preference resolution
    - REQ-03: Template rendering with variable substitution
    - REQ-04: Multi-channel dispatch (in-app, email, webhook)
    - REQ-05: Idempotency via deduplication key on each notification
    - REQ-06: Delivery status tracking and observability metrics
""")

_COMPLEX_PROPOSAL = textwrap.dedent("""\
    # Integration Proposal: Section 06

    ## Problem
    Domain events currently have no consumer. Users are not notified
    when relevant state changes occur. A notification pipeline must
    bridge the gap between event producers and delivery channels.

    ## Proposed Changes

    ### File: `events/listener.py` (modified)
    Register a `NotificationHandler` callback in the existing event bus
    dispatcher. The handler receives typed `DomainEvent` objects and
    forwards them to the notification router.

    ### File: `notifications/router.py` (new)
    Create a router that maps event types to notification templates and
    resolves per-user delivery preferences from the preference store.
    Emits `NotificationJob` objects for each resolved recipient.

    ### File: `notifications/renderer.py` (new)
    Template rendering module.  Loads Jinja2-style templates from
    `templates/notifications/` and renders them with event payload
    variables.  Validates that required variables are present.

    ### File: `notifications/dispatcher.py` (new)
    Accepts `NotificationJob` objects and dispatches through the
    appropriate channel adapter (in-app, email, webhook).  Each
    dispatch is idempotent — keyed on `(event_id, recipient_id)`.

    ### File: `notifications/models.py` (new)
    Data classes: `NotificationJob`, `DeliveryResult`, `DeliveryStatus`.
    Shared across router, renderer, and dispatcher.

    ### File: `users/preferences.py` (modified)
    Add a `get_notification_preferences(user_id)` method that returns
    channel preferences and quiet-hours configuration.  Must respect
    the existing `UserRepository` interface contract from section 02.

    ### File: `metrics/counters.py` (modified)
    Add counters for notification dispatches, failures, and latency
    histograms per channel.  Integrates with the existing Prometheus
    registry from section 09.

    ## Impact Assessment
    - 7 files across 4 modules (events, notifications, users, metrics)
    - Cross-section interface: UserRepository contract (section 02)
    - Cross-section interface: Prometheus registry (section 09)
    - Template directory dependency (templates/notifications/)
    - Idempotency requirement across restarts
""")

_COMPLEX_ALIGNMENT_EXCERPT = textwrap.dedent("""\
    # Alignment Excerpt: Section 06

    ## Constraints
    - Must not modify the `UserRepository` interface defined by section 02
    - Prometheus counter names must follow the existing naming convention
      from section 09 (prefix `app_`)
    - Event listener registration must not block the event bus dispatcher
    - No new external dependencies beyond the existing Jinja2 package

    ## Cross-Section Decisions
    - D-006-01: Notification preferences stored in users module, not
      a separate notification_preferences table
    - D-006-02: Metrics use existing Prometheus registry, no new registry
""")

_COMPLEX_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## events/
    - `events/bus.py` - Event bus with publish/subscribe
    - `events/listener.py` - Registered event handlers
    - `events/types.py` - DomainEvent base class and subtypes

    ## users/
    - `users/repository.py` - UserRepository interface (section 02 contract)
    - `users/preferences.py` - User preference storage

    ## metrics/
    - `metrics/registry.py` - Prometheus registry singleton
    - `metrics/counters.py` - Application metric counters

    ## templates/
    - `templates/notifications/` - (empty, to be populated)
""")


# ---------------------------------------------------------------------------
# Setup functions
# ---------------------------------------------------------------------------

def _setup_simple(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for a simple 2-3 file microstrategy."""
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

    # Integration proposal
    proposal_path = proposals / "section-05-integration-proposal.md"
    proposal_path.write_text(_SIMPLE_PROPOSAL, encoding="utf-8")

    # Alignment excerpt
    alignment_path = sections / "section-05-alignment-excerpt.md"
    alignment_path.write_text(_SIMPLE_ALIGNMENT_EXCERPT, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_SIMPLE_CODEMAP, encoding="utf-8")

    # Codespace with existing code
    cli_dir = codespace / "cli"
    cli_dir.mkdir(parents=True, exist_ok=True)
    (cli_dir / "__init__.py").write_text("", encoding="utf-8")
    (cli_dir / "main.py").write_text(textwrap.dedent("""\
        import sys
        from .parser import parse_args

        def main():
            args = parse_args(sys.argv[1:])
            # dispatch to subcommand
            handler = COMMANDS.get(args.command)
            if handler:
                handler(args)

        COMMANDS = {}
    """), encoding="utf-8")
    (cli_dir / "parser.py").write_text(textwrap.dedent("""\
        import argparse

        def parse_args(argv: list[str]) -> argparse.Namespace:
            parser = argparse.ArgumentParser(prog="myapp")
            parser.add_argument("command", choices=["run", "check", "init"])
            parser.add_argument("--config", default="config.yaml")
            parser.add_argument("--verbose", action="store_true")
            return parser.parse_args(argv)
    """), encoding="utf-8")

    utils_dir = codespace / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "__init__.py").write_text("", encoding="utf-8")
    (utils_dir / "config.py").write_text(textwrap.dedent("""\
        import yaml
        from pathlib import Path

        def load_config(path: str = "config.yaml") -> dict:
            with open(path) as f:
                return yaml.safe_load(f)
    """), encoding="utf-8")

    # Microstrategy output path (where the agent should write)
    microstrategy_path = proposals / "section-05-microstrategy.md"

    # Build the prompt — inline context for GLM-style dispatch
    prompt_path = artifacts / "microstrategy-05-prompt.md"
    prompt_path.write_text(
        "# Task: Microstrategy for Section 05\n\n"
        "## Context\n"
        f"Read the integration proposal: `{proposal_path}`\n"
        f"Read the alignment excerpt: `{alignment_path}`\n\n"
        "## Related Files\n"
        f"- `{codespace / 'cli/parser.py'}`\n"
        f"- `{codespace / 'cli/main.py'}`\n"
        f"- `{codespace / 'utils/config.py'}`\n\n"
        "## Integration Proposal (inline)\n\n"
        f"{_SIMPLE_PROPOSAL}\n\n"
        "## Alignment Excerpt (inline)\n\n"
        f"{_SIMPLE_ALIGNMENT_EXCERPT}\n\n"
        "## Instructions\n\n"
        "The integration proposal describes the HIGH-LEVEL strategy for this\n"
        "section. Your job is to produce a MICROSTRATEGY -- a tactical per-file\n"
        "breakdown that an implementation agent can follow directly.\n\n"
        "For each file that needs changes, write:\n"
        "1. **File path** and whether it's new or modified\n"
        "2. **What changes** -- specific functions, classes, or blocks to add/modify\n"
        "3. **Order** -- which file changes depend on which others\n"
        "4. **Risks** -- what could go wrong with this specific change\n\n"
        f"Write the microstrategy to: `{microstrategy_path}`\n\n"
        "Keep it tactical and concrete. The integration proposal already justified\n"
        "WHY -- you're capturing WHAT and WHERE at the file level.\n",
        encoding="utf-8",
    )

    return prompt_path


def _setup_complex(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for a complex multi-concern microstrategy."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    decisions_dir = artifacts / "decisions"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    decisions_dir.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-06.md"
    section_path.write_text(_COMPLEX_SECTION_SPEC, encoding="utf-8")

    # Integration proposal
    proposal_path = proposals / "section-06-integration-proposal.md"
    proposal_path.write_text(_COMPLEX_PROPOSAL, encoding="utf-8")

    # Alignment excerpt
    alignment_path = sections / "section-06-alignment-excerpt.md"
    alignment_path.write_text(_COMPLEX_ALIGNMENT_EXCERPT, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_COMPLEX_CODEMAP, encoding="utf-8")

    # Codespace with existing code across multiple modules
    events_dir = codespace / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "__init__.py").write_text("", encoding="utf-8")
    (events_dir / "bus.py").write_text(textwrap.dedent("""\
        from typing import Callable

        class EventBus:
            def __init__(self):
                self._handlers: dict[str, list[Callable]] = {}

            def subscribe(self, event_type: str, handler: Callable) -> None:
                self._handlers.setdefault(event_type, []).append(handler)

            def publish(self, event_type: str, payload: dict) -> None:
                for handler in self._handlers.get(event_type, []):
                    handler(payload)
    """), encoding="utf-8")
    (events_dir / "listener.py").write_text(textwrap.dedent("""\
        from .bus import EventBus

        def register_handlers(bus: EventBus) -> None:
            # existing handlers
            bus.subscribe("user.created", _on_user_created)

        def _on_user_created(payload: dict) -> None:
            print(f"User created: {payload.get('user_id')}")
    """), encoding="utf-8")
    (events_dir / "types.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass

        @dataclass
        class DomainEvent:
            event_type: str
            event_id: str
            payload: dict
    """), encoding="utf-8")

    users_dir = codespace / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    (users_dir / "__init__.py").write_text("", encoding="utf-8")
    (users_dir / "repository.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass

        @dataclass
        class User:
            id: int
            username: str
            email: str

        class UserRepository:
            def find_by_id(self, user_id: int) -> User | None:
                raise NotImplementedError

            def find_by_username(self, username: str) -> User | None:
                raise NotImplementedError
    """), encoding="utf-8")
    (users_dir / "preferences.py").write_text(textwrap.dedent("""\
        class UserPreferences:
            def get_theme(self, user_id: int) -> str:
                return "default"

            def get_locale(self, user_id: int) -> str:
                return "en"
    """), encoding="utf-8")

    metrics_dir = codespace / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "__init__.py").write_text("", encoding="utf-8")
    (metrics_dir / "registry.py").write_text(textwrap.dedent("""\
        # Prometheus registry singleton
        _REGISTRY = {}

        def get_registry() -> dict:
            return _REGISTRY
    """), encoding="utf-8")
    (metrics_dir / "counters.py").write_text(textwrap.dedent("""\
        from .registry import get_registry

        def increment(name: str, labels: dict | None = None) -> None:
            registry = get_registry()
            key = (name, tuple(sorted((labels or {}).items())))
            registry[key] = registry.get(key, 0) + 1
    """), encoding="utf-8")

    templates_dir = codespace / "templates" / "notifications"
    templates_dir.mkdir(parents=True, exist_ok=True)

    # Cross-section decisions
    import json
    (decisions_dir / "section-06.json").write_text(json.dumps([
        {
            "id": "d-006-01",
            "scope": "section",
            "section": "06",
            "concern_scope": "notification-preferences",
            "proposal_summary": (
                "Notification preferences stored in users module, "
                "not a separate table"
            ),
            "status": "decided",
        },
        {
            "id": "d-006-02",
            "scope": "section",
            "section": "06",
            "concern_scope": "metrics-registry",
            "proposal_summary": (
                "Metrics use existing Prometheus registry from section 09"
            ),
            "status": "decided",
        },
    ]) + "\n", encoding="utf-8")

    # Microstrategy output path
    microstrategy_path = proposals / "section-06-microstrategy.md"

    # Build the prompt
    prompt_path = artifacts / "microstrategy-06-prompt.md"
    prompt_path.write_text(
        "# Task: Microstrategy for Section 06\n\n"
        "## Context\n"
        f"Read the integration proposal: `{proposal_path}`\n"
        f"Read the alignment excerpt: `{alignment_path}`\n\n"
        "## Related Files\n"
        f"- `{codespace / 'events/listener.py'}`\n"
        f"- `{codespace / 'events/bus.py'}`\n"
        f"- `{codespace / 'events/types.py'}`\n"
        f"- `{codespace / 'users/preferences.py'}`\n"
        f"- `{codespace / 'users/repository.py'}`\n"
        f"- `{codespace / 'metrics/counters.py'}`\n"
        f"- `{codespace / 'metrics/registry.py'}`\n\n"
        "## Integration Proposal (inline)\n\n"
        f"{_COMPLEX_PROPOSAL}\n\n"
        "## Alignment Excerpt (inline)\n\n"
        f"{_COMPLEX_ALIGNMENT_EXCERPT}\n\n"
        "## Instructions\n\n"
        "The integration proposal describes the HIGH-LEVEL strategy for this\n"
        "section. Your job is to produce a MICROSTRATEGY -- a tactical per-file\n"
        "breakdown that an implementation agent can follow directly.\n\n"
        "For each file that needs changes, write:\n"
        "1. **File path** and whether it's new or modified\n"
        "2. **What changes** -- specific functions, classes, or blocks to add/modify\n"
        "3. **Order** -- which file changes depend on which others\n"
        "4. **Risks** -- what could go wrong with this specific change\n\n"
        f"Write the microstrategy to: `{microstrategy_path}`\n\n"
        "Keep it tactical and concrete. The integration proposal already justified\n"
        "WHY -- you're capturing WHAT and WHERE at the file level.\n",
        encoding="utf-8",
    )

    return prompt_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_microstrategy(planspace: Path, section: str, agent_output: str) -> str:
    """Return microstrategy text from file or agent stdout.

    The agent is instructed to write to the microstrategy path, but it
    may also emit the content to stdout.  Check the file first.
    """
    micro_path = (planspace / "artifacts" / "proposals"
                  / f"section-{section}-microstrategy.md")
    if micro_path.exists() and micro_path.stat().st_size > 0:
        return micro_path.read_text(encoding="utf-8")
    # Fallback: treat agent stdout as the microstrategy content
    if agent_output and len(agent_output.strip()) > 50:
        return agent_output
    return ""


def _count_ordered_steps(text: str) -> int:
    """Count numbered step headings or list items in the microstrategy.

    Matches patterns like:
      ## Step 1:  /  ### 1.  /  **Step 1:**  /  1. File:  /  1) ...
    """
    # Heading-style: ## Step 1, ### Step 1, ## 1., ### 1.
    heading_pattern = re.findall(
        r"^#{1,4}\s*(?:Step\s*)?\d+[\.\):]\s*",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    if len(heading_pattern) >= 2:
        return len(heading_pattern)
    # Bold-style: **Step 1:**, **1.**
    bold_pattern = re.findall(
        r"\*\*(?:Step\s*)?\d+[\.\):]",
        text,
        re.IGNORECASE,
    )
    if len(bold_pattern) >= 2:
        return len(bold_pattern)
    # List-style: top-level numbered items (1. ..., 2. ..., etc.)
    list_pattern = re.findall(r"^\d+\.\s+\S", text, re.MULTILINE)
    return len(list_pattern)


# ---------------------------------------------------------------------------
# Check functions — simple scenario
# ---------------------------------------------------------------------------

def _check_simple_has_ordered_steps(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the microstrategy contains ordered steps."""
    text = _read_microstrategy(planspace, "05", agent_output)
    if not text:
        return False, "Microstrategy is empty (not written to file or stdout)"
    count = _count_ordered_steps(text)
    if count >= 2:
        return True, f"Found {count} ordered steps"
    return False, f"Expected >=2 ordered steps, found {count}"


def _check_simple_references_files(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify steps reference specific files from the proposal."""
    text = _read_microstrategy(planspace, "05", agent_output)
    if not text:
        return False, "Microstrategy is empty"
    lower = text.lower()
    expected_files = ["parser.py", "config.py", "errors.py"]
    found = [f for f in expected_files if f in lower]
    if len(found) >= 2:
        return True, f"References files: {found}"
    return False, f"Expected >=2 file references from {expected_files}, found: {found}"


def _check_simple_step_count_reasonable(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify step count is between 2 and 4 for a simple proposal."""
    text = _read_microstrategy(planspace, "05", agent_output)
    if not text:
        return False, "Microstrategy is empty"
    count = _count_ordered_steps(text)
    if 2 <= count <= 4:
        return True, f"Step count {count} is reasonable for a simple proposal"
    return False, f"Expected 2-4 steps for a simple proposal, got {count}"


def _check_simple_steps_have_actions(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify steps contain concrete action language (add, modify, create, etc.)."""
    text = _read_microstrategy(planspace, "05", agent_output)
    if not text:
        return False, "Microstrategy is empty"
    action_words = ["add", "create", "modify", "update", "implement", "introduce",
                    "refactor", "extract", "define", "return", "validate", "import"]
    lower = text.lower()
    found = [w for w in action_words if w in lower]
    if len(found) >= 3:
        return True, f"Found action language: {found[:5]}"
    return False, f"Expected >=3 action words, found: {found}"


# ---------------------------------------------------------------------------
# Check functions — complex scenario
# ---------------------------------------------------------------------------

def _check_complex_has_ordered_steps(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the microstrategy contains ordered steps."""
    text = _read_microstrategy(planspace, "06", agent_output)
    if not text:
        return False, "Microstrategy is empty (not written to file or stdout)"
    count = _count_ordered_steps(text)
    if count >= 3:
        return True, f"Found {count} ordered steps"
    return False, f"Expected >=3 ordered steps for complex proposal, found {count}"


def _check_complex_addresses_cross_deps(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the microstrategy addresses cross-file/cross-section dependencies."""
    text = _read_microstrategy(planspace, "06", agent_output)
    if not text:
        return False, "Microstrategy is empty"
    lower = text.lower()
    # Must mention at least two of the cross-cutting concerns
    cross_dep_markers = [
        ("event bus", "events/listener", "eventbus", "event_bus", "listener"),
        ("userrepository", "user_repository", "users/repository",
         "repository interface", "section 02"),
        ("prometheus", "metrics", "counters", "registry", "section 09"),
        ("notification", "router", "dispatcher", "renderer"),
    ]
    concerns_found = 0
    found_labels = []
    for markers in cross_dep_markers:
        if any(m in lower for m in markers):
            concerns_found += 1
            found_labels.append(markers[0])
    if concerns_found >= 3:
        return True, f"Addresses {concerns_found} cross-cutting concerns: {found_labels}"
    return False, (
        f"Expected >=3 cross-cutting concerns addressed, "
        f"found {concerns_found}: {found_labels}"
    )


def _check_complex_includes_verification(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the microstrategy includes a verification or validation step."""
    text = _read_microstrategy(planspace, "06", agent_output)
    if not text:
        return False, "Microstrategy is empty"
    lower = text.lower()
    verification_markers = [
        "verif", "validat", "confirm", "check", "test",
        "assert", "ensure", "idempoten",
    ]
    found = [m for m in verification_markers if m in lower]
    if found:
        return True, f"Verification language found: {found[:4]}"
    return False, "No verification/validation step language found"


def _check_complex_no_invented_architecture(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the microstrategy does not invent architecture beyond the proposal."""
    text = _read_microstrategy(planspace, "06", agent_output)
    if not text:
        return False, "Microstrategy is empty"
    lower = text.lower()
    # These would indicate the writer is inventing new architectural components
    # not mentioned in the proposal
    invention_markers = [
        "microservice", "kafka", "rabbitmq", "celery", "redis queue",
        "message broker", "grpc", "graphql", "docker", "kubernetes",
    ]
    found = [m for m in invention_markers if m in lower]
    if found:
        return False, f"Invented architecture not in proposal: {found}"
    return True, "No invented architecture beyond proposal scope"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="microstrategy_writer_simple",
        agent_file="microstrategy-writer.md",
        model_policy_key="implementation",
        setup=_setup_simple,
        checks=[
            Check(
                description="Microstrategy contains ordered steps",
                verify=_check_simple_has_ordered_steps,
            ),
            Check(
                description="Steps reference specific files from proposal",
                verify=_check_simple_references_files,
            ),
            Check(
                description="Step count is reasonable (2-4)",
                verify=_check_simple_step_count_reasonable,
            ),
            Check(
                description="Steps contain concrete action language",
                verify=_check_simple_steps_have_actions,
            ),
        ],
    ),
    Scenario(
        name="microstrategy_writer_complex",
        agent_file="microstrategy-writer.md",
        model_policy_key="implementation",
        setup=_setup_complex,
        checks=[
            Check(
                description="Microstrategy contains ordered steps",
                verify=_check_complex_has_ordered_steps,
            ),
            Check(
                description="Addresses cross-file and cross-section dependencies",
                verify=_check_complex_addresses_cross_deps,
            ),
            Check(
                description="Includes verification or validation step",
                verify=_check_complex_includes_verification,
            ),
            Check(
                description="Does not invent architecture beyond proposal",
                verify=_check_complex_no_invented_architecture,
            ),
        ],
    ),
]
