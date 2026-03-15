"""Intent agent scenario evals.

Tests that the intent-pack-generator and problem-expander agents produce
correct outputs under varying conditions (philosophy present/absent,
new surface discovery).

The intent-pack-generator reads a section spec, global proposal, and
optional philosophy.md, then produces a problem.md with axis structure,
a problem-alignment.md rubric, and optionally a philosophy-excerpt.md.

The problem-expander reads an existing problem.md plus a surface
discovery signal and integrates confirmed surfaces into the problem
definition and rubric.

Scenarios:
  intent_pack_with_philosophy:       Philosophy present -> problem.md references philosophy
  intent_pack_without_philosophy:    No philosophy -> valid problem.md, no hallucinated philosophy
  problem_expander_deepens_on_surface: Surface discovery -> expanded problem.md with new detail
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SECTION_SPEC = textwrap.dedent("""\
    # Section 03: Database Session Provider

    ## Problem
    The application manages database sessions inconsistently across
    modules. Some modules open sessions inline, others rely on a global
    singleton, and several never close sessions properly, leading to
    connection pool exhaustion under load. A unified session provider is
    needed that manages lifecycle, scoping, and cleanup.

    ## Requirements
    - REQ-01: Request-scoped session lifecycle (open on entry, close/commit on exit)
    - REQ-02: Configurable isolation level per use case (read-only vs. read-write)
    - REQ-03: Automatic rollback on unhandled exceptions
    - REQ-04: Connection pool health monitoring and metrics
    - REQ-05: Support for nested transaction savepoints

    ## Constraints
    - Must integrate with existing FastAPI dependency injection
    - Must not break the 12 existing repository classes that use raw sessions
    - Connection pool max size: 20 (production), 5 (testing)

    ## Related Files

    ### db/session.py
    Current global session factory -- needs replacement.

    ### db/models.py
    SQLAlchemy model definitions.

    ### api/deps.py
    FastAPI dependency functions that currently create ad-hoc sessions.

    ### repositories/user_repo.py
    Example repository using raw session access (pattern repeated 12x).
""")

_GLOBAL_PROPOSAL = textwrap.dedent("""\
    # Global Proposal

    ## Section 03: Database Session Provider

    Introduce a SessionProvider that owns the session lifecycle.
    FastAPI endpoints receive sessions via dependency injection.
    Repositories accept a session parameter instead of creating their own.

    Key changes:
    - New db/provider.py with SessionProvider class
    - Refactor api/deps.py to use SessionProvider
    - Migrate repositories to accept session parameter
    - Add connection pool monitoring hooks

    Cross-section impact: Section 07 (background jobs) needs access to
    the same session provider for long-running tasks.
""")

_PHILOSOPHY = textwrap.dedent("""\
    # Operational Philosophy

    ## Principle 1: Fail Closed on Ambiguity
    When a component encounters an ambiguous state, it must fail rather
    than guess. Guessing propagates hidden assumptions through the system.
    Prefer explicit errors over silent defaults.

    ## Principle 2: Seam Ownership
    Every integration point between modules must have exactly one owner.
    The owner is responsible for the contract definition, error handling,
    and backward compatibility. Shared ownership is no ownership.

    ## Principle 3: Observable by Default
    Every non-trivial operation must emit structured telemetry (logs,
    metrics, traces). If you cannot observe it in production, you cannot
    debug it in production. Monitoring is not optional.

    ## Principle 4: Incremental Migration
    Large-scale refactors must be decomposable into steps that each leave
    the system in a working state. No big-bang migrations. Each step must
    be independently testable and reversible.
""")

_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## db/
    - `db/session.py` - Global session factory (singleton, 80 lines)
    - `db/models.py` - SQLAlchemy models (Order, User, Product)
    - `db/migrations/` - Alembic migration scripts

    ## api/
    - `api/deps.py` - FastAPI dependency injection functions
    - `api/routes.py` - API route definitions

    ## repositories/
    - `repositories/user_repo.py` - User repository (raw session)
    - `repositories/order_repo.py` - Order repository (raw session)
    - `repositories/product_repo.py` - Product repository (raw session)
""")


# ---------------------------------------------------------------------------
# Fixture: existing problem.md for expander scenario
# ---------------------------------------------------------------------------

