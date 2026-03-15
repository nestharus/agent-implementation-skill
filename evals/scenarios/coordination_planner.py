"""Coordination planner scenario evals.

Tests that the coordination-planner agent correctly groups problems
based on cross-section dependencies and keeps independent sections
apart.

Scenarios:
  coordination_planner_cross_deps: Sections with real cross-dependencies
  coordination_planner_independent: Sections with no cross-dependencies
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures: cross-dependency scenario
# ---------------------------------------------------------------------------

_SECTION_21_SPEC = textwrap.dedent("""\
    # Section 21: Auth Token Service

    ## Problem
    The auth token service issues and validates JWT tokens for all
    API consumers.  It owns the `TokenPayload` schema that downstream
    services decode to extract user identity and permission claims.

    ## Requirements
    - REQ-01: Issue short-lived JWTs with configurable TTL
    - REQ-02: Validate token signature and expiration
    - REQ-03: Embed permission claims in token payload
    - REQ-04: Rotate signing keys without downtime

    ## Related Files

    ### auth/token_service.py
    Token issuance and validation logic.

    ### auth/schemas.py
    TokenPayload schema -- shared interface consumed by other services.

    ### auth/key_rotation.py
    Signing key rotation mechanism.
""")

_SECTION_22_SPEC = textwrap.dedent("""\
    # Section 22: API Gateway Middleware

    ## Problem
    The API gateway middleware intercepts every inbound request,
    validates the bearer token by decoding the JWT, and attaches
    the resolved identity to the request context.  It imports
    `TokenPayload` directly from the auth module.

    ## Requirements
    - REQ-01: Extract bearer token from Authorization header
    - REQ-02: Decode and validate JWT using auth module's public key
    - REQ-03: Attach decoded TokenPayload to request context
    - REQ-04: Reject expired or malformed tokens with 401

    ## Related Files

    ### gateway/middleware.py
    Request interception and token validation -- imports TokenPayload.

    ### gateway/context.py
    Request context that carries the decoded identity.
""")

_SECTION_23_SPEC = textwrap.dedent("""\
    # Section 23: Permissions Evaluator

    ## Problem
    The permissions evaluator checks whether the current user is
    authorized for a requested action.  It reads the permission
    claims embedded in `TokenPayload` and evaluates them against
    the resource policy.

    ## Requirements
    - REQ-01: Evaluate permission claims from TokenPayload
    - REQ-02: Support role-based and attribute-based policies
    - REQ-03: Cache policy evaluation results per-request
    - REQ-04: Deny by default when claims are missing

    ## Related Files

    ### perms/evaluator.py
    Policy evaluation logic -- reads claims from TokenPayload.

    ### perms/policies.py
    Policy definitions and matching rules.
""")

_CROSS_DEP_PROBLEMS = [
    {
        "section": "21",
        "type": "misaligned",
        "description": (
            "Section 21 is adding a 'scope' claim to TokenPayload and "
            "changing the permission claims from a flat list to a nested "
            "structure.  This is a breaking schema change that affects "
            "every downstream consumer of TokenPayload."
        ),
        "files": [
            "auth/schemas.py",
            "auth/token_service.py",
        ],
    },
    {
        "section": "22",
        "type": "unaddressed_note",
        "description": (
            "Section 22's gateway middleware decodes TokenPayload and "
            "accesses `.permissions` as a flat list.  Section 21 is "
            "restructuring permissions into a nested format.  The "
            "middleware will break at runtime because it indexes into "
            "the permissions list directly."
        ),
        "files": [
            "gateway/middleware.py",
            "gateway/context.py",
            "auth/schemas.py",
        ],
    },
    {
        "section": "23",
        "type": "unaddressed_note",
        "description": (
            "Section 23's permissions evaluator reads `.permissions` "
            "from TokenPayload and matches them against policy rules. "
            "The flat-to-nested migration in section 21 will break the "
            "evaluator's claim matching logic."
        ),
        "files": [
            "perms/evaluator.py",
            "auth/schemas.py",
        ],
    },
]

_CROSS_DEP_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## auth/
    - `auth/token_service.py` - JWT issuance and validation
    - `auth/schemas.py` - TokenPayload schema (shared across services)
    - `auth/key_rotation.py` - Signing key rotation

    ## gateway/
    - `gateway/middleware.py` - Request interception, token validation
    - `gateway/context.py` - Request context carrying decoded identity

    ## perms/
    - `perms/evaluator.py` - Permission claim evaluation
    - `perms/policies.py` - Policy definitions and matching rules
""")


