"""Re-explorer scenario evals.

Tests that the section-re-explorer agent correctly classifies sections
as brownfield vs greenfield and writes the structured mode signal JSON.

Scenarios:
  reexplorer_brownfield: Section with matching code -> mode=brownfield
  reexplorer_greenfield: Section with no matching code -> mode=greenfield
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_AUTH_SECTION_SPEC = textwrap.dedent("""\
    # Section 01: User Authentication

    ## Problem
    The user authentication system needs to validate credentials against
    the existing user store and issue session tokens. Password hashing
    must use bcrypt with a configurable work factor. Failed login attempts
    must be rate-limited per IP address.

    ## Requirements
    - REQ-01: Validate username/password against user store
    - REQ-02: Issue JWT session tokens on successful authentication
    - REQ-03: Rate-limit failed login attempts (max 5 per 15 minutes per IP)
    - REQ-04: Support configurable bcrypt work factor (default 12)

    ## Constraints
    - Must integrate with existing UserRepository interface
    - Must not break existing session middleware
""")

_AUTH_LOGIN_PY = textwrap.dedent("""\
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
            \"\"\"Authenticate user and return JWT token, or None on failure.\"\"\"
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
""")

_CODEMAP_WITH_AUTH = textwrap.dedent("""\
    # Project Codemap

    ## auth/
    - `auth/login.py` - Authentication service with bcrypt + JWT
    - `auth/user_repository.py` - User data access layer
    - `auth/rate_limiter.py` - IP-based rate limiting for login attempts

    ## api/
    - `api/routes.py` - HTTP route definitions
    - `api/middleware.py` - Session middleware and request validation

    ## models/
    - `models/user.py` - User model definition
    - `models/session.py` - Session model definition
""")

_NOTIF_SECTION_SPEC = textwrap.dedent("""\
    # Section 02: Real-Time Notification System

    ## Problem
    Users need to receive real-time notifications when events occur in
    the system (new messages, status changes, collaborative edits).
    The notification system must support WebSocket delivery, notification
    preferences per user, and a persistence layer for offline users.

    ## Requirements
    - REQ-01: WebSocket-based real-time delivery to connected clients
    - REQ-02: Per-user notification preferences (email, push, in-app)
    - REQ-03: Queue notifications for offline users, deliver on reconnect
    - REQ-04: Support notification templates with variable substitution
    - REQ-05: Notification deduplication within a configurable time window

    ## Constraints
    - Must scale to 10k concurrent WebSocket connections
    - Must not couple to any specific message broker
""")

_CODEMAP_NO_NOTIF = textwrap.dedent("""\
    # Project Codemap

    ## auth/
    - `auth/login.py` - Authentication service
    - `auth/user_repository.py` - User data access layer

    ## api/
    - `api/routes.py` - HTTP route definitions
    - `api/middleware.py` - Session middleware

    ## models/
    - `models/user.py` - User model definition

    ## utils/
    - `utils/config.py` - Configuration loader
    - `utils/logging.py` - Structured logging setup
""")


# ---------------------------------------------------------------------------
# Setup functions
# ---------------------------------------------------------------------------

def _setup_brownfield(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for brownfield re-exploration."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-01.md"
    section_path.write_text(_AUTH_SECTION_SPEC, encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CODEMAP_WITH_AUTH, encoding="utf-8")

    # Codespace with auth code
    auth_dir = codespace / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "__init__.py").write_text("", encoding="utf-8")
    (auth_dir / "login.py").write_text(_AUTH_LOGIN_PY, encoding="utf-8")
    (auth_dir / "user_repository.py").write_text(
        "class UserRepository:\n    pass\n", encoding="utf-8",
    )
    (auth_dir / "rate_limiter.py").write_text(
        "class RateLimiter:\n    pass\n", encoding="utf-8",
    )

    # Write the prompt (mirrors reexplore.py's prompt structure)
    prompt_path = artifacts / "reexplore-01-prompt.md"
    signal_path = signals / "section-01-mode.json"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Re-Explore Section 01

        ## Summary
        User authentication system with bcrypt password hashing,
        JWT token issuance, and IP-based rate limiting.

        ## Files to Read
        1. Section specification: `{section_path}`
        2. Codespace root: `{codespace}`
        3. Codemap: `{codemap_path}`

        ## Context
        This section has NO related files after the initial codemap exploration.
        Your job is to determine why and classify the situation.

        ## Instructions
        1. Read the section specification to understand the problem
        2. Read the codemap for project structure context
        3. Explore the codespace strategically

        ## Output

        If you find related files, append them to the section file at
        `{section_path}` using the standard format:

        ```
        ## Related Files

        ### <relative-path>
        Brief reason why this file matters.
        ```

        Then write a brief classification to stdout:
        - `section_mode: brownfield | greenfield | hybrid`
        - Justification (1-2 sentences)

        **Also write a structured JSON signal** to
        `{signal_path}`:
        ```json
        {{"mode": "brownfield|greenfield|hybrid", "confidence": "high|medium|low", "reason": "..."}}
        ```
        This is how the pipeline reads your classification.
    """), encoding="utf-8")

    return prompt_path


