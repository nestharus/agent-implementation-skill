"""Substrate seeder agent scenario evals.

Tests that the substrate-seeder agent produces correct anchor stubs
matching the SIS (Shared Integration Substrate) contract defined by
the seed plan, and that it does NOT produce full implementations.

The seeder reads seed-plan.json and substrate.md, then creates:
  1. Minimal anchor files in codespace (stubs, not implementations)
  2. Related-files update signals per wired section
  3. Substrate input refs per wired section
  4. A completion signal (seed-signal.json)

These scenarios dispatch the real agent with pre-seeded substrate
artifacts and check the structured outputs against the SIS anchor
contract.

Scenarios:
  substrate_seeder_anchor_contract: Greenfield seeding produces
      correct stubs matching declared surfaces.
  substrate_seeder_no_implementation: Complex surfaces remain at
      anchor/interface level without business logic.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures: seed plans and substrate docs
# ---------------------------------------------------------------------------

_SIMPLE_SUBSTRATE_DOC = textwrap.dedent("""\
    # Shared Integration Substrate

    ## Shared Seams Decided

    ### 1. Async Session Provider
    Multiple sections need async database sessions. A single shared
    session-provider module avoids each section reinventing connection
    management. The provider exposes a context-manager interface that
    sections import and use; they do not create their own engines.

    ### 2. Idempotency Key Store
    Sections 01, 03, and 05 all perform operations that must be
    idempotent. A shared idempotency-key store backed by the database
    prevents duplicate processing. Sections call `check_and_set(key)`
    before executing side effects.

    ## Shared Seams Deferred
    - Logging format: can be standardized later without blocking proposals.

    ## Ownership
    Anchor files are SIS-owned. Sections extend, not redefine.
""")

_SIMPLE_SEED_PLAN = json.dumps({
    "schema_version": 1,
    "anchors": [
        {
            "path": "shared/session_provider.py",
            "purpose": "Async DB session context-manager for cross-section use",
            "owned_by": "SIS",
            "touched_by_sections": [1, 3, 5],
        },
        {
            "path": "shared/idempotency.py",
            "purpose": "Idempotency key check-and-set store",
            "owned_by": "SIS",
            "touched_by_sections": [1, 3, 5],
        },
    ],
    "wire_sections": [1, 3, 5],
    "open_questions": [],
}, indent=2)


_COMPLEX_SUBSTRATE_DOC = textwrap.dedent("""\
    # Shared Integration Substrate

    ## Shared Seams Decided

    ### 1. Event Bus
    An async publish-subscribe event bus for domain events.  Sections
    publish events when state transitions occur; other sections subscribe
    to react.  The bus supports at-least-once delivery with ordered
    per-topic consumption.  Backed by an internal queue with persistence.

    ### 2. Authorization Gate
    A shared authorization gate that sections call before mutating
    resources.  Encapsulates RBAC policy lookups, token validation,
    and scope enforcement.  Sections pass a request context and the
    gate returns allow/deny with a reason.

    ### 3. Distributed Lock Manager
    A shared lock manager for coordinating exclusive access to named
    resources across sections.  Supports TTL-based auto-release and
    fencing tokens.  Backed by Redis or database advisory locks
    depending on deployment.

    ## Shared Seams Deferred
    - Metrics collection: can be added later without blocking.
    - Health-check aggregation: deferrable.

    ## Ownership
    Anchor files are SIS-owned. Sections extend, not redefine.