# ---------------------------------------------------------------------------
# Fixtures: independent scenario
# ---------------------------------------------------------------------------

_SECTION_31_SPEC = textwrap.dedent("""\
    # Section 31: PDF Export Service

    ## Problem
    The PDF export service renders reports into downloadable PDFs.
    It uses a template engine and a rendering library.  Completely
    self-contained; no shared interfaces with other sections.

    ## Requirements
    - REQ-01: Render report data into PDF using templates
    - REQ-02: Support page headers, footers, and page numbers
    - REQ-03: Stream large PDFs without buffering in memory

    ## Related Files

    ### export/pdf_renderer.py
    PDF rendering logic using template engine.

    ### export/templates.py
    PDF template definitions.
""")

_SECTION_32_SPEC = textwrap.dedent("""\
    # Section 32: Background Job Scheduler

    ## Problem
    The background job scheduler queues and executes periodic tasks
    (e.g., cleanup, digest emails).  It has its own task table and
    execution engine.  No shared files or interfaces with PDF export.

    ## Requirements
    - REQ-01: Register periodic jobs with cron-style schedules
    - REQ-02: Execute jobs with retry and dead-letter handling
    - REQ-03: Track job execution history

    ## Related Files

    ### scheduler/engine.py
    Job execution engine with retry logic.

    ### scheduler/registry.py
    Job registration and schedule management.
""")

_INDEPENDENT_PROBLEMS = [
    {
        "section": "31",
        "type": "misaligned",
        "description": (
            "The PDF renderer does not handle page breaks correctly for "
            "tables that span multiple pages.  Table rows are clipped at "
            "the page boundary instead of reflowing to the next page."
        ),
        "files": [
            "export/pdf_renderer.py",
            "export/templates.py",
        ],
    },
    {
        "section": "32",
        "type": "misaligned",
        "description": (
            "The job scheduler's retry logic uses a fixed delay instead "
            "of exponential backoff.  Jobs that fail due to transient "
            "errors overwhelm the target service with rapid retries."
        ),
        "files": [
            "scheduler/engine.py",
        ],
    },
]

_INDEPENDENT_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## export/
    - `export/pdf_renderer.py` - PDF rendering logic
    - `export/templates.py` - PDF template definitions

    ## scheduler/
    - `scheduler/engine.py` - Job execution engine with retry
    - `scheduler/registry.py` - Job registration and schedules
