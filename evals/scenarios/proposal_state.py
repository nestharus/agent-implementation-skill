"""Proposal-state scenario evals.

Tests that the integration-proposer agent correctly emits proposal-state
artifacts with the right shape depending on the codebase context:
vacuum (no related files), partial anchors, and fully resolved brownfield.
Also verifies that the proposer output does not contain scaffolding
language (no inventing file paths or module structures).

Scenarios:
  proposal_state_vacuum: No related code -> unresolved fields, execution_ready=false
  proposal_state_partial_anchors: Some anchors resolved, contracts unresolved
  proposal_state_brownfield_ready: Fully resolved -> execution_ready=true
  proposal_state_no_scaffolding: Proposer output avoids architecture invention
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VACUUM_SECTION_SPEC = textwrap.dedent("""\
    # Section 08: Telemetry Data Pipeline

    ## Problem
    The system needs a telemetry pipeline that collects usage metrics from
    all services, aggregates them in a time-series store, and exposes a
    query API for dashboards. The pipeline must support custom metric
    definitions, configurable retention policies, and backpressure when
    the collection rate exceeds the write throughput.

    ## Requirements
    - REQ-01: Collect structured metrics from all services via a common SDK
    - REQ-02: Aggregate metrics in a time-series store with configurable windows
    - REQ-03: Expose a query API for dashboard consumption
    - REQ-04: Support custom metric type definitions at runtime
    - REQ-05: Implement backpressure when write throughput is exceeded

    ## Constraints
    - Must not introduce coupling between services and the telemetry backend
    - Must support at least 10k metrics/second sustained throughput
""")

_PARTIAL_SECTION_SPEC = textwrap.dedent("""\
    # Section 09: Audit Trail Service

    ## Problem
    The system needs a tamper-evident audit trail that records all
    state-changing operations. The audit log must be append-only,
    support cryptographic verification, and integrate with the existing
    UserRepository for actor identification.

    ## Requirements
    - REQ-01: Append-only audit log for all state-changing operations
    - REQ-02: Cryptographic chaining (hash chain) for tamper detection
    - REQ-03: Actor identification via UserRepository integration
    - REQ-04: Structured query API for compliance reporting
    - REQ-05: Configurable retention and archival policies

    ## Constraints
    - Must integrate with existing UserRepository interface
    - Must not degrade write performance of audited operations
    - Audit entries must reference existing domain events

    ## Related Files

    ### auth/user_repository.py
    Existing user data access layer -- needed for actor identification.

    ### events/bus.py
    Existing event emitter -- audit entries triggered by domain events.
""")

_PARTIAL_USER_REPO_PY = textwrap.dedent("""\
    class UserRepository:
        \"\"\"Data access layer for user records.\"\"\"

        def find_by_id(self, user_id: str) -> dict | None:
            # Real implementation would query DB
            return {"user_id": user_id, "username": f"user_{user_id}"}

        def find_by_username(self, username: str) -> dict | None:
            return {"user_id": "1", "username": username}
""")

_PARTIAL_EVENT_BUS_PY = textwrap.dedent("""\
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

_BROWNFIELD_SECTION_SPEC = textwrap.dedent("""\
    # Section 10: Password Reset Flow

    ## Problem
    Users need a self-service password reset flow. The system must
    generate time-limited reset tokens, validate them, and update
    the password hash in the user store.

    ## Requirements
    - REQ-01: Generate cryptographically random reset tokens
    - REQ-02: Tokens expire after 30 minutes
    - REQ-03: Validate token and update password via UserRepository
    - REQ-04: Rate-limit reset requests per email address

    ## Constraints
    - Must integrate with existing UserRepository
    - Must use existing RateLimiter for rate limiting
    - Must use existing bcrypt hashing from AuthService

    ## Related Files

    ### auth/login.py
    Existing AuthService with bcrypt hashing -- reuse password hashing.

    ### auth/user_repository.py
    Existing user data access -- needed for password update.

    ### auth/rate_limiter.py
    Existing rate limiter -- reuse for reset request throttling.
""")

