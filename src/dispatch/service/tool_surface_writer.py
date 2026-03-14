"""Tool registry surface writer — reads registry and presents tools to agents.

Public API: ``surface_tool_registry()``, ``write_tool_surface()``.
"""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from signals.service.blocker_manager import _update_blocker_rollup
from signals.types import SIGNAL_NEEDS_PARENT


def _extract_tools(registry: dict | list) -> list:
    """Normalise registry (list or dict with 'tools' key) to a plain list."""
    return registry if isinstance(registry, list) else registry.get("tools", [])


def _log_surface_result(
    section_number: str,
    relevant_count: int,
    total: int,
    tools_available_path: Path,
) -> None:
    """Log the outcome of a tool-surface write."""
    if relevant_count:
        Services.logger().log(
            f"Section {section_number}: {relevant_count} "
            f"relevant tools (of {total} total)"
        )
    elif tools_available_path.exists():
        Services.logger().log(
            f"Section {section_number}: removed stale "
            f"tools-available surface (no relevant tools)"
        )


def _dispatch_registry_repair(
    *,
    section_number: str,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> bool:
    """Clean up stale surface and dispatch a repair agent for a malformed registry.

    Returns True if the repair dispatch was issued, False if prompt
    validation prevented it.
    """
    paths = PathRegistry(planspace)
    tool_registry_path = paths.tool_registry()
    tools_available_path = paths.tools_available(section_number)
    artifacts = paths.artifacts
    policy = Services.policies().load(planspace)
    if tools_available_path.exists():
        tools_available_path.unlink()
        Services.logger().log(
            f"Section {section_number}: removed stale "
            f"tools-available surface (malformed registry)"
        )
    Services.logger().log(
        f"Section {section_number}: tool-registry.json "
        f"malformed — dispatching repair "
        f"(original preserved as .malformed.json)"
    )
    malformed_path = tool_registry_path.with_suffix(".malformed.json")
    repair_prompt = artifacts / f"tool-registry-repair-{section_number}-prompt.md"
    repair_output = artifacts / f"tool-registry-repair-{section_number}-output.md"
    if not Services.prompt_guard().write_validated(
        f"# Task: Repair Tool Registry\n\n"
        f"The tool registry was malformed. The preserved copy is at "
        f"`{malformed_path}`.\n\n"
        f"Read the malformed file, reconstruct valid JSON preserving all "
        f"tool entries, and write back to `{tool_registry_path}`.\n",
        repair_prompt,
    ):
        return False
    Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "tool_registrar"),
        repair_prompt,
        repair_output,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file=Services.task_router().agent_for("dispatch.tool_registry_repair"),
    )
    return True


def _handle_repair_result(
    *,
    section_number: str,
    planspace: Path,
) -> int:
    """After a repair dispatch, re-read the registry, surface tools, or write a blocker.

    Returns the total tool count (0 if repair failed).
    """
    paths = PathRegistry(planspace)
    tool_registry_path = paths.tool_registry()
    tools_available_path = paths.tools_available(section_number)
    registry = Services.artifact_io().read_json(tool_registry_path)
    if registry is not None:
        all_tools = _extract_tools(registry)
        Services.logger().log(
            f"Section {section_number}: tool registry "
            f"repaired ({len(all_tools)} tools)"
        )
        relevant_count = write_tool_surface(
            all_tools, section_number, tools_available_path,
        )
        if relevant_count:
            Services.logger().log(
                f"Section {section_number}: rebuilt tools "
                f"surface ({relevant_count} relevant tools)"
            )
        return len(all_tools)

    Services.logger().log(
        f"Section {section_number}: tool registry "
        f"repair failed — writing blocker signal"
    )
    blocker = {
        "state": SIGNAL_NEEDS_PARENT,
        "detail": (
            "Tool registry malformed; repair agent "
            "could not fix it."
        ),
        "needs": "Valid tool-registry.json",
        "why_blocked": (
            "Cannot safely surface tools with an "
            "invalid registry."
        ),
    }
    Services.artifact_io().write_json(
        PathRegistry(planspace).blocker_signal(section_number),
        blocker,
    )
    _update_blocker_rollup(planspace)
    return 0


def write_tool_surface(
    all_tools: list,
    section_number: str,
    tools_available_path: Path,
) -> int:
    """Filter and write section-relevant tools surface."""
    sec_key = f"section-{section_number}"
    relevant_tools = [
        tool for tool in all_tools
        if tool.get("scope") == "cross-section"
        or tool.get("created_by") == sec_key
    ]
    if relevant_tools:
        lines = ["# Available Tools\n", "Cross-section and section-local tools:\n"]
        for tool in relevant_tools:
            path = tool.get("path", "unknown")
            desc = tool.get("description", "")
            scope = tool.get("scope", "section-local")
            creator = tool.get("created_by", "unknown")
            status = tool.get("status", "experimental")
            tool_id = tool.get("id", "")
            id_tag = f" id={tool_id}" if tool_id else ""
            lines.append(
                f"- `{path}` [{status}] ({scope}, "
                f"from {creator}{id_tag}): {desc}"
            )
        tools_available_path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    elif tools_available_path.exists():
        tools_available_path.unlink()
    return len(relevant_tools)


def surface_tool_registry(
    *,
    section_number: str,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> int:
    """Load the tool registry, repair if needed, and write the tool surface."""
    paths = PathRegistry(planspace)
    tool_registry_path = paths.tool_registry()
    if not tool_registry_path.exists():
        return 0
    tools_available_path = paths.tools_available(section_number)

    registry = Services.artifact_io().read_json(tool_registry_path)
    if registry is not None:
        all_tools = _extract_tools(registry)
        relevant_count = write_tool_surface(
            all_tools, section_number, tools_available_path,
        )
        _log_surface_result(
            section_number, relevant_count, len(all_tools), tools_available_path,
        )
        return len(all_tools)

    dispatched = _dispatch_registry_repair(
        section_number=section_number,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )
    if not dispatched:
        return 0
    return _handle_repair_result(
        section_number=section_number,
        planspace=planspace,
    )