def _setup_greenfield(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for greenfield re-exploration."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    # Section spec
    section_path = sections / "section-02.md"
    section_path.write_text(_NOTIF_SECTION_SPEC, encoding="utf-8")

    # Codemap with NO notification-related code
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CODEMAP_NO_NOTIF, encoding="utf-8")

    # Codespace with unrelated code only
    auth_dir = codespace / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "__init__.py").write_text("", encoding="utf-8")
    (auth_dir / "login.py").write_text(
        "class AuthService:\n    pass\n", encoding="utf-8",
    )
    utils_dir = codespace / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "config.py").write_text(
        "def load_config():\n    pass\n", encoding="utf-8",
    )

    # Write the prompt
    prompt_path = artifacts / "reexplore-02-prompt.md"
    signal_path = signals / "section-02-mode.json"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Re-Explore Section 02

        ## Summary
        Real-time notification system with WebSocket delivery,
        per-user preferences, and offline queuing.

        ## Files to Read
        1. Section specification: `{section_path}`
        2. Codespace root: `{codespace}`
        3. Codemap: `{codemap_path}`

        ## Context
        This section has NO related files after the initial codemap exploration.
        Your job is to determine why and classify the situation.

        ## Instructions
        1. Read the section specification to understand the problem
        2. Read the codemap for project structure context
        3. Explore the codespace strategically

        ## Output

        If you find related files, append them to the section file at
        `{section_path}` using the standard format:

        ```
        ## Related Files

        ### <relative-path>
        Brief reason why this file matters.
        ```

        Then write a brief classification to stdout:
        - `section_mode: brownfield | greenfield | hybrid`
        - Justification (1-2 sentences)

        **Also write a structured JSON signal** to
        `{signal_path}`:
        ```json
        {{"mode": "brownfield|greenfield|hybrid", "confidence": "high|medium|low", "reason": "..."}}
        ```
        This is how the pipeline reads your classification.
    """), encoding="utf-8")

    return prompt_path


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _check_brownfield_mode(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify that mode signal JSON has mode=brownfield."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "section-01-mode.json")
    if not signal_path.exists():
        return False, f"Signal file not written: {signal_path}"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Signal file is not valid JSON: {exc}"
    mode = data.get("mode", "")
    if mode == "brownfield":
        return True, f"mode={mode}"
    return False, f"Expected mode=brownfield, got mode={mode}"


def _check_brownfield_mentions_auth(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify that agent output mentions the auth file."""
    lower = agent_output.lower()
    if "login.py" in lower or "auth" in lower:
        return True, "Output mentions auth/login code"
    return False, "Output does not mention auth/login.py or auth directory"


def _check_greenfield_mode(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify that mode signal JSON has mode=greenfield."""
    signal_path = (planspace / "artifacts" / "signals"
                   / "section-02-mode.json")
    if not signal_path.exists():
        return False, f"Signal file not written: {signal_path}"
    try:
        data = json.loads(signal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Signal file is not valid JSON: {exc}"
    mode = data.get("mode", "")
    if mode == "greenfield":
        return True, f"mode={mode}"
    return False, f"Expected mode=greenfield, got mode={mode}"


def _check_greenfield_no_scaffolding(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify that greenfield output does NOT contain scaffolding language."""
    lower = agent_output.lower()
    scaffolding_phrases = ["create new file", "proceed with", "let me create"]
    found = [p for p in scaffolding_phrases if p in lower]
    if found:
        return False, f"Output contains scaffolding language: {found}"
    return True, "No scaffolding language found in output"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="reexplorer_brownfield",
        agent_file="section-re-explorer.md",
        model_policy_key="setup",
        setup=_setup_brownfield,
        checks=[
            Check(
                description="Mode signal JSON has mode=brownfield",
                verify=_check_brownfield_mode,
            ),
            Check(
                description="Agent output mentions auth-related code",
                verify=_check_brownfield_mentions_auth,
            ),
        ],
    ),
    Scenario(
        name="reexplorer_greenfield",
        agent_file="section-re-explorer.md",
        model_policy_key="setup",
        setup=_setup_greenfield,
        checks=[
            Check(
                description="Mode signal JSON has mode=greenfield",
                verify=_check_greenfield_mode,
            ),
            Check(
                description="Output does not contain scaffolding language",
                verify=_check_greenfield_no_scaffolding,
            ),
        ],
    ),
]