_BROWNFIELD_AUTH_LOGIN_PY = textwrap.dedent("""\
    import bcrypt
    import jwt
    from datetime import datetime, timedelta

    from .user_repository import UserRepository
    from .rate_limiter import RateLimiter


    class AuthService:
        \"\"\"Handles user authentication with rate limiting.\"\"\"

        def __init__(self, user_repo: UserRepository, secret_key: str,
                     bcrypt_rounds: int = 12):
            self.user_repo = user_repo
            self.secret_key = secret_key
            self.bcrypt_rounds = bcrypt_rounds
            self.rate_limiter = RateLimiter(max_attempts=5, window_minutes=15)

        def authenticate(self, username: str, password: str,
                         client_ip: str) -> str | None:
            if self.rate_limiter.is_blocked(client_ip):
                return None
            user = self.user_repo.find_by_username(username)
            if user is None:
                self.rate_limiter.record_failure(client_ip)
                return None
            if not bcrypt.checkpw(password.encode(), user.password_hash):
                self.rate_limiter.record_failure(client_ip)
                return None
            self.rate_limiter.reset(client_ip)
            return self._issue_token(user.id, user.username)

        def _issue_token(self, user_id: int, username: str) -> str:
            payload = {
                "sub": str(user_id),
                "username": username,
                "exp": datetime.utcnow() + timedelta(hours=8),
            }
            return jwt.encode(payload, self.secret_key, algorithm="HS256")

        def hash_password(self, password: str) -> bytes:
            return bcrypt.hashpw(password.encode(), bcrypt.gensalt(self.bcrypt_rounds))
""")

_BROWNFIELD_USER_REPO_PY = textwrap.dedent("""\
    class UserRepository:
        \"\"\"User data access layer.\"\"\"

        def find_by_username(self, username: str):
            pass

        def find_by_id(self, user_id: str):
            pass

        def update_password(self, user_id: str, password_hash: bytes) -> None:
            pass

        def find_by_email(self, email: str):
            pass
""")

_BROWNFIELD_RATE_LIMITER_PY = textwrap.dedent("""\
    import time
    from collections import defaultdict


    class RateLimiter:
        \"\"\"IP-based rate limiter.\"\"\"

        def __init__(self, max_attempts: int = 5, window_minutes: int = 15):
            self.max_attempts = max_attempts
            self.window_seconds = window_minutes * 60
            self._attempts: dict[str, list[float]] = defaultdict(list)

        def is_blocked(self, key: str) -> bool:
            now = time.time()
            self._attempts[key] = [
                t for t in self._attempts[key]
                if now - t < self.window_seconds
            ]
            return len(self._attempts[key]) >= self.max_attempts

        def record_failure(self, key: str) -> None:
            self._attempts[key].append(time.time())

        def reset(self, key: str) -> None:
            self._attempts.pop(key, None)
""")

_CODEMAP_MINIMAL = textwrap.dedent("""\
    # Project Codemap

    ## auth/
    - `auth/login.py` - Authentication service with bcrypt + JWT
    - `auth/user_repository.py` - User data access layer
    - `auth/rate_limiter.py` - IP-based rate limiting

    ## api/
    - `api/routes.py` - HTTP route definitions

    ## utils/
    - `utils/config.py` - Configuration loader
""")

_CODEMAP_VACUUM = textwrap.dedent("""\
    # Project Codemap

    ## api/
    - `api/routes.py` - HTTP route definitions
    - `api/middleware.py` - Request middleware

    ## models/
    - `models/user.py` - User model
""")


# ---------------------------------------------------------------------------
# Setup functions
# ---------------------------------------------------------------------------

