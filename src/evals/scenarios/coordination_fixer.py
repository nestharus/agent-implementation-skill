"""Coordination fixer scenario eval.

Tests that the coordination-fixer agent identifies cross-concern
friction between sections sharing an interface and produces
meaningful output about the coordination problem.

Scenarios:
  coordination_fix_cross_concern: Two sections sharing an interface
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from evals.harness import Check, Scenario


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SECTION_03_SPEC = textwrap.dedent("""\
    # Section 03: User Profile Service

    ## Problem
    The user profile service manages CRUD operations for user profiles.
    It exposes a `UserProfile` data class consumed by multiple other
    services. The profile includes display name, avatar URL, preferences,
    and notification settings.

    ## Requirements
    - REQ-01: CRUD operations for user profiles
    - REQ-02: Profile validation (display name length, avatar URL format)
    - REQ-03: Preferences as a JSON blob with schema validation
    - REQ-04: Notification settings integration with notification service

    ## Related Files

    ### profiles/service.py
    Core profile CRUD logic.

    ### profiles/models.py
    UserProfile data model -- shared interface.

    ### profiles/validators.py
    Profile field validation.
""")

_SECTION_06_SPEC = textwrap.dedent("""\
    # Section 06: Activity Feed

    ## Problem
    The activity feed aggregates user actions and displays them as a
    timeline. Each feed entry references the acting user via their
    profile. The feed needs to show display names and avatars inline
    without N+1 queries.

    ## Requirements
    - REQ-01: Aggregate user actions into chronological feed
    - REQ-02: Batch-resolve user profiles for feed entries
    - REQ-03: Cache resolved profiles for feed rendering
    - REQ-04: Handle profile updates (invalidate cached entries)

    ## Related Files

    ### feed/aggregator.py
    Feed aggregation and timeline construction.

    ### feed/profile_resolver.py
    Batch profile resolution for feed entries -- consumes UserProfile.

    ### feed/cache.py
    Profile cache for feed rendering.
""")

_PROFILE_SERVICE_PY = textwrap.dedent("""\
    from .models import UserProfile
    from .validators import validate_profile

    class ProfileService:
        def __init__(self, db):
            self.db = db

        def get_profile(self, user_id: str) -> UserProfile:
            row = self.db.fetch("profiles", user_id)
            return UserProfile(
                user_id=row["user_id"],
                display_name=row["display_name"],
                avatar_url=row["avatar_url"],
                preferences=row.get("preferences", {}),
                notification_settings=row.get("notification_settings", {}),
            )

        def update_profile(self, user_id: str, updates: dict) -> UserProfile:
            validate_profile(updates)
            self.db.update("profiles", user_id, updates)
            return self.get_profile(user_id)
""")

_PROFILE_MODELS_PY = textwrap.dedent("""\
    from dataclasses import dataclass, field

    @dataclass
    class UserProfile:
        user_id: str
        display_name: str
        avatar_url: str = ""
        preferences: dict = field(default_factory=dict)
        notification_settings: dict = field(default_factory=dict)
""")

_PROFILE_RESOLVER_PY = textwrap.dedent("""\
    from profiles.models import UserProfile

    class ProfileResolver:
        \"\"\"Batch-resolves user profiles for feed entries.

        Currently imports UserProfile directly. If the profile model
        changes (e.g., adding required fields), this breaks silently
        because there is no interface contract -- just a direct import.
        \"\"\"

        def __init__(self, profile_service):
            self.profile_service = profile_service
            self._cache = {}

        def resolve_batch(self, user_ids: list[str]) -> dict[str, UserProfile]:
            result = {}
            uncached = [uid for uid in user_ids if uid not in self._cache]
            for uid in uncached:
                profile = self.profile_service.get_profile(uid)
                self._cache[uid] = profile
            for uid in user_ids:
                result[uid] = self._cache[uid]
            return result

        def invalidate(self, user_id: str) -> None:
            self._cache.pop(user_id, None)
""")

_CODEMAP = textwrap.dedent("""\
    # Project Codemap

    ## profiles/
    - `profiles/service.py` - Profile CRUD operations
    - `profiles/models.py` - UserProfile data model (shared)
    - `profiles/validators.py` - Profile validation rules

    ## feed/
    - `feed/aggregator.py` - Feed timeline construction
    - `feed/profile_resolver.py` - Batch profile resolution (consumes UserProfile)
    - `feed/cache.py` - Profile cache for feed rendering