""")

_COMPLEX_SEED_PLAN = json.dumps({
    "schema_version": 1,
    "anchors": [
        {
            "path": "shared/event_bus.py",
            "purpose": "Async pub-sub event bus for domain events",
            "owned_by": "SIS",
            "touched_by_sections": [2, 4, 6, 8],
        },
        {
            "path": "shared/auth_gate.py",
            "purpose": "Authorization gate for RBAC policy enforcement",
            "owned_by": "SIS",
            "touched_by_sections": [2, 4, 6],
        },
        {
            "path": "shared/lock_manager.py",
            "purpose": "Distributed lock manager with TTL and fencing tokens",
            "owned_by": "SIS",
            "touched_by_sections": [4, 6, 8],
        },
    ],
    "wire_sections": [2, 4, 6, 8],
    "open_questions": [],
}, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IMPLEMENTATION_PATTERNS = re.compile(
    r"""
    (while\s+True)                          # infinite loops
    | (for\s+\w+\s+in\s+range\()           # iteration with range
    | (try:\s*\n\s+.*except)                # try/except with real logic
    | (import\s+redis)                      # concrete library imports
    | (import\s+sqlalchemy)
    | (import\s+asyncpg)
    | (import\s+aiohttp)
    | (\.connect\()                         # connection calls
    | (\.execute\()                         # query execution
    | (\.commit\()                          # transaction commits
    | (SELECT\s+.*FROM)                     # SQL queries
    | (INSERT\s+INTO)
    | (CREATE\s+TABLE)
    | (await\s+\w+\.send\()                 # real async sends
    | (sleep\()                             # blocking calls
    | (threading\.Thread)                   # concurrency primitives
    | (asyncio\.create_task\()
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)

_STUB_MARKERS = re.compile(
    r"""
    (raise\s+NotImplementedError)
    | (pass\s*$)
    | (\.{3}\s*$)                            # ellipsis (...)
    | (stub)
    | (TODO)
    | (placeholder)
    | (not\s+implemented)
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)


def _read_seed_signal(planspace: Path) -> dict | None:
    """Read the seed-signal.json completion signal if it exists."""
    signal_path = planspace / "artifacts" / "substrate" / "seed-signal.json"
    if not signal_path.exists():
        return None
    try:
        return json.loads(signal_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _collect_created_files(codespace: Path) -> list[Path]:
    """Return all files the seeder created under codespace."""
    if not codespace.exists():
        return []
    return [p for p in codespace.rglob("*") if p.is_file()]


def _file_content(path: Path) -> str:
    """Read file content, returning empty string on error."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Setup: anchor contract scenario (simple greenfield)
# ---------------------------------------------------------------------------

def _setup_anchor_contract(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for anchor-contract verification."""
    artifacts = planspace / "artifacts"
    substrate_dir = artifacts / "substrate"
    substrate_dir.mkdir(parents=True, exist_ok=True)

    # Substrate document
    substrate_path = substrate_dir / "substrate.md"
    substrate_path.write_text(_SIMPLE_SUBSTRATE_DOC, encoding="utf-8")

    # Seed plan
    seed_plan_path = substrate_dir / "seed-plan.json"
    seed_plan_path.write_text(_SIMPLE_SEED_PLAN, encoding="utf-8")

    # Codespace is empty greenfield -- nothing to create

    # Build the prompt that dispatch will feed to the agent
    prompt_path = artifacts / "substrate-seeder-prompt.md"
    prompt_path.write_text(
        "# Task: Seed Substrate Anchors\n\n"
        "## Seed Plan\n\n"
        f"Read: `{seed_plan_path}`\n\n"
        "## Substrate Document\n\n"
        f"Read: `{substrate_path}`\n\n"
        "## Codespace Root\n\n"
        f"`{codespace}`\n\n"
        "## Artifact Paths\n\n"
        f"- Related-files signals: `{artifacts}/signals/related-files-update/`\n"
        f"- Substrate input refs: `{artifacts}/inputs/`\n"
        f"- Completion signal: `{substrate_dir}/seed-signal.json`\n\n"
        "## Instructions\n\n"
        "Read the seed plan and substrate document. Create minimal anchor\n"
        "stubs in codespace at the paths listed in the seed plan. Write\n"
        "related-files update signals and substrate refs for wired sections.\n"
        "Write the completion signal when done.\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Setup: no-implementation scenario (complex surfaces)
# ---------------------------------------------------------------------------

def _setup_no_implementation(planspace: Path, codespace: Path) -> Path:
    """Seed artifacts for no-implementation verification (complex surfaces)."""
    artifacts = planspace / "artifacts"
    substrate_dir = artifacts / "substrate"
    substrate_dir.mkdir(parents=True, exist_ok=True)

    # Substrate document (complex surfaces)
    substrate_path = substrate_dir / "substrate.md"
    substrate_path.write_text(_COMPLEX_SUBSTRATE_DOC, encoding="utf-8")

    # Seed plan (complex anchors)
    seed_plan_path = substrate_dir / "seed-plan.json"
    seed_plan_path.write_text(_COMPLEX_SEED_PLAN, encoding="utf-8")

    # Codespace is empty greenfield

    # Build prompt
    prompt_path = artifacts / "substrate-seeder-prompt.md"
    prompt_path.write_text(
        "# Task: Seed Substrate Anchors\n\n"
        "## Seed Plan\n\n"
        f"Read: `{seed_plan_path}`\n\n"
        "## Substrate Document\n\n"
        f"Read: `{substrate_path}`\n\n"
        "## Codespace Root\n\n"
        f"`{codespace}`\n\n"
        "## Artifact Paths\n\n"
        f"- Related-files signals: `{artifacts}/signals/related-files-update/`\n"
        f"- Substrate input refs: `{artifacts}/inputs/`\n"
        f"- Completion signal: `{substrate_dir}/seed-signal.json`\n\n"
        "## Instructions\n\n"
        "Read the seed plan and substrate document. Create minimal anchor\n"
        "stubs in codespace at the paths listed in the seed plan. Write\n"
        "related-files update signals and substrate refs for wired sections.\n"
        "Write the completion signal when done.\n\n"
        "IMPORTANT: These surfaces are complex (event bus, auth gate,\n"
        "distributed lock manager). Create ONLY anchor stubs — interfaces,\n"
        "type definitions, and stub methods. Do NOT implement the actual\n"
        "business logic.\n",
        encoding="utf-8",
    )
    return prompt_path


# ---------------------------------------------------------------------------
# Checks: anchor contract scenario
# ---------------------------------------------------------------------------

def _check_anchor_files_created(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify anchor files from the seed plan exist in codespace."""
    plan = json.loads(_SIMPLE_SEED_PLAN)
    expected_paths = [a["path"] for a in plan["anchors"]]
    missing = []
    for rel_path in expected_paths:
        full = codespace / rel_path
        if not full.exists():
            missing.append(rel_path)
    if missing:
        return False, f"Missing anchor files: {missing}"
    return True, f"All {len(expected_paths)} anchor files created"


def _check_anchors_are_stubs(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify anchor files are minimal stubs, not full implementations."""
    plan = json.loads(_SIMPLE_SEED_PLAN)
    problems = []
    for anchor in plan["anchors"]:
        full = codespace / anchor["path"]
        if not full.exists():
            problems.append(f"{anchor['path']}: file missing")
            continue
        content = _file_content(full)
        if not content.strip():
            problems.append(f"{anchor['path']}: file is empty")
            continue
        # Check for stub markers (at least one expected)
        has_stub_marker = bool(_STUB_MARKERS.search(content))
        # Check for implementation patterns (should be absent)
        impl_matches = _IMPLEMENTATION_PATTERNS.findall(content)
        if impl_matches:
            flat = [m for group in impl_matches for m in group if m]
            problems.append(
                f"{anchor['path']}: contains implementation patterns: "
                f"{flat[:3]}"
            )
        if not has_stub_marker:
            # Not necessarily a failure -- some stubs use empty bodies
            # or type-only definitions. Only flag if impl patterns found.
            if impl_matches:
                problems.append(
                    f"{anchor['path']}: no stub markers and has "
                    f"implementation code"
                )
    if problems:
        return False, "; ".join(problems)
    return True, "All anchor files are minimal stubs"


def _check_anchors_match_shard_surfaces(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify anchor content relates to the declared surface purposes."""
    plan = json.loads(_SIMPLE_SEED_PLAN)
    problems = []
    # Map purpose keywords to expected content patterns
    surface_keywords = {
        "shared/session_provider.py": ["session", "async", "context"],
        "shared/idempotency.py": ["idempoten", "key", "check"],
    }
    for anchor in plan["anchors"]:
        full = codespace / anchor["path"]
        if not full.exists():
            continue
        content = _file_content(full).lower()
        expected = surface_keywords.get(anchor["path"], [])
        found = [kw for kw in expected if kw in content]
        if len(found) < 1 and expected:
            problems.append(
                f"{anchor['path']}: none of expected keywords "
                f"{expected} found in content"
            )
    if problems:
        return False, "; ".join(problems)
    return True, "Anchor content matches declared surface purposes"


def _check_seed_signal_written(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the completion signal was written with SEEDED state."""
    signal = _read_seed_signal(planspace)
    if signal is None:
        return False, "seed-signal.json not found or unreadable"
    state = signal.get("state")
    if state != "SEEDED":
        return False, f"Expected state=SEEDED, got {state}"
    anchors = signal.get("anchors_created", [])
    if not anchors:
        return False, "anchors_created is empty in seed signal"
    return True, f"seed-signal.json: state=SEEDED, {len(anchors)} anchors"


def _check_output_describes_files(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify agent output text mentions the files it creates."""
    lower = agent_output.lower()
    plan = json.loads(_SIMPLE_SEED_PLAN)
    mentioned = []
    for anchor in plan["anchors"]:
        # Check for the filename (with or without path)
        filename = Path(anchor["path"]).name
        if filename.lower() in lower or anchor["path"].lower() in lower:
            mentioned.append(anchor["path"])
    if not mentioned:
        return False, "Agent output does not mention any anchor file paths"
    if len(mentioned) < len(plan["anchors"]):
        missing = [
            a["path"] for a in plan["anchors"]
            if a["path"] not in mentioned
        ]
        return False, f"Agent output missing mention of: {missing}"
    return True, f"Agent output mentions all {len(mentioned)} anchor files"


# ---------------------------------------------------------------------------
# Checks: no-implementation scenario
# ---------------------------------------------------------------------------

def _check_complex_anchors_created(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify all complex anchor files exist in codespace."""
    plan = json.loads(_COMPLEX_SEED_PLAN)
    expected_paths = [a["path"] for a in plan["anchors"]]
    missing = []
    for rel_path in expected_paths:
        full = codespace / rel_path
        if not full.exists():
            missing.append(rel_path)
    if missing:
        return False, f"Missing anchor files: {missing}"
    return True, f"All {len(expected_paths)} complex anchor files created"


def _check_no_business_logic(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify anchors do NOT contain business logic / full implementations.

    Complex surfaces (event bus, auth gate, lock manager) are tempting
    to implement. The seeder must resist and produce stubs only.
    """
    plan = json.loads(_COMPLEX_SEED_PLAN)
    problems = []
    for anchor in plan["anchors"]:
        full = codespace / anchor["path"]
        if not full.exists():
            continue
        content = _file_content(full)
        if not content.strip():
            continue
        # Count lines of actual code (non-empty, non-comment, non-docstring)
        code_lines = []
        in_docstring = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if in_docstring:
                    in_docstring = False
                    continue
                # Single-line docstring
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    continue
                in_docstring = True
                continue
            if in_docstring:
                continue
            if stripped and not stripped.startswith("#"):
                code_lines.append(stripped)
        # Implementation pattern check
        impl_matches = _IMPLEMENTATION_PATTERNS.findall(content)
        if impl_matches:
            flat = [m for group in impl_matches for m in group if m]
            problems.append(
                f"{anchor['path']}: contains implementation patterns: "
                f"{flat[:5]}"
            )
        # Heuristic: more than 80 lines of code strongly suggests
        # implementation rather than stubs
        if len(code_lines) > 80:
            problems.append(
                f"{anchor['path']}: {len(code_lines)} code lines "
                f"(expected stub-level, <80)"
            )
    if problems:
        return False, "; ".join(problems)
    return True, "No business logic found in complex anchor files"


def _check_anchor_level_appropriate(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify anchors remain at interface/stub level.

    Each anchor should define types, abstract methods, protocols,
    or stubs -- not working implementations.
    """
    plan = json.loads(_COMPLEX_SEED_PLAN)
    problems = []
    # Keywords that indicate interface/stub level
    interface_markers = [
        "class", "def", "protocol", "abstract", "interface",
        "type", "dataclass", "namedtuple", "typeddict",
        "notimplementederror", "pass", "...",
    ]
    for anchor in plan["anchors"]:
        full = codespace / anchor["path"]
        if not full.exists():
            continue
        content = _file_content(full).lower()
        if not content.strip():
            problems.append(f"{anchor['path']}: empty file")
            continue
        found = [m for m in interface_markers if m in content]
        if not found:
            problems.append(
                f"{anchor['path']}: no interface/stub markers found "
                f"(expected class/def/protocol/abstract/pass/...)"
            )
    if problems:
        return False, "; ".join(problems)
    return True, "All anchors at interface/stub level"


def _check_complex_seed_signal(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify seed signal for the complex scenario."""
    signal = _read_seed_signal(planspace)
    if signal is None:
        return False, "seed-signal.json not found or unreadable"
    state = signal.get("state")
    if state != "SEEDED":
        return False, f"Expected state=SEEDED, got {state}"
    anchors = signal.get("anchors_created", [])
    plan = json.loads(_COMPLEX_SEED_PLAN)
    expected_count = len(plan["anchors"])
    if len(anchors) < expected_count:
        return False, (
            f"Expected {expected_count} anchors_created, "
            f"got {len(anchors)}"
        )
    return True, (
        f"seed-signal.json: state=SEEDED, "
        f"{len(anchors)} anchors (expected {expected_count})"
    )


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="substrate_seeder_anchor_contract",
        agent_file="substrate-seeder.md",
        model_policy_key="scan",
        setup=_setup_anchor_contract,
        checks=[
            Check(
                description="Output describes files to create",
                verify=_check_output_describes_files,
            ),
            Check(
                description="Anchor files from seed plan created in codespace",
                verify=_check_anchor_files_created,
            ),
            Check(
                description="Anchor files are minimal stubs, not implementations",
                verify=_check_anchors_are_stubs,
            ),
            Check(
                description="Anchor content matches shard declared surfaces",
                verify=_check_anchors_match_shard_surfaces,
            ),
            Check(
                description="Completion signal written with SEEDED state",
                verify=_check_seed_signal_written,
            ),
        ],
    ),
    Scenario(
        name="substrate_seeder_no_implementation",
        agent_file="substrate-seeder.md",
        model_policy_key="scan",
        setup=_setup_no_implementation,
        checks=[
            Check(
                description="All complex anchor files created in codespace",
                verify=_check_complex_anchors_created,
            ),
            Check(
                description="No business logic in anchor files",
                verify=_check_no_business_logic,
            ),
            Check(
                description="Anchors remain at interface/stub level",
                verify=_check_anchor_level_appropriate,
            ),
            Check(
                description="Completion signal written with SEEDED state",
                verify=_check_complex_seed_signal,
            ),
        ],
    ),
]
