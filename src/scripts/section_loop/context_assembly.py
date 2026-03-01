"""Agent-scoped context resolution.

Reads an agent file's YAML frontmatter ``context:`` field and resolves
each declared category to its content string by reading the appropriate
planspace/artifact files.

This is the S1 mechanism for scoping what context an agent sees.  If an
agent file has no ``context:`` field, ``resolve_context`` returns an
empty dict -- fully backwards-compatible with existing prompt assembly.

Context categories
------------------
- ``section_spec``       -- the section specification text
- ``decision_history``   -- structured decision sidecars (JSON array)
- ``strategic_state``    -- the current strategic-state snapshot (JSON)
- ``codemap``            -- the codemap for navigation
- ``related_files``      -- the related files list for the section
- ``coordination_state`` -- cross-section coordination state
- ``allowed_tasks``      -- the task types this agent can submit
- ``section_output``     -- previous output for this section
- ``model_policy``       -- current model policy (JSON)
"""

from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Frontmatter parser (minimal -- avoids PyYAML dependency)
# ---------------------------------------------------------------------------

def parse_context_field(agent_file: str) -> list[str]:
    """Extract the ``context:`` list from an agent file's YAML frontmatter.

    ``agent_file`` is the **absolute path** (or resolvable path) to the
    agent ``.md`` file.  Returns an empty list if the file has no
    ``context:`` field or no frontmatter.
    """
    path = Path(agent_file)
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    # Frontmatter is delimited by ``---`` on its own line.
    if not text.startswith("---"):
        return []

    end = text.find("\n---", 3)
    if end < 0:
        return []

    frontmatter = text[3:end]

    # Find the ``context:`` key and collect its ``- item`` entries.
    categories: list[str] = []
    in_context = False
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("context:"):
            # ``context:`` may have an inline value or start a list.
            inline = stripped[len("context:"):].strip()
            if inline:
                # Unlikely but handle ``context: [a, b]`` YAML inline list.
                if inline.startswith("[") and inline.endswith("]"):
                    for item in inline[1:-1].split(","):
                        item = item.strip().strip("'\"")
                        if item:
                            categories.append(item)
                    return categories
            in_context = True
            continue
        if in_context:
            if stripped.startswith("- "):
                categories.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                # New key encountered -- stop reading context entries.
                break

    return categories


# ---------------------------------------------------------------------------
# Valid category names
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset({
    "section_spec",
    "decision_history",
    "strategic_state",
    "codemap",
    "related_files",
    "coordination_state",
    "allowed_tasks",
    "section_output",
    "model_policy",
})


# ---------------------------------------------------------------------------
# Per-category resolvers
# ---------------------------------------------------------------------------

def _resolve_section_spec(
    planspace: Path, section: str | None,
) -> str:
    """Read the section specification markdown."""
    if not section:
        return ""
    spec_path = planspace / "artifacts" / "sections" / f"section-{section}.md"
    if spec_path.exists():
        return spec_path.read_text(encoding="utf-8")
    return ""


def _resolve_decision_history(
    planspace: Path, section: str | None,
) -> str:
    """Read the structured decision sidecar JSON for a section."""
    decisions_dir = planspace / "artifacts" / "decisions"
    if section:
        json_path = decisions_dir / f"section-{section}.json"
    else:
        json_path = decisions_dir / "global.json"
    if json_path.exists():
        return json_path.read_text(encoding="utf-8")
    return ""


def _resolve_strategic_state(
    planspace: Path, _section: str | None,
) -> str:
    """Read the current strategic-state snapshot."""
    state_path = planspace / "artifacts" / "strategic-state.json"
    if state_path.exists():
        return state_path.read_text(encoding="utf-8")
    return ""


def _resolve_codemap(
    planspace: Path, _section: str | None,
) -> str:
    """Read the codemap markdown."""
    codemap_path = planspace / "artifacts" / "codemap.md"
    if codemap_path.exists():
        return codemap_path.read_text(encoding="utf-8")
    return ""