""")


# ---------------------------------------------------------------------------
# Setup function
# ---------------------------------------------------------------------------

def _setup_cross_concern(planspace: Path, codespace: Path) -> Path:
    """Create fixtures for cross-concern coordination scenario."""
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    signals = artifacts / "signals"
    coordination = artifacts / "coordination"
    sections.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    coordination.mkdir(parents=True, exist_ok=True)

    # Section specs
    sec03_path = sections / "section-03.md"
    sec03_path.write_text(_SECTION_03_SPEC, encoding="utf-8")

    sec06_path = sections / "section-06.md"
    sec06_path.write_text(_SECTION_06_SPEC, encoding="utf-8")

    # Proposal excerpts
    (sections / "section-03-proposal-excerpt.md").write_text(textwrap.dedent("""\
        # Proposal Excerpt: Section 03

        Add a `last_active` timestamp field to UserProfile. Update
        ProfileService.update_profile() to set last_active on every
        mutation. Add schema validation for preferences JSON blob.
    """), encoding="utf-8")

    (sections / "section-06-proposal-excerpt.md").write_text(textwrap.dedent("""\
        # Proposal Excerpt: Section 06

        ProfileResolver currently imports UserProfile directly with no
        interface contract. Add a lightweight profile contract (protocol
        class or typed dict) that both sections agree on. Update cache
        invalidation to trigger on profile update events.
    """), encoding="utf-8")

    # Alignment excerpts
    (sections / "section-03-alignment-excerpt.md").write_text(textwrap.dedent("""\
        # Alignment Excerpt: Section 03

        The last_active field addition is straightforward but needs
        coordination with section 06 -- the feed's ProfileResolver
        imports UserProfile directly. Adding a required field without
        a default would break the resolver's cached entries.
    """), encoding="utf-8")

    (sections / "section-06-alignment-excerpt.md").write_text(textwrap.dedent("""\
        # Alignment Excerpt: Section 06

        The direct import of UserProfile from profiles.models is a
        cross-section coupling risk. If section 03 adds fields, section
        06's cache becomes stale. Need an agreed interface contract.
    """), encoding="utf-8")

    # Codemap
    codemap_path = artifacts / "codemap.md"
    codemap_path.write_text(_CODEMAP, encoding="utf-8")

    # Codespace with both modules
    profiles_dir = codespace / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "__init__.py").write_text("", encoding="utf-8")
    (profiles_dir / "service.py").write_text(
        _PROFILE_SERVICE_PY, encoding="utf-8")
    (profiles_dir / "models.py").write_text(
        _PROFILE_MODELS_PY, encoding="utf-8")
    (profiles_dir / "validators.py").write_text(
        "def validate_profile(updates: dict) -> None:\n    pass\n",
        encoding="utf-8",
    )

    feed_dir = codespace / "feed"
    feed_dir.mkdir(parents=True, exist_ok=True)
    (feed_dir / "__init__.py").write_text("", encoding="utf-8")
    (feed_dir / "profile_resolver.py").write_text(
        _PROFILE_RESOLVER_PY, encoding="utf-8")
    (feed_dir / "aggregator.py").write_text(
        "class FeedAggregator:\n    pass\n", encoding="utf-8")
    (feed_dir / "cache.py").write_text(
        "class ProfileCache:\n    pass\n", encoding="utf-8")

    # Build the coordination fix prompt (mirrors execution.py structure)
    problems = [
        {
            "section": "03",
            "type": "INTERFACE_DRIFT",
            "description": (
                "Section 03 is adding a `last_active` timestamp field to "
                "UserProfile. Section 06's ProfileResolver imports "
                "UserProfile directly and caches profile instances. "
                "Adding a new field without default value will break "
                "cached entries in section 06. The two sections need "
                "an agreed interface contract or the new field needs "
                "a default value."
            ),
            "files": [
                "profiles/models.py",
                "profiles/service.py",
                "feed/profile_resolver.py",
            ],
        },
        {
            "section": "06",
            "type": "CACHE_INVALIDATION",
            "description": (
                "Section 06's profile cache does not invalidate when "
                "section 03 updates a profile. The `update_profile()` "
                "method in section 03 does not emit any event that "
                "section 06 can subscribe to. Without a cache "
                "invalidation mechanism, feed entries will show stale "
                "profile data after updates."
            ),
            "files": [
                "profiles/service.py",
                "feed/profile_resolver.py",
                "feed/cache.py",
            ],
        },
    ]

    modified_report = coordination / "fix-1-modified.txt"

    # Problem descriptions for prompt
    problem_descriptions = []
    for i, p in enumerate(problems):
        desc = (
            f"### Problem {i + 1} (Section {p['section']}, "
            f"type: {p['type']})\n{p['description']}"
        )
        problem_descriptions.append(desc)
    problems_text = "\n\n".join(problem_descriptions)

    all_files = sorted({
        f for p in problems for f in p.get("files", [])
    })
    file_list = "\n".join(f"- `{codespace / f}`" for f in all_files)

    prompt_path = coordination / "fix-1-prompt.md"
    prompt_path.write_text(textwrap.dedent(f"""\
        # Task: Coordinated Fix for Problem Group 1

        ## Problems to Fix

        {problems_text}

        ## Affected Files
        {file_list}

        ## Section Context
        - Section 03 specification: `{sec03_path}`
          - Proposal excerpt: `{sections / "section-03-proposal-excerpt.md"}`
        - Section 06 specification: `{sec06_path}`
          - Proposal excerpt: `{sections / "section-06-proposal-excerpt.md"}`

        - Section 03 alignment excerpt: `{sections / "section-03-alignment-excerpt.md"}`
        - Section 06 alignment excerpt: `{sections / "section-06-alignment-excerpt.md"}`

        ## Project Understanding
        - Codemap: `{codemap_path}`

        ## Instructions

        Fix ALL the problems listed above in a COORDINATED way. These problems
        are related -- they share files and/or have a common root cause. Fixing
        them together avoids the cascade where fixing one problem in isolation
        creates or re-triggers another.

        ### Strategy

        1. **Explore first.** Before making changes, understand the full picture.
        2. **Plan holistically.** Consider how all the problems interact.
        3. **Implement.** Make the changes.
        4. **Verify.** After implementation, verify the fixes address all problems.

        ### Report Modified Files

        After implementation, write a list of ALL files you modified to:
        `{modified_report}`

        One file path per line (relative to codespace root `{codespace}`).
    """), encoding="utf-8")

    return prompt_path


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def _check_output_mentions_cross_concern(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output identifies the cross-concern friction."""
    lower = agent_output.lower()
    # The agent should recognize the UserProfile interface coupling
    indicators = [
        "userprofile",
        "profile",
        "interface",
        "cross-section",
        "cross section",
        "shared",
        "coupling",
        "contract",
    ]
    found = [ind for ind in indicators if ind in lower]
    if len(found) >= 2:
        return True, f"Output mentions cross-concern indicators: {found}"
    return False, (
        f"Output has too few cross-concern indicators "
        f"(found {found}, need >=2)"
    )