_EXISTING_PROBLEM = textwrap.dedent("""\
    # Section Problem Definition: Database Session Provider

    ## A1: Session Lifecycle Consistency
    The application has three distinct session management patterns: inline
    creation, global singleton access, and dependency-injected sessions.
    This inconsistency means session close/commit behavior varies by call
    path, leading to leaked connections.
    Evidence: db/session.py provides a global singleton; api/deps.py
    creates ad-hoc sessions; repositories/user_repo.py opens inline sessions.
    Success criterion: All session creation flows through a single provider
    with guaranteed cleanup on scope exit.

    ## A2: Exception Safety
    Unhandled exceptions during database operations leave sessions in an
    undefined state. Some code paths commit partial work, others leave
    uncommitted transactions blocking the connection pool.
    Evidence: repositories/order_repo.py has a bare try/except that commits
    on exception; db/session.py never calls rollback.
    Success criterion: Every session scope automatically rolls back on
    unhandled exception before releasing the connection.

    ## A3: Connection Pool Pressure
    The pool is capped at 20 connections. With 12 repositories potentially
    holding sessions simultaneously and no scoping, the pool can exhaust
    under moderate load.
    Evidence: Production logs show "pool exhausted" errors during peak
    traffic (3x/week). Current pool has no health monitoring.
    Success criterion: Pool utilization is observable via metrics and
    sessions are scoped to request lifetime to prevent accumulation.

    ## A4: Migration Path for Existing Repositories
    12 repository classes use raw session access. Migrating them all at
    once risks a large, hard-to-review changeset.
    Evidence: repositories/ directory contains 12 files following the same
    raw-session pattern.
    Success criterion: Migration can proceed repository-by-repository with
    both old and new patterns working simultaneously.
""")

_EXISTING_RUBRIC = textwrap.dedent("""\
    # Problem Alignment Rubric: Database Session Provider

    | Axis | Title | Description |
    |------|-------|-------------|
    | A1   | Session Lifecycle Consistency | All sessions flow through a single provider with guaranteed cleanup |
    | A2   | Exception Safety | Automatic rollback on unhandled exception before connection release |
    | A3   | Connection Pool Pressure | Observable pool utilization, request-scoped session lifetime |
    | A4   | Migration Path | Repository-by-repository migration with coexistence of old and new patterns |
""")


# ---------------------------------------------------------------------------
# Setup: intent_pack_with_philosophy
# ---------------------------------------------------------------------------