def _setup_vacuum(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for a vacuum section (no related files)."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-08.md"
    section_path.write_text(_VACUUM_SECTION_SPEC, encoding="utf-8")

    # Codemap with no telemetry-related code
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CODEMAP_VACUUM, encoding="utf-8")

    # Mode signal (greenfield)
    mode_signal = signals / "section-08-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "greenfield", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Codespace with only unrelated code
    api_dir = codespace / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "routes.py").write_text(
        "def index():\n    return 'ok'\n", encoding="utf-8")

    # Proposal-state artifact path (agent writes here)
    proposal_state_path = (
        proposals / "section-08-proposal-state.json"
    )

    # Write the prompt
    prompt_path = artifacts / "integration-proposer-08-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Integration Proposal for Section 08

        ## Files to Read
        1. Section specification: `{section_path}`
        2. Codespace root: `{codespace}`
        3. Codemap: `{codemap_path}`

        ## Section Characteristics
        - Related files: 0
        - Section mode: greenfield
        - Previous proposal attempts: 0

        ## Instructions
        Read the section specification. Explore the codespace. Write your
        integration proposal as a problem-state diagnostic.

        Write the human-readable proposal to:
        `{proposals / "section-08-integration-proposal.md"}`

        Write the machine-readable proposal-state JSON to:
        `{proposal_state_path}`

        The proposal-state JSON must have these fields:
        resolved_anchors, unresolved_anchors, resolved_contracts,
        unresolved_contracts, research_questions, user_root_questions,
        new_section_candidates, shared_seam_candidates, execution_ready,
        readiness_rationale.

        This is a greenfield section with NO related files in the codebase.
        Set execution_ready to false if there are any unresolved anchors,
        unresolved contracts, user root questions, or shared seam candidates.
    """), encoding="utf-8")

    return prompt_path


def _setup_partial_anchors(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for a section with partial anchors."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-09.md"
    section_path.write_text(_PARTIAL_SECTION_SPEC, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(textwrap.dedent("""\
        # Project Codemap

        ## auth/
        - `auth/user_repository.py` - User data access layer

        ## events/
        - `events/bus.py` - Basic event emitter

        ## api/
        - `api/routes.py` - HTTP route definitions
    """), encoding="utf-8")

    # Mode signal (hybrid)
    mode_signal = signals / "section-09-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "hybrid", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Codespace with partial related code
    auth_dir = codespace / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "__init__.py").write_text("", encoding="utf-8")
    (auth_dir / "user_repository.py").write_text(
        _PARTIAL_USER_REPO_PY, encoding="utf-8")

    events_dir = codespace / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "__init__.py").write_text("", encoding="utf-8")
    (events_dir / "bus.py").write_text(
        _PARTIAL_EVENT_BUS_PY, encoding="utf-8")

    # No audit trail code exists -- that's the point
    proposal_state_path = (
        proposals / "section-09-proposal-state.json"
    )

    # Write the prompt
    prompt_path = artifacts / "integration-proposer-09-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Integration Proposal for Section 09

        ## Files to Read
        1. Section specification: `{section_path}`
        2. Codespace root: `{codespace}`
        3. Codemap: `{artifacts / "codemap.md"}`

        ## Section Characteristics
        - Related files: 2 (user_repository.py, bus.py)
        - Section mode: hybrid
        - Previous proposal attempts: 0

        ## Instructions
        Read the section specification. Explore the codespace. Write your
        integration proposal as a problem-state diagnostic.

        The UserRepository exists and provides the actor identification anchor.
        The EventBus exists and provides the event trigger anchor. However,
        the audit trail itself (append-only log, hash chain, query API) does
        NOT exist in the codebase -- those anchors and contracts are unresolved.

        Write the human-readable proposal to:
        `{proposals / "section-09-integration-proposal.md"}`

        Write the machine-readable proposal-state JSON to:
        `{proposal_state_path}`

        The proposal-state JSON must have these fields:
        resolved_anchors, unresolved_anchors, resolved_contracts,
        unresolved_contracts, research_questions, user_root_questions,
        new_section_candidates, shared_seam_candidates, execution_ready,
        readiness_rationale.

        Mark anchors for UserRepository and EventBus integration as resolved.
        Mark anchors for the audit log storage, hash chain, and query API as
        unresolved. Set execution_ready to false because unresolved anchors
        and contracts remain.
    """), encoding="utf-8")

    return prompt_path


