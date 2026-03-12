"""Agent-scoped context resolution and sidecar materialization."""

from __future__ import annotations

import json
from pathlib import Path

from flow.helpers.file_utils import read_if_exists
from orchestrator.path_registry import PathRegistry


def parse_context_field(agent_file: str) -> list[str]:
    """Extract the ``context:`` list from an agent file's YAML frontmatter."""
    path = Path(agent_file)
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return []

    end = text.find("\n---", 3)
    if end < 0:
        return []

    frontmatter = text[3:end]
    categories: list[str] = []
    in_context = False
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("context:"):
            inline = stripped[len("context:"):].strip()
            if inline:
                if inline.startswith("[") and inline.endswith("]"):
                    for item in inline[1:-1].split(","):
                        value = item.strip().strip("'\"")
                        if value:
                            categories.append(value)
                    return categories
            in_context = True
            continue
        if in_context:
            if stripped.startswith("- "):
                categories.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                break

    return categories


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
    "flow_context",
    "governance",
})


def _resolve_section_spec(planspace: Path, section: str | None) -> str:
    if not section:
        return ""
    return read_if_exists(PathRegistry(planspace).section_spec(section))


def _resolve_decision_history(planspace: Path, section: str | None) -> str:
    paths = PathRegistry(planspace)
    decisions_dir = paths.decisions_dir()
    if section:
        json_path = decisions_dir / f"section-{section}.json"
    else:
        json_path = decisions_dir / "global.json"
    return read_if_exists(json_path)


def _resolve_strategic_state(planspace: Path, _section: str | None) -> str:
    return read_if_exists(PathRegistry(planspace).strategic_state())


def _resolve_codemap(planspace: Path, _section: str | None) -> str:
    paths = PathRegistry(planspace)
    codemap_path = paths.codemap()
    if not codemap_path.exists():
        return ""
    content = codemap_path.read_text(encoding="utf-8")
    corrections_path = paths.corrections()
    if corrections_path.exists():
        corrections_text = corrections_path.read_text(encoding="utf-8")
        content += (
            "\n\n## Codemap Corrections (authoritative)\n\n"
            "The following corrections override the routing claims above. "
            "Treat these as the ground truth where they conflict with the "
            "codemap body.\n\n"
            f"```json\n{corrections_text}\n```\n"
        )
    return content


def _resolve_related_files(planspace: Path, section: str | None) -> str:
    if not section:
        return ""
    paths = PathRegistry(planspace)
    json_path = paths.signals_dir() / f"related-files-{section}.json"
    if json_path.exists():
        return json_path.read_text(encoding="utf-8")

    spec_path = paths.section_spec(section)
    if spec_path.exists():
        text = spec_path.read_text(encoding="utf-8")
        marker = "## Related Files"
        index = text.find(marker)
        if index >= 0:
            next_heading = text.find("\n## ", index + len(marker))
            if next_heading >= 0:
                return text[index:next_heading].strip()
            return text[index:].strip()
    return ""


def _resolve_coordination_state(planspace: Path, _section: str | None) -> str:
    return read_if_exists(
        PathRegistry(planspace).coordination_dir() / "problems.json"
    )


def _resolve_allowed_tasks(_planspace: Path, _section: str | None) -> str:
    from taskrouter import ensure_discovered, registry as _reg

    ensure_discovered()
    return json.dumps(sorted(_reg.all_task_types), indent=2)


def _resolve_section_output(planspace: Path, section: str | None) -> str:
    if not section:
        return ""
    artifacts = PathRegistry(planspace).artifacts
    for path in [
        artifacts / f"intg-proposal-{section}-output.md",
        artifacts / f"intg-align-{section}-output.md",
        artifacts / f"section-{section}-output.md",
    ]:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _resolve_model_policy(planspace: Path, _section: str | None) -> str:
    return read_if_exists(PathRegistry(planspace).model_policy())


def _resolve_flow_context(planspace: Path, _section: str | None) -> str:
    flows_dir = PathRegistry(planspace).flows_dir()
    if not flows_dir.is_dir():
        return ""
    context_files = sorted(flows_dir.glob("task-*-context.json"))
    if len(context_files) == 1:
        return context_files[0].read_text(encoding="utf-8")
    return ""


def _resolve_governance(planspace: Path, section: str | None) -> str:
    if not section:
        return ""
    return read_if_exists(PathRegistry(planspace).governance_packet(section))


_RESOLVERS = {
    "section_spec": _resolve_section_spec,
    "decision_history": _resolve_decision_history,
    "strategic_state": _resolve_strategic_state,
    "codemap": _resolve_codemap,
    "related_files": _resolve_related_files,
    "coordination_state": _resolve_coordination_state,
    "allowed_tasks": _resolve_allowed_tasks,
    "section_output": _resolve_section_output,
    "model_policy": _resolve_model_policy,
    "flow_context": _resolve_flow_context,
    "governance": _resolve_governance,
}


def resolve_context(
    agent_file: str,
    planspace: Path,
    section: str | None = None,
) -> dict[str, str]:
    """Resolve an agent's declared context categories to content strings."""
    categories = parse_context_field(agent_file)
    if not categories:
        return {}

    result: dict[str, str] = {}
    for category in categories:
        if category not in VALID_CATEGORIES:
            continue
        resolver = _RESOLVERS.get(category)
        if resolver is not None:
            result[category] = resolver(planspace, section)

    return result


def materialize_context_sidecar(
    agent_file_path: str,
    planspace: Path,
    section: str | None = None,
) -> Path | None:
    """Resolve and write the scoped-context sidecar JSON."""
    agent_context = resolve_context(agent_file_path, planspace, section=section)
    if not agent_context:
        return None
    ctx_path = PathRegistry(planspace).context_sidecar(Path(agent_file_path).stem)
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text(
        json.dumps(agent_context, indent=2) + "\n",
        encoding="utf-8",
    )
    return ctx_path
