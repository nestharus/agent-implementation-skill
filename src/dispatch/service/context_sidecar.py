"""Agent-scoped context resolution and sidecar materialization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService

logger = logging.getLogger(__name__)


def _parse_inline_yaml_list(text: str) -> list[str]:
    """Parse a YAML inline list like ``[a, b, c]`` into string items."""
    if not (text.startswith("[") and text.endswith("]")):
        return []
    items: list[str] = []
    for item in text[1:-1].split(","):
        value = item.strip().strip("'\"")
        if value:
            items.append(value)
    return items


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
                return _parse_inline_yaml_list(inline)
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
    "user_entry",
    "classification",
    "problems",
    "values",
    "proposal",
})


class ContextSidecar:
    """Agent-scoped context resolution and sidecar materialization."""

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def _resolve_section_spec(self, planspace: Path, section: str | None) -> str:
        if not section:
            return ""
        return self._artifact_io.read_if_exists(PathRegistry(planspace).section_spec(section))

    def _resolve_decision_history(self, planspace: Path, section: str | None) -> str:
        paths = PathRegistry(planspace)
        if section:
            json_path = paths.decision_json(section)
        else:
            json_path = paths.global_decision_json()
        return self._artifact_io.read_if_exists(json_path)

    def _resolve_strategic_state(self, planspace: Path, _section: str | None) -> str:
        return self._artifact_io.read_if_exists(PathRegistry(planspace).strategic_state())

    def _resolve_coordination_state(self, planspace: Path, _section: str | None) -> str:
        return self._artifact_io.read_if_exists(
            PathRegistry(planspace).coordination_problems()
        )

    def _resolve_model_policy(self, planspace: Path, _section: str | None) -> str:
        return self._artifact_io.read_if_exists(PathRegistry(planspace).model_policy())

    def _resolve_governance(self, planspace: Path, section: str | None) -> str:
        if not section:
            return ""
        return self._artifact_io.read_if_exists(PathRegistry(planspace).governance_packet(section))

    def _resolve_user_entry(self, planspace: Path, _section: str | None) -> str:
        spec_path = PathRegistry(planspace).artifacts / "spec.md"
        if spec_path.exists():
            return spec_path.read_text(encoding="utf-8")
        return ""

    def _resolve_classification(self, planspace: Path, _section: str | None) -> str:
        return self._artifact_io.read_if_exists(
            PathRegistry(planspace).entry_classification_json()
        )

    def _resolve_problems(self, planspace: Path, _section: str | None) -> str:
        paths = PathRegistry(planspace)
        explored = paths.global_problems_dir() / "explored-problems.json"
        if explored.exists():
            return explored.read_text(encoding="utf-8")
        initial = paths.global_problems_dir() / "initial-problems.json"
        if initial.exists():
            return initial.read_text(encoding="utf-8")
        return ""

    def _resolve_values(self, planspace: Path, _section: str | None) -> str:
        paths = PathRegistry(planspace)
        explored = paths.global_values_dir() / "explored-values.json"
        if explored.exists():
            return explored.read_text(encoding="utf-8")
        initial = paths.global_values_dir() / "initial-values.json"
        if initial.exists():
            return initial.read_text(encoding="utf-8")
        return ""

    def _resolve_proposal(self, planspace: Path, _section: str | None) -> str:
        return self._artifact_io.read_if_exists(
            PathRegistry(planspace).global_proposal()
        )

    def check_codemap_refine_signal(
        self,
        planspace: Path,
        section: str | None,
    ) -> bool:
        """Detect and consume a codemap-refinement-needed signal for *section*.

        If a signal file exists at
        ``PathRegistry.codemap_refine_signal(section)``, reads it,
        deletes the file (consume-once), and returns ``True``.

        This is the on-demand hook: agents can request codemap refinement
        by writing the signal file.  The caller is responsible for
        submitting the ``scan.codemap_refine`` task when this returns
        ``True``.

        Returns ``False`` when *section* is ``None`` or no signal file
        is present.
        """
        if not section:
            return False

        paths = PathRegistry(planspace)
        signal_path = paths.codemap_refine_signal(section)
        if not signal_path.is_file():
            return False

        try:
            signal_path.unlink()
        except OSError:
            pass
        logger.info(
            "codemap_refine signal detected for section %s", section,
        )
        return True

    def resolve_context(
        self,
        agent_file: str,
        planspace: Path,
        section: str | None = None,
    ) -> dict[str, str]:
        """Resolve an agent's declared context categories to content strings."""
        categories = parse_context_field(agent_file)
        if not categories:
            return {}

        resolvers = {
            "section_spec": self._resolve_section_spec,
            "decision_history": self._resolve_decision_history,
            "strategic_state": self._resolve_strategic_state,
            "codemap": _resolve_codemap,
            "related_files": _resolve_related_files,
            "coordination_state": self._resolve_coordination_state,
            "allowed_tasks": _resolve_allowed_tasks,
            "section_output": _resolve_section_output,
            "model_policy": self._resolve_model_policy,
            "flow_context": _resolve_flow_context,
            "governance": self._resolve_governance,
            "user_entry": self._resolve_user_entry,
            "classification": self._resolve_classification,
            "problems": self._resolve_problems,
            "values": self._resolve_values,
            "proposal": self._resolve_proposal,
        }

        result: dict[str, str] = {}
        for category in categories:
            if category not in VALID_CATEGORIES:
                continue
            resolver = resolvers.get(category)
            if resolver is not None:
                result[category] = resolver(planspace, section)

        # Check for codemap-refinement-needed signal (any-state, 5D).
        # When the signal is present, mark the result so the caller
        # can submit a scan.codemap_refine task.
        if self.check_codemap_refine_signal(planspace, section):
            result["_codemap_refine_needed"] = section

        return result

    def materialize_context_sidecar(
        self,
        agent_file_path: str,
        planspace: Path,
        section: str | None = None,
    ) -> Path | None:
        """Resolve and write the scoped-context sidecar JSON."""
        agent_context = self.resolve_context(agent_file_path, planspace, section=section)
        if not agent_context:
            return None
        ctx_path = PathRegistry(planspace).context_sidecar(Path(agent_file_path).stem)
        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text(
            json.dumps(agent_context, indent=2) + "\n",
            encoding="utf-8",
        )
        return ctx_path


# ---------------------------------------------------------------------------
# Pure resolvers (no Services usage)
# ---------------------------------------------------------------------------

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
    json_path = paths.related_files_signal(section)
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


def _resolve_flow_context(planspace: Path, _section: str | None) -> str:
    flows_dir = PathRegistry(planspace).flows_dir()
    if not flows_dir.is_dir():
        return ""
    context_files = sorted(flows_dir.glob("task-*-context.json"))
    if len(context_files) == 1:
        return context_files[0].read_text(encoding="utf-8")
    return ""