def _setup_brownfield_ready(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for a fully resolved brownfield section."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    proposals = artifacts / "proposals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-10.md"
    section_path.write_text(_BROWNFIELD_SECTION_SPEC, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CODEMAP_MINIMAL, encoding="utf-8")

    # Mode signal (brownfield)
    mode_signal = signals / "section-10-mode.json"
    mode_signal.write_text(
        json.dumps({"mode": "brownfield", "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    # Codespace with all related code
    auth_dir = codespace / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "__init__.py").write_text("", encoding="utf-8")
    (auth_dir / "login.py").write_text(
        _BROWNFIELD_AUTH_LOGIN_PY, encoding="utf-8")
    (auth_dir / "user_repository.py").write_text(
        _BROWNFIELD_USER_REPO_PY, encoding="utf-8")
    (auth_dir / "rate_limiter.py").write_text(
        _BROWNFIELD_RATE_LIMITER_PY, encoding="utf-8")

    proposal_state_path = (
        proposals / "section-10-proposal-state.json"
    )

    # Write the prompt
    prompt_path = artifacts / "integration-proposer-10-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Integration Proposal for Section 10

        ## Files to Read
        1. Section specification: `{section_path}`
        2. Codespace root: `{codespace}`
        3. Codemap: `{codemap_path}`

        ## Section Characteristics
        - Related files: 3 (login.py, user_repository.py, rate_limiter.py)
        - Section mode: brownfield
        - Previous proposal attempts: 0

        ## Instructions
        Read the section specification. Explore the codespace. Write your
        integration proposal as a problem-state diagnostic.

        All integration surfaces for this section exist in the codebase:
        - AuthService.hash_password() for bcrypt hashing
        - UserRepository.update_password() for password updates
        - UserRepository.find_by_email() for email lookup
        - RateLimiter for rate-limiting reset requests

        Every anchor has concrete existing code. Every interface contract is
        verifiable in the existing files. There are no cross-section seams
        and no user questions.

        Write the human-readable proposal to:
        `{proposals / "section-10-integration-proposal.md"}`

        Write the machine-readable proposal-state JSON to:
        `{proposal_state_path}`

        Mark all anchors as resolved. Mark all contracts as resolved.
        Set execution_ready to true with a rationale explaining why all
        integration points are covered by existing code.
    """), encoding="utf-8")

    return prompt_path


def _setup_no_scaffolding(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for scaffolding-language detection.

    Uses the same vacuum scenario but the check specifically verifies
    that the proposer output avoids architecture invention phrases.
    """
    return _setup_vacuum(planspace, codespace)


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _check_vacuum_state_written(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify proposal-state JSON was written for vacuum section."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-08-proposal-state.json")
    if not state_path.exists():
        return False, f"Proposal-state not written: {state_path}"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Proposal-state is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return False, f"Proposal-state is not a dict: {type(data)}"
    return True, "Proposal-state JSON written and parseable"


def _check_vacuum_not_ready(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify execution_ready=false for vacuum section."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-08-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    ready = data.get("execution_ready")
    if ready is False:
        return True, "execution_ready=false (correct for vacuum)"
    return False, f"Expected execution_ready=false, got {ready}"


def _check_vacuum_has_unresolved(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify vacuum section has unresolved anchors or contracts."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-08-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    unresolved_anchors = data.get("unresolved_anchors", [])
    unresolved_contracts = data.get("unresolved_contracts", [])
    if unresolved_anchors or unresolved_contracts:
        return True, (
            f"Has {len(unresolved_anchors)} unresolved anchors, "
            f"{len(unresolved_contracts)} unresolved contracts"
        )
    return False, "Expected unresolved items for vacuum section, found none"


def _check_partial_state_written(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify proposal-state JSON was written for partial anchors section."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-09-proposal-state.json")
    if not state_path.exists():
        return False, f"Proposal-state not written: {state_path}"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Proposal-state is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return False, f"Proposal-state is not a dict: {type(data)}"
    return True, "Proposal-state JSON written and parseable"


def _check_partial_has_resolved(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify partial section has at least one resolved anchor."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-09-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    resolved = data.get("resolved_anchors", [])
    if resolved:
        return True, f"Has {len(resolved)} resolved anchors"
    return False, "Expected at least one resolved anchor, found none"


def _check_partial_not_ready(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify execution_ready=false for partial section."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-09-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    ready = data.get("execution_ready")
    if ready is False:
        return True, "execution_ready=false (correct for partial anchors)"
    return False, f"Expected execution_ready=false, got {ready}"


def _check_partial_has_unresolved_contracts(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify partial section has unresolved contracts."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-09-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    unresolved = data.get("unresolved_contracts", [])
    if unresolved:
        return True, f"Has {len(unresolved)} unresolved contracts"
    return False, "Expected unresolved contracts, found none"


def _check_brownfield_ready(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify execution_ready=true for fully resolved brownfield section."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-10-proposal-state.json")
    if not state_path.exists():
        return False, f"Proposal-state not written: {state_path}"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Proposal-state is not valid JSON: {exc}"
    ready = data.get("execution_ready")
    if ready is True:
        return True, "execution_ready=true (correct for brownfield)"
    return False, f"Expected execution_ready=true, got {ready}"


def _check_brownfield_has_resolved_anchors(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify brownfield section has resolved anchors."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-10-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    resolved = data.get("resolved_anchors", [])
    if resolved:
        return True, f"Has {len(resolved)} resolved anchors"
    return False, "Expected resolved anchors for brownfield, found none"


def _check_brownfield_no_blocking_fields(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify brownfield section has no items in blocking fields."""
    state_path = (planspace / "artifacts" / "proposals"
                  / "section-10-proposal-state.json")
    if not state_path.exists():
        return False, "Proposal-state not written"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "Proposal-state is not valid JSON"
    blocking_fields = [
        "unresolved_anchors", "unresolved_contracts",
        "user_root_questions", "shared_seam_candidates",
    ]
    non_empty = [
        f for f in blocking_fields
        if data.get(f) and isinstance(data[f], list) and len(data[f]) > 0
    ]
    if non_empty:
        return False, f"Blocking fields still populated: {non_empty}"
    return True, "No blocking fields populated"


def _check_no_scaffolding_language(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify proposer output does not contain scaffolding language.

    The integration-proposer must NOT tell the agent to decide "what NEW
    files and modules to create" or "where they belong."  This check
    catches architecture-invention phrases in the agent's output.
    """
    lower = agent_output.lower()
    scaffolding_phrases = [
        "create new file",
        "create the following files",
        "new module structure",
        "project structure should be",
        "let me create",
        "i will create",
        "we need to create",
        "where they belong",
        "proposed directory structure",
        "file layout",
    ]
    found = [p for p in scaffolding_phrases if p in lower]
    if found:
        return False, f"Output contains scaffolding language: {found}"
    return True, "No scaffolding language found in output"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="proposal_state_vacuum",
        agent_file="integration-proposer.md",
        model_policy_key="proposal",
        setup=_setup_vacuum,
        checks=[
            Check(
                description="Proposal-state JSON written and parseable",
                verify=_check_vacuum_state_written,
            ),
            Check(
                description="execution_ready=false for vacuum section",
                verify=_check_vacuum_not_ready,
            ),
            Check(
                description="Vacuum section has unresolved anchors or contracts",
                verify=_check_vacuum_has_unresolved,
            ),
            Check(
                description="No scaffolding language in output",
                verify=_check_no_scaffolding_language,
            ),
        ],
    ),
    Scenario(
        name="proposal_state_partial_anchors",
        agent_file="integration-proposer.md",
        model_policy_key="proposal",
        setup=_setup_partial_anchors,
        checks=[
            Check(
                description="Proposal-state JSON written and parseable",
                verify=_check_partial_state_written,
            ),
            Check(
                description="At least one resolved anchor present",
                verify=_check_partial_has_resolved,
            ),
            Check(
                description="Unresolved contracts present",
                verify=_check_partial_has_unresolved_contracts,
            ),
            Check(
                description="execution_ready=false for partial section",
                verify=_check_partial_not_ready,
            ),
        ],
    ),
    Scenario(
        name="proposal_state_brownfield_ready",
        agent_file="integration-proposer.md",
        model_policy_key="proposal",
        setup=_setup_brownfield_ready,
        checks=[
            Check(
                description="execution_ready=true for brownfield section",
                verify=_check_brownfield_ready,
            ),
            Check(
                description="Resolved anchors present",
                verify=_check_brownfield_has_resolved_anchors,
            ),
            Check(
                description="No blocking fields populated",
                verify=_check_brownfield_no_blocking_fields,
            ),
        ],
    ),
    Scenario(
        name="proposal_state_no_scaffolding",
        agent_file="integration-proposer.md",
        model_policy_key="proposal",
        setup=_setup_no_scaffolding,
        checks=[
            Check(
                description="No scaffolding language in proposer output",
                verify=_check_no_scaffolding_language,
            ),
        ],
    ),
]
