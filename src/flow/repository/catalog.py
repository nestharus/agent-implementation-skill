"""Flow catalog — named chain package resolution.

Maps named package references (chain_ref values) to concrete TaskSpec
sequences. This lets agents request high-level workflows like
"proposal_alignment_package" without knowing the individual steps.

Also provides ``build_coordination_branches()`` to convert coordination
fix groups into BranchSpec lists suitable for ``submit_fanout()``.
"""

from __future__ import annotations

from pathlib import Path

from flow.types.schema import BranchSpec, TaskSpec
from orchestrator.path_registry import PathRegistry


# ---------------------------------------------------------------------------
# Package registry
# ---------------------------------------------------------------------------

# Each entry maps a package name to a callable that takes (args, origin_refs)
# and returns a list of TaskSpec.  The args dict allows callers to
# parameterize packages (e.g. section, prompt paths).

def _proposal_alignment_package(
    args: dict, origin_refs: list[str]
) -> list[TaskSpec]:
    """proposal.integration → staleness.alignment_check."""
    return [
        TaskSpec(
            task_type="proposal.integration",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
        TaskSpec(
            task_type="staleness.alignment_check",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("alignment_payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
    ]


def _implementation_alignment_package(
    args: dict, origin_refs: list[str]
) -> list[TaskSpec]:
    """implementation.strategic → staleness.alignment_check."""
    return [
        TaskSpec(
            task_type="implementation.strategic",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
        TaskSpec(
            task_type="staleness.alignment_check",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("alignment_payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
    ]


def _coordination_fix_package(
    args: dict, origin_refs: list[str]
) -> list[TaskSpec]:
    """Single coordination_fix step.

    Used as a chain_ref inside fanout branches so that each
    coordination fix group becomes a separate branch with its own
    chain_id.  The ``payload_path`` arg should point to the per-group
    fix prompt written by ``write_fix_prompt()``.
    """
    return [
        TaskSpec(
            task_type="coordination.fix",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
    ]


def _research_ticket_package(
    args: dict, origin_refs: list[str]
) -> list[TaskSpec]:
    """Single web research ticket."""
    del origin_refs
    return [
        TaskSpec(
            task_type="research.domain_ticket",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
    ]


def _research_code_ticket_package(
    args: dict, origin_refs: list[str]
) -> list[TaskSpec]:
    """Code research ticket: scan.explore -> research.domain_ticket."""
    del origin_refs
    return [
        TaskSpec(
            task_type="scan.explore",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("scan_payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
        TaskSpec(
            task_type="research.domain_ticket",
            concern_scope=args.get("concern_scope", ""),
            payload_path=args.get("payload_path", ""),
            priority=args.get("priority", "normal"),
            problem_id=args.get("problem_id", ""),
        ),
    ]


_PACKAGE_REGISTRY: dict[str, callable] = {
    "proposal_alignment_package": _proposal_alignment_package,
    "implementation_alignment_package": _implementation_alignment_package,
    "coordination_fix_package": _coordination_fix_package,
    "research_ticket_package": _research_ticket_package,
    "research_code_ticket_package": _research_code_ticket_package,
}

KNOWN_PACKAGES: frozenset[str] = frozenset(_PACKAGE_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_chain_ref(
    name: str, args: dict, origin_refs: list[str]
) -> list[TaskSpec]:
    """Resolve a named chain package to a list of TaskSpec steps.

    Known packages:
    - "proposal_alignment_package" -> [proposal.integration, staleness.alignment_check]
    - "implementation_alignment_package" -> [implementation.strategic, staleness.alignment_check]
    - "coordination_fix_package" -> [coordination.fix]

    Raises ValueError for unknown package names.
    """
    if name not in _PACKAGE_REGISTRY:
        raise ValueError(
            f"Unknown chain_ref package: {name!r}. "
            f"Known packages: {sorted(KNOWN_PACKAGES)}"
        )
    return _PACKAGE_REGISTRY[name](args, origin_refs)


def build_coordination_branches(
    groups: dict[int, list[dict]],
    planspace: Path,
) -> list[BranchSpec]:
    """Convert coordination fix groups into BranchSpec list for submit_fanout.

    Each group becomes a separate branch using the ``coordination_fix_package``
    chain_ref. The ``payload_path`` points to the per-group fix prompt at
    ``artifacts/coordination/fix-{group_id}-prompt.md``.

    Args:
        groups: mapping of group_id -> list of problem dicts (same structure
            used by ``_dispatch_fix_group``).
        planspace: planspace root for computing prompt paths.

    Returns:
        List of BranchSpec, one per group. Empty list if groups is empty.
    """
    branches: list[BranchSpec] = []
    registry = PathRegistry(planspace)
    for group_id in sorted(groups):
        prompt_path = registry.coordination_fix_prompt(group_id)
        branches.append(
            BranchSpec(
                label=f"coord-fix-{group_id}",
                chain_ref="coordination_fix_package",
                args={
                    "concern_scope": f"coord-group-{group_id}",
                    "payload_path": str(prompt_path),
                    "priority": "normal",
                },
            )
        )
    return branches