def _resolve_related_files(
    planspace: Path, section: str | None,
) -> str:
    """Read the related-files list for a section.

    Related files are embedded in the section spec markdown under a
    ``## Related Files`` heading, or stored as a standalone JSON
    artifact.  We try the JSON artifact first, then fall back to the
    section file.
    """
    if not section:
        return ""
    # JSON artifact (written by scan stage)
    json_path = (
        planspace / "artifacts" / "signals"
        / f"related-files-{section}.json"
    )
    if json_path.exists():
        return json_path.read_text(encoding="utf-8")
    # Fallback: section spec may embed related files
    spec_path = planspace / "artifacts" / "sections" / f"section-{section}.md"
    if spec_path.exists():
        text = spec_path.read_text(encoding="utf-8")
        # Extract the ## Related Files block if present.
        marker = "## Related Files"
        idx = text.find(marker)
        if idx >= 0:
            block_start = idx
            # Find the next heading or end of file.
            next_heading = text.find("\n## ", idx + len(marker))
            if next_heading >= 0:
                return text[block_start:next_heading].strip()
            return text[block_start:].strip()
    return ""


def _resolve_coordination_state(
    planspace: Path, _section: str | None,
) -> str:
    """Read cross-section coordination state.

    The coordination directory may contain multiple files.  We return
    the coordination problems JSON if it exists, as that is the
    structured summary of coordination state.
    """
    coord_dir = planspace / "artifacts" / "coordination"
    problems_path = coord_dir / "problems.json"
    if problems_path.exists():
        return problems_path.read_text(encoding="utf-8")
    return ""


def _resolve_allowed_tasks(
    _planspace: Path, _section: str | None,
) -> str:
    """Return the list of task types from the task router.

    This is a static list derived from ``task_router.TASK_ROUTES``.
    We import it lazily to avoid circular dependencies.
    """
    try:
        from task_router import TASK_ROUTES
        return json.dumps(sorted(TASK_ROUTES.keys()), indent=2)
    except ImportError:
        # Fallback: return a hardcoded snapshot of known task types.
        return json.dumps([
            "alignment_adjudicate", "alignment_check",
            "consequence_triage", "coordination_fix",
            "exception_handling", "impact_analysis",
            "integration_proposal", "microstrategy_decision",
            "recurrence_adjudication", "scan_adjudicate",
            "scan_codemap_build", "scan_codemap_freshness",
            "scan_codemap_verify", "scan_deep_analyze",
            "scan_explore", "scan_tier_rank",
            "section_setup", "state_adjudicate",
            "strategic_implementation",
            "substrate_prune", "substrate_seed", "substrate_shard",
            "tool_registry_repair",
        ], indent=2)


def _resolve_section_output(
    planspace: Path, section: str | None,
) -> str:
    """Read the most recent output for a section.

    Looks for common output file patterns in artifacts/.
    """
    if not section:
        return ""
    artifacts = planspace / "artifacts"
    # Try integration-proposal output first (most common section output)
    candidates = [
        artifacts / f"intg-proposal-{section}-output.md",
        artifacts / f"intg-align-{section}-output.md",
        artifacts / f"section-{section}-output.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _resolve_model_policy(
    planspace: Path, _section: str | None,
) -> str:
    """Read the current model policy JSON."""
    policy_path = planspace / "artifacts" / "model-policy.json"
    if policy_path.exists():
        return policy_path.read_text(encoding="utf-8")
    return ""


# Map category name -> resolver function
_RESOLVERS: dict[str, object] = {
    "section_spec": _resolve_section_spec,
    "decision_history": _resolve_decision_history,
    "strategic_state": _resolve_strategic_state,
    "codemap": _resolve_codemap,
    "related_files": _resolve_related_files,
    "coordination_state": _resolve_coordination_state,
    "allowed_tasks": _resolve_allowed_tasks,
    "section_output": _resolve_section_output,
    "model_policy": _resolve_model_policy,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_context(
    agent_file: str,
    planspace: Path,
    section: str | None = None,
) -> dict[str, str]:
    """Resolve an agent's declared context categories to content strings.

    Parameters
    ----------
    agent_file:
        Absolute path to the agent ``.md`` file.
    planspace:
        The planspace directory for the current run.
    section:
        Optional section number (e.g. ``"01"``).  Required for
        section-scoped categories like ``section_spec``.

    Returns
    -------
    A dict mapping each declared context category name to its resolved
    content string.  Categories whose artifacts are missing resolve to
    ``""``.  If the agent file has no ``context:`` field, returns an
    empty dict.
    """
    categories = parse_context_field(agent_file)
    if not categories:
        return {}

    result: dict[str, str] = {}
    for cat in categories:
        if cat not in VALID_CATEGORIES:
            # Unknown category -- skip silently (forward-compatible).
            continue
        resolver = _RESOLVERS.get(cat)
        if resolver is not None:
            result[cat] = resolver(planspace, section)  # type: ignore[operator]

    return result