def _check_output_addresses_both_problems(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify output addresses both the interface drift and cache issue."""
    lower = agent_output.lower()
    has_interface = any(
        term in lower
        for term in ["last_active", "field", "model", "default"]
    )
    has_cache = any(
        term in lower
        for term in ["cache", "invalidat", "stale", "event"]
    )
    if has_interface and has_cache:
        return True, "Output addresses both interface drift and cache issues"
    missing = []
    if not has_interface:
        missing.append("interface drift (last_active/field/model)")
    if not has_cache:
        missing.append("cache invalidation")
    return False, f"Output missing coverage of: {missing}"


def _check_modified_files_report(
    planspace: Path, codespace: Path, agent_output: str,
) -> tuple[bool, str]:
    """Verify modified files report was written (if agent produced changes)."""
    report_path = (planspace / "artifacts" / "coordination"
                   / "fix-1-modified.txt")
    if report_path.exists():
        content = report_path.read_text(encoding="utf-8").strip()
        if content:
            files = [l.strip() for l in content.splitlines() if l.strip()]
            return True, f"Modified files report has {len(files)} entries"
        return False, "Modified files report exists but is empty"
    # The agent might not have made changes if it determined it couldn't
    # modify files (codespace may not have been writable). That is
    # acceptable -- the check is soft.
    return True, (
        "Modified files report not written (acceptable if agent "
        "determined changes not possible in eval context)"
    )


# ---------------------------------------------------------------------------
# Exported scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    Scenario(
        name="coordination_fix_cross_concern",
        agent_file="coordination-fixer.md",
        model_policy_key="coordination_fix",
        setup=_setup_cross_concern,
        checks=[
            Check(
                description="Output identifies cross-concern friction",
                verify=_check_output_mentions_cross_concern,
            ),
            Check(
                description="Output addresses both interface drift and cache issues",
                verify=_check_output_addresses_both_problems,
            ),
            Check(
                description="Modified files report written (soft check)",
                verify=_check_modified_files_report,
            ),
        ],
    ),
]