""")


# ---------------------------------------------------------------------------
# Setup: cross-dependencies
# ---------------------------------------------------------------------------

def _setup_cross_deps(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for multi-section cross-dependency scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    coordination = artifacts / "coordination"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    coordination.mkdir(parents=True, exist_ok=True)

    # Section specs
    (sections / "section-21.md").write_text(_SECTION_21_SPEC, encoding="utf-8")
    (sections / "section-22.md").write_text(_SECTION_22_SPEC, encoding="utf-8")
    (sections / "section-23.md").write_text(_SECTION_23_SPEC, encoding="utf-8")

    # Consequence notes showing cross-section impact
    (coordination / "consequence-note-sec21-to-sec22.md").write_text(
        textwrap.dedent("""\
            # Consequence Note: Section 21 -> Section 22

            Section 21 is restructuring TokenPayload.permissions from a flat
            list (`list[str]`) to a nested structure (`dict[str, list[str]]`).
            Section 22's gateway middleware indexes into `.permissions` as a
            list at gateway/middleware.py:47.  This will cause a TypeError
            at runtime after section 21's change lands.
        """),
        encoding="utf-8",
    )
    (coordination / "consequence-note-sec21-to-sec23.md").write_text(
        textwrap.dedent("""\
            # Consequence Note: Section 21 -> Section 23

            Section 21 is restructuring TokenPayload.permissions from a flat
            list to a nested dict.  Section 23's permissions evaluator iterates
            over `.permissions` expecting strings.  After the schema change,
            it will iterate over dict keys instead of permission strings,
            breaking all policy evaluations.
        """),
        encoding="utf-8",
    )

    # Scope deltas referencing shared file
    (coordination / "scope-delta-sec21.json").write_text(
        json.dumps({
            "section": "21",
            "changed_files": ["auth/schemas.py", "auth/token_service.py"],
            "impact": "Breaking change to TokenPayload.permissions structure",
        }),
        encoding="utf-8",
    )

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CROSS_DEP_CODEMAP, encoding="utf-8")

    # Codespace with stub modules
    for subdir in ("auth", "gateway", "perms"):
        d = codespace / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
    (codespace / "auth" / "schemas.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass, field

        @dataclass
        class TokenPayload:
            user_id: str
            permissions: list[str] = field(default_factory=list)
            exp: int = 0
    """), encoding="utf-8")
    (codespace / "auth" / "token_service.py").write_text(textwrap.dedent("""\
        from .schemas import TokenPayload

        class TokenService:
            def issue(self, user_id: str, permissions: list[str]) -> str:
                payload = TokenPayload(user_id=user_id, permissions=permissions)
                return self._encode(payload)

            def _encode(self, payload: TokenPayload) -> str:
                return "jwt-stub"
    """), encoding="utf-8")
    (codespace / "gateway" / "middleware.py").write_text(textwrap.dedent("""\
        from auth.schemas import TokenPayload

        class AuthMiddleware:
            def process_request(self, token: str) -> dict:
                payload = self._decode(token)
                # Access permissions as a flat list
                has_admin = "admin" in payload.permissions
                return {"user_id": payload.user_id, "is_admin": has_admin}

            def _decode(self, token: str) -> TokenPayload:
                return TokenPayload(user_id="stub", permissions=[])
    """), encoding="utf-8")
    (codespace / "perms" / "evaluator.py").write_text(textwrap.dedent("""\
        from auth.schemas import TokenPayload

        class PermissionsEvaluator:
            def check(self, payload: TokenPayload, action: str) -> bool:
                # Iterates permissions as list of strings
                return action in payload.permissions
    """), encoding="utf-8")

    # Write problems.json (what the planner agent reads)
    problems_path = coordination / "problems.json"
    problems_path.write_text(
        json.dumps(_CROSS_DEP_PROBLEMS, indent=2),
        encoding="utf-8",
    )

    # Build the planner prompt
    prompt_path = coordination / "coordination-plan-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Plan Coordination Strategy

        ## Outstanding Problems

        Read the problems list from: `{problems_path}`

        ## Project Skeleton

        Read the codemap for project structure context: `{codemap_path}`

        ## Consequence Notes

        - `{coordination / "consequence-note-sec21-to-sec22.md"}`
        - `{coordination / "consequence-note-sec21-to-sec23.md"}`

        ## Scope Deltas

        - `{coordination / "scope-delta-sec21.json"}`

        ## Instructions

        You are the coordination planner. Read the problems above (and the
        codemap if provided) and produce a JSON coordination plan. Think
        strategically about problem relationships -- don't just match files.
        Understand whether problems share root causes, whether fixing one
        affects another, and what order minimizes rework.

        Reply with a JSON block:

        ```json
        {{
          "groups": [
            {{
              "problems": [0, 1],
              "reason": "Both problems stem from incomplete event model",
              "strategy": "sequential"
            }}
          ],
          "batches": [[0]],
          "notes": "Optional observations about cross-group dependencies."
        }}
        ```

        Each group's `problems` array contains indices into the problems list above.
        Every problem index (0 through 2) must appear in exactly one group.

        Strategy values:
        - `sequential`: problems within this group must be fixed in order
        - `parallel`: problems within this group can be fixed concurrently

        The `batches` array defines execution ordering of GROUPS. Each batch is a
        list of group indices to run concurrently (subject to file-safety checks).
        Batches execute sequentially -- batch 0 completes before batch 1 starts.
    """), encoding="utf-8")

    return prompt_path


# ---------------------------------------------------------------------------
# Setup: independent sections
# ---------------------------------------------------------------------------

def _setup_independent(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for independent-sections scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    coordination = artifacts / "coordination"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    coordination.mkdir(parents=True, exist_ok=True)

    # Section specs
    (sections / "section-31.md").write_text(_SECTION_31_SPEC, encoding="utf-8")
    (sections / "section-32.md").write_text(_SECTION_32_SPEC, encoding="utf-8")

    # No consequence notes -- sections are independent

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_INDEPENDENT_CODEMAP, encoding="utf-8")

    # Codespace with stub modules
    for subdir in ("export", "scheduler"):
        d = codespace / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
    (codespace / "export" / "pdf_renderer.py").write_text(textwrap.dedent("""\
        class PDFRenderer:
            def render(self, data: dict) -> bytes:
                return b"%PDF-stub"
    """), encoding="utf-8")
    (codespace / "export" / "templates.py").write_text(textwrap.dedent("""\
        TEMPLATES = {"default": "<html>{{content}}</html>"}
    """), encoding="utf-8")
    (codespace / "scheduler" / "engine.py").write_text(textwrap.dedent("""\
        import time

        class JobEngine:
            def execute_with_retry(self, job, max_retries=3):
                for attempt in range(max_retries):
                    try:
                        return job()
                    except Exception:
                        time.sleep(1)  # Fixed delay, no backoff
                raise RuntimeError("Job failed after retries")
    """), encoding="utf-8")

    # Write problems.json
    problems_path = coordination / "problems.json"
    problems_path.write_text(
        json.dumps(_INDEPENDENT_PROBLEMS, indent=2),
        encoding="utf-8",
    )

    # Build the planner prompt
    prompt_path = coordination / "coordination-plan-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Plan Coordination Strategy

        ## Outstanding Problems

        Read the problems list from: `{problems_path}`

        ## Project Skeleton

        Read the codemap for project structure context: `{codemap_path}`

        ## Instructions

        You are the coordination planner. Read the problems above (and the
        codemap if provided) and produce a JSON coordination plan. Think
        strategically about problem relationships -- don't just match files.
        Understand whether problems share root causes, whether fixing one
        affects another, and what order minimizes rework.

        Reply with a JSON block:

        ```json
        {{
          "groups": [
            {{
              "problems": [0],
              "reason": "Independent issue",
              "strategy": "parallel"
            }}
          ],
          "batches": [[0]],
          "notes": "Optional observations about cross-group dependencies."
        }}
        ```

        Each group's `problems` array contains indices into the problems list above.
        Every problem index (0 through 1) must appear in exactly one group.

        Strategy values:
        - `sequential`: problems within this group must be fixed in order
        - `parallel`: problems within this group can be fixed concurrently

        The `batches` array defines execution ordering of GROUPS. Each batch is a
        list of group indices to run concurrently (subject to file-safety checks).
        Batches execute sequentially -- batch 0 completes before batch 1 starts.
    """), encoding="utf-8")

    return prompt_path