def _setup_pack_with_philosophy(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for intent pack generation WITH philosophy present."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    sections.mkdir(parents=True, exist_ok=True)

    # Section spec
    (sections / "section-03.md").write_text(_SECTION_SPEC, encoding="utf-8")

    # Global proposal
    (artifacts / "global-proposal.md").write_text(_GLOBAL_PROPOSAL, encoding="utf-8")

    # Philosophy (present)
    (artifacts / "philosophy.md").write_text(_PHILOSOPHY, encoding="utf-8")

    # Codemap
    (artifacts / "codemap.md").write_text(_CODEMAP, encoding="utf-8")

    # Minimal codespace
    for d in ["db", "api", "repositories"]:
        (codespace / d).mkdir(parents=True, exist_ok=True)
        (codespace / d / "__init__.py").write_text("", encoding="utf-8")

    (codespace / "db" / "session.py").write_text(textwrap.dedent("""\
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("postgresql://localhost/app")
        SessionFactory = sessionmaker(bind=engine)

        # Global singleton -- used by some modules directly
        _session = None

        def get_session():
            global _session
            if _session is None:
                _session = SessionFactory()
            return _session
    """), encoding="utf-8")

    (codespace / "api" / "deps.py").write_text(textwrap.dedent("""\
        from db.session import SessionFactory

        def get_db():
            session = SessionFactory()
            try:
                yield session
            finally:
                session.close()
    """), encoding="utf-8")

    (codespace / "repositories" / "user_repo.py").write_text(textwrap.dedent("""\
        from db.session import get_session

        class UserRepository:
            def get_user(self, user_id: int):
                session = get_session()
                return session.query(User).get(user_id)
                # Note: session never closed
    """), encoding="utf-8")

    # Build prompt
    prompt_path = artifacts / "intent-pack-03-prompt.md"
    prompt_path.write_text(
        "# Task: Generate Intent Pack for Section 03\n\n"
        "## Section Spec\n\n"
        f"{_SECTION_SPEC}\n\n"
        "## Global Proposal\n\n"
        f"{_GLOBAL_PROPOSAL}\n\n"
        "## Philosophy\n\n"
        f"{_PHILOSOPHY}\n\n"
        "## Codemap\n\n"
        f"{_CODEMAP}\n\n"
        "## Instructions\n\n"
        "You are the intent-pack-generator. Produce:\n"
        "1. A problem.md with axis-structured problem definition (A1, A2, ...)\n"
        "2. A problem-alignment.md rubric table\n"
        "3. A philosophy-excerpt.md with the subset of philosophy principles\n"
        "   relevant to this section\n"
        "4. A surface-registry.json initialized empty\n\n"
        "Output all content inline. Use markdown headers to delimit each file.\n"
        "Start each file section with: ## FILE: <filename>\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: intent_pack_without_philosophy
# ---------------------------------------------------------------------------

def _setup_pack_without_philosophy(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for intent pack generation WITHOUT philosophy."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    sections.mkdir(parents=True, exist_ok=True)

    # Section spec
    (sections / "section-03.md").write_text(_SECTION_SPEC, encoding="utf-8")

    # Global proposal
    (artifacts / "global-proposal.md").write_text(_GLOBAL_PROPOSAL, encoding="utf-8")

    # NO philosophy.md

    # Codemap
    (artifacts / "codemap.md").write_text(_CODEMAP, encoding="utf-8")

    # Minimal codespace
    for d in ["db", "api", "repositories"]:
        (codespace / d).mkdir(parents=True, exist_ok=True)
        (codespace / d / "__init__.py").write_text("", encoding="utf-8")

    (codespace / "db" / "session.py").write_text(textwrap.dedent("""\
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("postgresql://localhost/app")
        SessionFactory = sessionmaker(bind=engine)

        _session = None

        def get_session():
            global _session
            if _session is None:
                _session = SessionFactory()
            return _session
    """), encoding="utf-8")

    (codespace / "api" / "deps.py").write_text(textwrap.dedent("""\
        from db.session import SessionFactory

        def get_db():
            session = SessionFactory()
            try:
                yield session
            finally:
                session.close()
    """), encoding="utf-8")

    (codespace / "repositories" / "user_repo.py").write_text(textwrap.dedent("""\
        from db.session import get_session

        class UserRepository:
            def get_user(self, user_id: int):
                session = get_session()
                return session.query(User).get(user_id)
    """), encoding="utf-8")

    # Build prompt -- explicitly note no philosophy
    prompt_path = artifacts / "intent-pack-03-prompt.md"
    prompt_path.write_text(
        "# Task: Generate Intent Pack for Section 03\n\n"
        "## Section Spec\n\n"
        f"{_SECTION_SPEC}\n\n"
        "## Global Proposal\n\n"
        f"{_GLOBAL_PROPOSAL}\n\n"
        "## Philosophy\n\n"
        "No operational philosophy has been defined for this project.\n\n"
        "## Codemap\n\n"
        f"{_CODEMAP}\n\n"
        "## Instructions\n\n"
        "You are the intent-pack-generator. Produce:\n"
        "1. A problem.md with axis-structured problem definition (A1, A2, ...)\n"
        "2. A problem-alignment.md rubric table\n"
        "3. A surface-registry.json initialized empty\n\n"
        "There is no philosophy.md for this project. Do NOT fabricate\n"
        "philosophy content or produce a philosophy-excerpt.md.\n\n"
        "Output all content inline. Use markdown headers to delimit each file.\n"
        "Start each file section with: ## FILE: <filename>\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: problem_expander_deepens_on_surface
# ---------------------------------------------------------------------------

def _setup_expander_with_surface(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for problem expander with a new surface discovery."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Section spec
    (sections / "section-03.md").write_text(_SECTION_SPEC, encoding="utf-8")

    # Existing problem definition and rubric
    (artifacts / "problem.md").write_text(_EXISTING_PROBLEM, encoding="utf-8")
    (artifacts / "problem-alignment.md").write_text(_EXISTING_RUBRIC, encoding="utf-8")

    # Surface registry with existing entries
    registry = {
        "section": "section-03",
        "next_id": 2,
        "surfaces": [
            {
                "id": "P-03-0001",
                "kind": "problem_surface",
                "status": "applied",
                "summary": "Session lifecycle inconsistency across modules",
            },
        ],
    }
    (artifacts / "surface-registry.json").write_text(
        json.dumps(registry, indent=2), encoding="utf-8",
    )

    # New surface discovery signal from intent judge
    surface_signal = {
        "section": "section-03",
        "surfaces": [
            {
                "id": "P-03-0002",
                "kind": "problem_surface",
                "axis_ref": "A3",
                "summary": (
                    "Shared seam detected with Section 07 around database "
                    "session provider: background jobs hold sessions for "
                    "minutes, competing with request-scoped sessions for "
                    "the same 20-connection pool. Current pool pressure "
                    "analysis (A3) only considers request-scoped sessions "
                    "and misses this long-lived contention."
                ),
                "evidence": (
                    "Section 07 background job spec requires database access "
                    "for long-running tasks (up to 5 minutes). Global proposal "
                    "notes cross-section impact on session provider."
                ),
                "source_cycle": 2,
            },
        ],
    }
    (signals / "intent-surfaces-02.json").write_text(
        json.dumps(surface_signal, indent=2), encoding="utf-8",
    )

    # Minimal codespace
    for d in ["db", "api", "repositories"]:
        (codespace / d).mkdir(parents=True, exist_ok=True)
        (codespace / d / "__init__.py").write_text("", encoding="utf-8")

    (codespace / "db" / "session.py").write_text(textwrap.dedent("""\
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("postgresql://localhost/app")
        SessionFactory = sessionmaker(bind=engine)

        _session = None

        def get_session():
            global _session
            if _session is None:
                _session = SessionFactory()
            return _session
    """), encoding="utf-8")

    # Build prompt
    prompt_path = artifacts / "problem-expander-03-prompt.md"
    prompt_path.write_text(
        "# Task: Expand Problem Definition for Section 03\n\n"
        "## Current Problem Definition\n\n"
        f"{_EXISTING_PROBLEM}\n\n"
        "## Current Rubric\n\n"
        f"{_EXISTING_RUBRIC}\n\n"
        "## Surface Discovery (from intent judge cycle 2)\n\n"
        f"```json\n{json.dumps(surface_signal, indent=2)}\n```\n\n"
        "## Instructions\n\n"
        "You are the problem-expander. For each surface:\n"
        "1. Triage: already covered, in-scope integration, or out-of-scope discard\n"
        "2. If INTEGRATE: extend the relevant axis or add a new axis\n"
        "3. Update the rubric table\n"
        "4. Emit an intent-delta signal\n\n"
        "The surface describes a shared seam with Section 07 (background jobs)\n"
        "competing for the same connection pool. The existing A3 axis covers\n"
        "pool pressure but only for request-scoped sessions -- this surface\n"
        "reveals long-lived background job sessions as a new contention source.\n\n"
        "Output all content inline. Use markdown headers to delimit each file.\n"
        "Start each file section with: ## FILE: <filename>\n\n"
        "For the delta signal, output it as a fenced JSON block.\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Check functions: intent_pack_with_philosophy
# ---------------------------------------------------------------------------

def _check_pack_phil_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output is non-empty and contains problem definition content."""
    if not agent_output or len(agent_output.strip()) < 100:
        return False, f"Output too short ({len(agent_output)} chars)"
    return True, f"Output is {len(agent_output)} chars"


def _check_pack_phil_has_axes(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains axis-structured problem definition (A1, A2, ...)."""
    axis_pattern = re.findall(r"##\s*A\d+[:\s]", agent_output)
    if len(axis_pattern) >= 2:
        return True, f"Found {len(axis_pattern)} axis headings"
    # Fallback: look for A1, A2 references without heading markup
    axis_refs = re.findall(r"\bA\d+\b", agent_output)
    unique_axes = set(axis_refs)
    if len(unique_axes) >= 2:
        return True, f"Found axis references: {sorted(unique_axes)}"
    return False, "Fewer than 2 axis headings or references found"


def _check_pack_phil_references_section(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the problem definition references the section's core concern."""
    lower = agent_output.lower()
    session_terms = ["session", "database session", "session provider", "connection pool"]
    found = [t for t in session_terms if t in lower]
    if found:
        return True, f"References section concern via: {found}"
    return False, "No reference to session/database session/connection pool"


def _check_pack_phil_has_philosophy(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output includes philosophy-derived content or mentions philosophy.

    The intent-pack-generator should produce a philosophy-excerpt.md when
    philosophy.md is present, and the problem definition should reflect
    design principles from the philosophy.
    """
    lower = agent_output.lower()
    # Check for explicit philosophy excerpt section
    has_excerpt = "philosophy" in lower
    # Check for philosophy principle references
    principle_terms = [
        "fail closed", "fail-closed", "ambiguity",
        "seam ownership", "observable", "telemetry",
        "incremental migration", "incremental",
    ]
    found_principles = [t for t in principle_terms if t in lower]

    if has_excerpt and found_principles:
        return True, (
            f"Philosophy referenced; principles found: {found_principles}"
        )
    if has_excerpt:
        return True, "Philosophy section present in output"
    if found_principles:
        return True, (
            f"Philosophy principles reflected in output: {found_principles}"
        )
    return False, "No philosophy excerpt or philosophy-derived content found"


# ---------------------------------------------------------------------------
# Check functions: intent_pack_without_philosophy
# ---------------------------------------------------------------------------

def _check_pack_nophil_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output is non-empty."""
    if not agent_output or len(agent_output.strip()) < 100:
        return False, f"Output too short ({len(agent_output)} chars)"
    return True, f"Output is {len(agent_output)} chars"


def _check_pack_nophil_has_axes(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains axis-structured problem definition."""
    axis_pattern = re.findall(r"##\s*A\d+[:\s]", agent_output)
    if len(axis_pattern) >= 2:
        return True, f"Found {len(axis_pattern)} axis headings"
    axis_refs = re.findall(r"\bA\d+\b", agent_output)
    unique_axes = set(axis_refs)
    if len(unique_axes) >= 2:
        return True, f"Found axis references: {sorted(unique_axes)}"
    return False, "Fewer than 2 axis headings or references found"


def _check_pack_nophil_no_hallucination(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the agent does NOT hallucinate philosophy content.

    When no philosophy.md is provided, the agent should not fabricate
    principles, produce a philosophy-excerpt.md, or reference design
    philosophy that was not in the inputs.
    """
    lower = agent_output.lower()

    # Check for fabricated philosophy excerpt file
    fabricated_excerpt = bool(
        re.search(r"##\s*file:\s*philosophy-excerpt", lower)
    )
    if fabricated_excerpt:
        return False, "Agent produced a philosophy-excerpt.md when no philosophy was provided"

    # Check for fabricated principle language that maps to our test philosophy
    # (these specific phrases only appear in _PHILOSOPHY, not in the section spec)
    hallucination_markers = [
        "fail closed on ambiguity",
        "seam ownership",
        "observable by default",
        "incremental migration",
        "operational philosophy",
    ]
    found = [m for m in hallucination_markers if m in lower]
    if found:
        return False, (
            f"Agent hallucinated philosophy content not in inputs: {found}"
        )
    return True, "No hallucinated philosophy content detected"


# ---------------------------------------------------------------------------
# Check functions: problem_expander_deepens_on_surface
# ---------------------------------------------------------------------------

def _check_expander_nonempty(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output is non-empty."""
    if not agent_output or len(agent_output.strip()) < 100:
        return False, f"Output too short ({len(agent_output)} chars)"
    return True, f"Output is {len(agent_output)} chars"


def _check_expander_more_detail(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the expanded output contains more detail than the original.

    The expander should have added content (new axis or extended existing
    axis) in response to the surface discovery.
    """
    # The original problem.md is ~1500 chars. The output should contain
    # at least as much plus new content.
    original_len = len(_EXISTING_PROBLEM)
    # Look for the updated problem definition in the output
    # It should be longer than the original because of the integration
    if len(agent_output) > original_len:
        return True, (
            f"Output ({len(agent_output)} chars) exceeds original "
            f"problem ({original_len} chars)"
        )
    return False, (
        f"Output ({len(agent_output)} chars) not longer than original "
        f"({original_len} chars)"
    )


def _check_expander_references_surface(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the output references the surface/seam discovery.

    The surface is about background jobs competing for the connection pool
    (shared seam with Section 07).
    """
    lower = agent_output.lower()
    surface_terms = [
        "background job", "section 07", "long-lived", "long-running",
        "seam", "contention", "background",
    ]
    found = [t for t in surface_terms if t in lower]
    if len(found) >= 2:
        return True, f"Surface/seam referenced via: {found}"
    if found:
        return True, f"Surface partially referenced via: {found}"
    return False, "No reference to the surface discovery (background jobs / Section 07 seam)"


def _check_expander_preserves_axes(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify existing axes (A1-A4) are preserved in the output.

    The problem-expander must never rewrite or remove existing axes.
    """
    # Check that all original axis IDs appear in the output
    expected_axes = ["A1", "A2", "A3", "A4"]
    missing = [ax for ax in expected_axes if ax not in agent_output]
    if not missing:
        return True, f"All original axes preserved: {expected_axes}"
    return False, f"Missing original axes in output: {missing}"


def _check_expander_delta_signal(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the expander emitted a delta signal with integration record.

    The delta should indicate what was applied/discarded and whether a
    restart is required.
    """
    # Try to find delta signal in output (fenced JSON)
    json_blocks = re.findall(
        r"```(?:json)?\s*\n(\{.*?\})\s*\n```", agent_output, re.DOTALL,
    )
    for block in json_blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict) and (
                "applied_surface_ids" in data or "applied" in data
            ):
                return True, f"Delta signal found: {json.dumps(data)[:200]}"
        except json.JSONDecodeError:
            continue

    # Fallback: check for any JSON object with delta fields
    for match in re.finditer(
        r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", agent_output,
    ):
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and (
                "applied_surface_ids" in data
                or "restart_required" in data
                or "new_axes" in data
            ):
                return True, f"Delta signal found inline: {json.dumps(data)[:200]}"
        except json.JSONDecodeError:
            continue

    # Also check if a signal file was written
    signals_dir = planspace / "artifacts" / "signals"
    for f in signals_dir.iterdir() if signals_dir.exists() else []:
        if f.name.startswith("intent-delta") and f.suffix == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                return True, f"Delta signal file {f.name}: {json.dumps(data)[:200]}"
            except (json.JSONDecodeError, OSError):
                continue

    return False, "No delta signal (intent-delta JSON) found in output or signal files"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="intent_pack_with_philosophy",
        agent_file="intent-pack-generator.md",
        model_policy_key="intent_pack",
        setup=_setup_pack_with_philosophy,
        checks=[
            Check(
                description="Output is non-empty",
                verify=_check_pack_phil_nonempty,
            ),
            Check(
                description="Contains axis-structured problem definition (A1, A2, ...)",
                verify=_check_pack_phil_has_axes,
            ),
            Check(
                description="Problem definition references the section concern (sessions/pool)",
                verify=_check_pack_phil_references_section,
            ),
            Check(
                description="Output includes philosophy-derived content or excerpt",
                verify=_check_pack_phil_has_philosophy,
            ),
        ],
    ),
    Scenario(
        name="intent_pack_without_philosophy",
        agent_file="intent-pack-generator.md",
        model_policy_key="intent_pack",
        setup=_setup_pack_without_philosophy,
        checks=[
            Check(
                description="Output is non-empty",
                verify=_check_pack_nophil_nonempty,
            ),
            Check(
                description="Contains axis-structured problem definition (A1, A2, ...)",
                verify=_check_pack_nophil_has_axes,
            ),
            Check(
                description="Does NOT hallucinate philosophy content",
                verify=_check_pack_nophil_no_hallucination,
            ),
        ],
    ),
    Scenario(
        name="problem_expander_deepens_on_surface",
        agent_file="problem-expander.md",
        model_policy_key="intent_problem_expander",
        setup=_setup_expander_with_surface,
        checks=[
            Check(
                description="Output is non-empty",
                verify=_check_expander_nonempty,
            ),
            Check(
                description="Expanded output contains more detail than the original problem",
                verify=_check_expander_more_detail,
            ),
            Check(
                description="Output references the surface/seam discovery (background jobs, Section 07)",
                verify=_check_expander_references_surface,
            ),
            Check(
                description="Original axes (A1-A4) are preserved",
                verify=_check_expander_preserves_axes,
            ),
            Check(
                description="Delta signal emitted with integration record",
                verify=_check_expander_delta_signal,
            ),
        ],
    ),
]