# ---------------------------------------------------------------------------
# Helpers: parse plan JSON from agent output
# ---------------------------------------------------------------------------

def _extract_plan(agent_output: str) -> dict | None:
    """Extract the JSON coordination plan from agent output."""
    # Try fenced block first
    in_fence = False
    json_lines: list[str] = []
    for line in agent_output.splitlines():
        if line.strip().startswith("```json"):
            in_fence = True
            json_lines = []
            continue
        if line.strip().startswith("```") and in_fence:
            in_fence = False
            text = "\n".join(json_lines)
            if '"groups"' in text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            continue
        if in_fence:
            json_lines.append(line)

    # Fallback: find outermost braces containing "groups"
    start = agent_output.find("{")
    end = agent_output.rfind("}")
    if start >= 0 and end > start:
        candidate = agent_output[start:end + 1]
        if '"groups"' in candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Checks: cross-dependency scenario
# ---------------------------------------------------------------------------

def _check_cross_output_has_plan(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a parseable JSON coordination plan."""
    plan = _extract_plan(agent_output)
    if plan is None:
        return False, "No valid JSON coordination plan found in output"
    groups = plan.get("groups")
    if not isinstance(groups, list) or len(groups) == 0:
        return False, f"Plan has no groups (got {type(groups).__name__})"
    return True, f"Plan contains {len(groups)} group(s)"


def _check_cross_plan_references_sections(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the plan references the affected sections (21, 22, 23)."""
    lower = agent_output.lower()
    # The planner should mention the sections by number or by name
    section_indicators = {
        "21": any(t in lower for t in ["section 21", "section-21", "sec21", "sec-21", '"21"']),
        "22": any(t in lower for t in ["section 22", "section-22", "sec22", "sec-22", '"22"']),
        "23": any(t in lower for t in ["section 23", "section-23", "sec23", "sec-23", '"23"']),
    }
    found = [s for s, hit in section_indicators.items() if hit]
    if len(found) >= 2:
        return True, f"Plan references sections: {found}"
    # Also accept if the plan groups all 3 problems together (implicit)
    plan = _extract_plan(agent_output)
    if plan:
        all_indices: set[int] = set()
        for g in plan.get("groups", []):
            for idx in g.get("problems", []):
                all_indices.add(idx)
        if all_indices == {0, 1, 2}:
            return True, "Plan covers all 3 problem indices (implicit section coverage)"
    return False, f"Plan only references sections: {found} (need >= 2)"


def _check_cross_plan_groups_related(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the plan groups cross-dependent problems together.

    Problems 0, 1, 2 all share auth/schemas.py (the TokenPayload
    schema change). A good plan puts at least 2 of them in the same
    group or marks them as sequential dependencies.
    """
    plan = _extract_plan(agent_output)
    if plan is None:
        return False, "No valid coordination plan to inspect"

    groups = plan.get("groups", [])

    # Check if any single group contains at least 2 of the 3 problems
    for group in groups:
        indices = set(group.get("problems", []))
        overlap = indices & {0, 1, 2}
        if len(overlap) >= 2:
            return True, (
                f"Group contains related problems {sorted(overlap)} "
                f"(reason: {group.get('reason', 'n/a')!r})"
            )

    # Alternatively, if batches are sequential it also shows the planner
    # recognized the dependency (group 0 before group 1, etc.)
    batches = plan.get("batches")
    if isinstance(batches, list) and len(batches) >= 2:
        return True, (
            f"Plan uses {len(batches)} sequential batches, showing "
            f"dependency awareness"
        )

    return False, (
        "No group contains >= 2 of the 3 related problems, and no "
        "sequential batch ordering detected"
    )


def _check_cross_plan_dependency_order(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the plan prioritizes the root-cause section (21) first.

    Section 21 owns the schema change. The planner should either put
    problem 0 first in a sequential group, or batch the group containing
    problem 0 before groups containing problems 1/2.
    """
    plan = _extract_plan(agent_output)
    if plan is None:
        return False, "No valid coordination plan to inspect"

    groups = plan.get("groups", [])

    # Find which group contains problem 0 (the root-cause section 21)
    root_group_idx = None
    for i, group in enumerate(groups):
        if 0 in group.get("problems", []):
            root_group_idx = i
            # If problem 0 is first in a sequential group, that works
            problems_list = group.get("problems", [])
            strategy = group.get("strategy", "")
            if strategy == "sequential" and problems_list and problems_list[0] == 0:
                return True, (
                    "Problem 0 (root-cause section 21) is first in "
                    "sequential group"
                )
            # If the root-cause group includes all problems, any strategy works
            if set(problems_list) == {0, 1, 2}:
                return True, (
                    "All problems in one group -- root cause addressed together"
                )
            break

    # Check batch ordering: root group should be in an early batch
    batches = plan.get("batches")
    if isinstance(batches, list) and len(batches) >= 1 and root_group_idx is not None:
        if root_group_idx in batches[0]:
            return True, (
                f"Root-cause group {root_group_idx} is in first batch"
            )

    # Soft pass: if there is only one group containing problem 0, ordering
    # is implicit
    if len(groups) == 1 and root_group_idx == 0:
        return True, "Single group contains all problems (ordering is implicit)"

    return False, (
        f"Root-cause problem 0 not prioritized "
        f"(root_group_idx={root_group_idx}, batches={batches})"
    )


# ---------------------------------------------------------------------------
# Checks: independent scenario
# ---------------------------------------------------------------------------

def _check_indep_output_has_plan(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output contains a parseable JSON coordination plan."""
    plan = _extract_plan(agent_output)
    if plan is None:
        return False, "No valid JSON coordination plan found in output"
    groups = plan.get("groups")
    if not isinstance(groups, list) or len(groups) == 0:
        return False, f"Plan has no groups (got {type(groups).__name__})"
    return True, f"Plan contains {len(groups)} group(s)"


def _check_indep_separate_groups(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the plan keeps independent problems in separate groups.

    Problems 0 and 1 have disjoint file sets and no shared concerns.
    A good plan puts them in separate groups or, if in one group,
    marks the strategy as parallel.
    """
    plan = _extract_plan(agent_output)
    if plan is None:
        return False, "No valid coordination plan to inspect"

    groups = plan.get("groups", [])

    # Best case: 2 separate groups, one per problem
    if len(groups) == 2:
        g0_problems = set(groups[0].get("problems", []))
        g1_problems = set(groups[1].get("problems", []))
        if g0_problems == {0} and g1_problems == {1}:
            return True, "Problems correctly separated into 2 independent groups"
        if g0_problems == {1} and g1_problems == {0}:
            return True, "Problems correctly separated into 2 independent groups"

    # Acceptable: single group with parallel strategy (acknowledging no deps)
    if len(groups) == 1:
        strategy = groups[0].get("strategy", "")
        if strategy == "parallel":
            return True, (
                "Single group with parallel strategy (acceptable -- "
                "acknowledges independence)"
            )
        return False, (
            f"Single group with strategy={strategy!r} -- sequential implies "
            f"a dependency that does not exist"
        )

    # More than 2 groups for 2 problems is odd but not wrong
    all_indices: set[int] = set()
    for g in groups:
        all_indices.update(g.get("problems", []))
    if all_indices == {0, 1}:
        return True, f"All problem indices covered across {len(groups)} groups"
    return False, f"Unexpected grouping: {[g.get('problems') for g in groups]}"


def _check_indep_no_fake_dependencies(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the plan does not invent fake cross-dependencies.

    The plan should NOT claim shared files, shared root causes, or
    sequential ordering between the two problems.
    """
    plan = _extract_plan(agent_output)
    if plan is None:
        return False, "No valid coordination plan to inspect"

    groups = plan.get("groups", [])

    # Check that no group with both problems uses sequential strategy
    for group in groups:
        indices = set(group.get("problems", []))
        if indices == {0, 1} and group.get("strategy") == "sequential":
            reason = group.get("reason", "")
            return False, (
                f"Plan incorrectly uses sequential strategy for both "
                f"independent problems (reason: {reason!r})"
            )

    # Check that bridge is not marked as needed between these sections
    for group in groups:
        bridge = group.get("bridge", {})
        if isinstance(bridge, dict) and bridge.get("needed"):
            indices = set(group.get("problems", []))
            if indices == {0, 1}:
                return False, (
                    "Plan incorrectly requests a bridge agent for "
                    "independent problems"
                )

    return True, "Plan does not fabricate dependencies between independent problems"


def _check_indep_acknowledges_independence(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify the plan or notes acknowledge the sections are independent."""
    lower = agent_output.lower()
    independence_indicators = [
        "independent",
        "no overlap",
        "no shared",
        "disjoint",
        "unrelated",
        "no cross",
        "no dependency",
        "no dependencies",
        "parallel",
        "concurrently",
        "separately",
        "no interaction",
        "no common",
        "isolated",
    ]
    found = [ind for ind in independence_indicators if ind in lower]
    if found:
        return True, f"Output acknowledges independence: {found}"

    # Also accept if the plan has parallel strategy or single-batch with both
    plan = _extract_plan(agent_output)
    if plan:
        batches = plan.get("batches", [])
        if isinstance(batches, list) and len(batches) == 1:
            return True, "Single batch implies both groups can run concurrently"
        for group in plan.get("groups", []):
            if group.get("strategy") == "parallel":
                return True, "Parallel strategy implicitly acknowledges independence"

    return False, f"No independence indicators found (checked: {independence_indicators})"


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="coordination_planner_cross_deps",
        agent_file="coordination-planner.md",
        model_policy_key="coordination_plan",
        setup=_setup_cross_deps,
        checks=[
            Check(
                description="Output contains a valid JSON coordination plan",
                verify=_check_cross_output_has_plan,
            ),
            Check(
                description="Plan references affected sections",
                verify=_check_cross_plan_references_sections,
            ),
            Check(
                description="Plan groups cross-dependent problems together",
                verify=_check_cross_plan_groups_related,
            ),
            Check(
                description="Plan prioritizes root-cause section in dependency order",
                verify=_check_cross_plan_dependency_order,
            ),
        ],
    ),
    Scenario(
        name="coordination_planner_independent",
        agent_file="coordination-planner.md",
        model_policy_key="coordination_plan",
        setup=_setup_independent,
        checks=[
            Check(
                description="Output contains a valid JSON coordination plan",
                verify=_check_indep_output_has_plan,
            ),
            Check(
                description="Plan keeps independent problems in separate groups or marks parallel",
                verify=_check_indep_separate_groups,
            ),
            Check(
                description="Plan does not fabricate dependencies between independent problems",
                verify=_check_indep_no_fake_dependencies,
            ),
            Check(
                description="Plan acknowledges sections are independent",
                verify=_check_indep_acknowledges_independence,
            ),
        ],
    ),
]
