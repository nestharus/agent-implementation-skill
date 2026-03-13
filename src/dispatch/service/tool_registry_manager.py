from __future__ import annotations

from pathlib import Path
from typing import Any

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
    tool_registry_path: Path,
    tools_available_path: Path,
    artifacts: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
    policy: object,
) -> bool:
    """Clean up stale surface and dispatch a repair agent for a malformed registry.

    Returns True if the repair dispatch was issued, False if prompt
    validation prevented it.
    """
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
    tool_registry_path: Path,
    tools_available_path: Path,
    planspace: Path,
) -> int:
    """After a repair dispatch, re-read the registry, surface tools, or write a blocker.

    Returns the total tool count (0 if repair failed).
    """
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
    tool_registry_path: Path,
    tools_available_path: Path,
    artifacts: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> int:
    """Load the tool registry, repair if needed, and write the tool surface."""
    policy = Services.policies().load(planspace)
    if not tool_registry_path.exists():
        return 0

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
        tool_registry_path=tool_registry_path,
        tools_available_path=tools_available_path,
        artifacts=artifacts,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        policy=policy,
    )
    if not dispatched:
        return 0
    return _handle_repair_result(
        section_number=section_number,
        tool_registry_path=tool_registry_path,
        tools_available_path=tools_available_path,
        planspace=planspace,
    )


def _build_registrar_prompt(
    section_number: str,
    tool_registry_path: Path,
    digest_path: Path,
    friction_signal_path: Path,
) -> str:
    """Build the prompt text for the tool-registrar validation dispatch."""
    return (
        f"# Validate Tool Registry\n\n"
        f"Section {section_number} just completed implementation.\n"
        f"Validate the tool registry at: `{tool_registry_path}`\n\n"
        f"For each tool entry:\n"
        f"1. Read the tool file and verify it exists and is legitimate\n"
        f"2. Verify scope classification is correct\n"
        f"3. Ensure required fields exist: `id`, `path`, "
        f"`created_by`, `scope`, `status`, `description`, "
        f"`registered_at`\n"
        f"4. If `id` is missing, assign a short kebab-case identifier\n"
        f"5. If `status` is missing, set to `experimental`\n"
        f"6. Promote tools to `stable` if they have passing tests or are "
        f"used by multiple sections\n"
        f"7. Remove entries for files that don't exist or aren't tools\n"
        f"8. If any cross-section tools were added, verify they are "
        f"genuinely reusable\n\n"
        f"After validation, write a tool digest to: "
        f"`{digest_path}`\n"
        f"Format: one line per tool grouped by scope "
        f"(cross-section, section-local, test-only).\n\n"
        f"Write the validated registry back to the same path.\n\n"
        f"## Tool Friction Detection\n\n"
        f"After validation, analyze the capability graph for disconnected "
        f"tool islands or missing bridges. If you detect friction, write "
        f"a friction signal to:\n"
        f"`{friction_signal_path}`\n\n"
        f"Format: `{{\"friction\": true, \"islands\": [[...]], "
        f"\"missing_bridge\": \"...\"}}`\n"
        f"If no friction detected, do NOT write a friction signal file.\n"
    )


def _dispatch_new_tool_validation(
    *,
    section_number: str,
    tool_registry_path: Path,
    artifacts: Path,
    friction_signal_path: Path,
    paths: Any,
    policy: Any,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> None:
    """Dispatch the tool-registrar agent to validate newly registered tools."""
    Services.logger().log(
        f"Section {section_number}: new tools registered — "
        f"dispatching tool-registrar for validation"
    )
    registrar_prompt = artifacts / f"tool-registrar-{section_number}-prompt.md"
    prompt_text = _build_registrar_prompt(
        section_number, tool_registry_path,
        paths.tool_digest(), friction_signal_path,
    )
    Services.prompt_guard().write_validated(prompt_text, registrar_prompt)
    registrar_output = artifacts / f"tool-registrar-{section_number}-output.md"
    Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "tool_registrar"),
        registrar_prompt,
        registrar_output,
        planspace,
        parent,
        f"tool-registrar-{section_number}",
        codespace=codespace,
        agent_file=Services.task_router().agent_for("dispatch.tool_registry_repair"),
        section_number=section_number,
    )


def _compose_repair_text(section_number: str, malformed_path: Path, tool_registry_path: Path) -> str:
    """Build the prompt text for post-implementation registry repair."""
    return (
        f"# Task: Repair Tool Registry (Post-Implementation)\n\n"
        f"The tool registry was malformed after section {section_number} "
        f"implementation. The preserved copy is at "
        f"`{malformed_path}`.\n\n"
        f"Read the malformed file, reconstruct valid JSON preserving all "
        f"tool entries, and write back to `{tool_registry_path}`.\n"
    )


def _dispatch_post_impl_repair(
    *,
    section_number: str,
    tool_registry_path: Path,
    artifacts: Path,
    paths: Any,
    policy: Any,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> None:
    """Dispatch repair for a malformed post-implementation registry."""
    malformed_path = tool_registry_path.with_suffix(".malformed.json")
    Services.logger().log(
        f"Section {section_number}: post-impl registry "
        f"malformed — dispatching repair "
        f"(original preserved as {malformed_path.name})"
    )
    repair_prompt = (
        artifacts / f"tool-registry-post-repair-{section_number}-prompt.md"
    )
    repair_output = (
        artifacts / f"tool-registry-post-repair-{section_number}-output.md"
    )
    Services.prompt_guard().write_validated(
        _compose_repair_text(section_number, malformed_path, tool_registry_path),
        repair_prompt,
    )
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
    if Services.artifact_io().read_json(tool_registry_path) is not None:
        Services.logger().log(
            f"Section {section_number}: post-impl tool registry repaired"
        )
    else:
        Services.logger().log(
            f"Section {section_number}: post-impl tool "
            f"registry repair failed — writing blocker"
        )
        blocker = {
            "state": SIGNAL_NEEDS_PARENT,
            "detail": (
                "Tool registry malformed after "
                "implementation; repair agent could "
                "not fix it."
            ),
            "needs": "Valid tool-registry.json",
            "why_blocked": (
                "Malformed registry affects subsequent "
                "sections' tool surfacing."
            ),
        }
        Services.artifact_io().write_json(
            paths.post_impl_blocker_signal(section_number),
            blocker,
        )
        _update_blocker_rollup(planspace)


def validate_tool_registry_after_implementation(
    *,
    section_number: str,
    pre_tool_total: int,
    tool_registry_path: Path,
    artifacts: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> Path:
    """Validate the tool registry after implementation and return the friction path."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    friction_signal_path = paths.tool_friction_signal(section_number)
    if not tool_registry_path.exists():
        return friction_signal_path

    post_registry = Services.artifact_io().read_json(tool_registry_path)
    if post_registry is not None:
        post_tools = _extract_tools(post_registry)
        if len(post_tools) > pre_tool_total:
            _dispatch_new_tool_validation(
                section_number=section_number,
                tool_registry_path=tool_registry_path,
                artifacts=artifacts,
                friction_signal_path=friction_signal_path,
                paths=paths,
                policy=policy,
                planspace=planspace,
                parent=parent,
                codespace=codespace,
            )
    else:
        _dispatch_post_impl_repair(
            section_number=section_number,
            tool_registry_path=tool_registry_path,
            artifacts=artifacts,
            paths=paths,
            policy=policy,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )

    return friction_signal_path


def _detect_friction(friction_signal_path: Path) -> bool:
    if not friction_signal_path.exists():
        return False
    friction = Services.artifact_io().read_json(friction_signal_path)
    if friction is not None:
        return friction.get("friction", False)
    return True


def _validate_bridge_signal(
    bridge_signal_path: Path, default_proposal_path: Path,
) -> tuple[bool, dict | None]:
    bridge_data = Services.artifact_io().read_json(bridge_signal_path)
    if bridge_data is None:
        return False, None
    if bridge_data.get("status") not in ("bridged", "no_action", SIGNAL_NEEDS_PARENT):
        return False, bridge_data
    proposal_path = Path(
        bridge_data.get("proposal_path", str(default_proposal_path))
    )
    if bridge_data["status"] == "no_action" or proposal_path.exists():
        return True, bridge_data
    return False, bridge_data


def _compose_bridge_text(
    section_number: str,
    tool_registry_path: Path,
    section_path,
    proposal_path: Path,
    default_proposal_path: Path,
    bridge_signal_path: Path,
) -> str:
    """Build the prompt text for the bridge-tools agent."""
    return f"""# Task: Bridge Tool Islands for Section {section_number}

## Context
Section {section_number} has signaled tooling friction — tools don't compose
cleanly or a needed tool doesn't exist.

## Files to Read
1. Tool registry: `{tool_registry_path}`
2. Section specification: `{section_path}`
3. Integration proposal: `{proposal_path}`

## Instructions
Analyze the tool registry and section needs. Either:
(a) Propose a new tool that bridges the gap
(b) Propose a composition pattern connecting existing tools

Write your proposal to: `{default_proposal_path}`
Update the tool registry if new tools are proposed.

## Structured Signal (Required)
Write a structured signal to: `{bridge_signal_path}`
with JSON:
```json
{{
  "status": "bridged"|"no_action"|"needs_parent",
  "proposal_path": "...",
  "notes": "...",
  "targets": ["03", "07"],
  "broadcast": false,
  "note_markdown": "..."
}}
```

- `targets` (optional): section numbers that need this bridge info
- `broadcast` (optional): if true, all sections receive a note
- `note_markdown` (optional): summary for target sections
"""


def _dispatch_bridge_agent(
    *, section_number, section_path, tool_registry_path,
    paths, policy, planspace, parent, codespace,
):
    bridge_tools_prompt = paths.bridge_tools_prompt(section_number)
    bridge_tools_output = paths.bridge_tools_output(section_number)
    bridge_signal_path = paths.tool_bridge_signal(section_number)
    default_proposal_path = paths.tool_bridge_proposal(section_number)
    if not Services.prompt_guard().write_validated(
        _compose_bridge_text(
            section_number,
            tool_registry_path,
            section_path,
            paths.proposal(section_number),
            default_proposal_path,
            bridge_signal_path,
        ),
        bridge_tools_prompt,
    ):
        return None, None, None

    pre_bridge_registry_hash = ""
    if tool_registry_path.exists():
        pre_bridge_registry_hash = Services.hasher().file_hash(tool_registry_path)

    Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "bridge_tools"),
        bridge_tools_prompt,
        bridge_tools_output,
        planspace,
        parent,
        f"bridge-tools-{section_number}",
        codespace=codespace,
        agent_file=Services.task_router().agent_for("dispatch.bridge_tools"),
        section_number=section_number,
    )

    bridge_valid, bridge_data = _validate_bridge_signal(
        bridge_signal_path, default_proposal_path,
    )

    if not bridge_valid:
        Services.logger().log(
            f"Section {section_number}: bridge signal missing or "
            f"invalid — retrying with escalation model"
        )
        escalation_output = paths.bridge_tools_escalation_output(section_number)
        Services.dispatcher().dispatch(
            Services.policies().resolve(policy, "escalation_model"),
            bridge_tools_prompt,
            escalation_output,
            planspace,
            parent,
            f"bridge-tools-{section_number}-escalation",
            codespace=codespace,
            agent_file=Services.task_router().agent_for("dispatch.bridge_tools"),
            section_number=section_number,
        )
        bridge_valid, bridge_data = _validate_bridge_signal(
            bridge_signal_path, default_proposal_path,
        )

    return bridge_valid, bridge_data, pre_bridge_registry_hash


def _compose_bridge_success_text(
    tool_registry_path: Path,
    section_number: str,
    tool_digest_path: Path,
) -> str:
    """Build the prompt text for regenerating the tool digest after bridge."""
    return (
        f"# Task: Regenerate Tool Digest\n\n"
        f"The tool registry at `{tool_registry_path}` was "
        f"modified by bridge-tools for section "
        f"{section_number}.\n\n"
        f"Read the registry and write an updated tool digest "
        f"to: `{tool_digest_path}`\n\n"
        f"Format: one line per tool grouped by scope "
        f"(cross-section, section-local, test-only).\n"
    )


def _handle_bridge_success(
    *, bridge_data, section_number, all_sections, tool_registry_path,
    pre_bridge_registry_hash, paths, artifacts, planspace, parent,
    codespace,
):
    default_proposal_path = paths.tool_bridge_proposal(section_number)
    bridge_proposal = bridge_data.get("proposal_path", str(default_proposal_path))
    inputs_dir = paths.input_refs_dir(section_number)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    ref_file = inputs_dir / "tool-bridge.ref"
    ref_file.write_text(str(bridge_proposal), encoding="utf-8")
    Services.logger().log(f"Section {section_number}: bridge proposal registered as input ref")

    targets = bridge_data.get("targets", [])
    broadcast = bridge_data.get("broadcast", False)
    note_md = bridge_data.get("note_markdown", "")
    if note_md and (targets or broadcast):
        if broadcast and all_sections:
            targets = [section.number for section in all_sections if section.number != section_number]
        for target in targets:
            Services.cross_section().write_consequence_note(
                planspace,
                f"bridge-{section_number}",
                str(target),
                f"# Bridge Note from Section {section_number}\n\n"
                f"{note_md}\n\n"
                f"See full proposal: `{bridge_proposal}`\n",
            )
        if targets:
            Services.logger().log(
                f"Section {section_number}: bridge notes routed "
                f"to {len(targets)} section(s)"
            )

    post_bridge_registry_hash = ""
    if tool_registry_path.exists():
        post_bridge_registry_hash = Services.hasher().file_hash(tool_registry_path)
    if post_bridge_registry_hash and pre_bridge_registry_hash != post_bridge_registry_hash:
        Services.logger().log(
            f"Section {section_number}: tool registry modified "
            f"by bridge-tools — regenerating digest"
        )
        digest_prompt = artifacts / f"tool-digest-regen-{section_number}-prompt.md"
        digest_output = artifacts / f"tool-digest-regen-{section_number}-output.md"
        Services.prompt_guard().write_validated(
            _compose_bridge_success_text(
                tool_registry_path, section_number, paths.tool_digest(),
            ),
            digest_prompt,
        )
        policy = Services.policies().load(planspace)
        Services.dispatcher().dispatch(
            Services.policies().resolve(policy, "tool_registrar"),
            digest_prompt,
            digest_output,
            planspace,
            parent,
            f"tool-digest-regen-{section_number}",
            codespace=codespace,
            section_number=section_number,
            agent_file=Services.task_router().agent_for("dispatch.tool_registry_repair"),
        )


def _handle_bridge_failure(*, section_number, paths, planspace):
    Services.logger().log(
        f"Section {section_number}: bridge-tools dispatch "
        f"failed after escalation — writing failure artifact"
    )
    failure_artifact = paths.bridge_tools_failure_signal(section_number)
    Services.artifact_io().write_json(
        failure_artifact,
        {
            "section": section_number,
            "status": "failed",
            "reason": "bridge-tools agent did not produce valid "
            "signal after primary + escalation dispatch",
        },
    )
    Services.artifact_io().write_json(
        paths.post_impl_blocker_signal(section_number),
        {
            "state": SIGNAL_NEEDS_PARENT,
            "detail": (
                "Bridge-tools agent failed to produce valid output "
                "after primary + escalation dispatch. Tool friction "
                "remains unresolved."
            ),
            "needs": "Manual review of tool composition gaps",
            "why_blocked": f"See failure details: {failure_artifact}",
        },
    )
    _update_blocker_rollup(planspace)


def handle_tool_friction(
    *,
    section_number: str,
    section_path: str | Path,
    all_sections: list[Any] | None,
    artifacts: Path,
    tool_registry_path: Path,
    friction_signal_path: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> None:
    """Handle tool-friction signals and dispatch bridge-tools when needed."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)

    if not (_detect_friction(friction_signal_path) and tool_registry_path.exists()):
        return

    Services.logger().log(
        f"Section {section_number}: tooling friction detected — "
        f"dispatching bridge-tools agent"
    )

    result = _dispatch_bridge_agent(
        section_number=section_number, section_path=section_path,
        tool_registry_path=tool_registry_path, paths=paths, policy=policy,
        planspace=planspace, parent=parent, codespace=codespace,
    )
    if result[0] is None:
        return
    bridge_valid, bridge_data, pre_bridge_registry_hash = result

    if bridge_valid:
        _handle_bridge_success(
            bridge_data=bridge_data, section_number=section_number,
            all_sections=all_sections, tool_registry_path=tool_registry_path,
            pre_bridge_registry_hash=pre_bridge_registry_hash, paths=paths,
            artifacts=artifacts, planspace=planspace,
            parent=parent, codespace=codespace,
        )
    else:
        _handle_bridge_failure(
            section_number=section_number, paths=paths, planspace=planspace,
        )

    try:
        Services.artifact_io().write_json(
            friction_signal_path,
            {
                "friction": False,
                "status": "handled",
            },
        )
    except OSError:
        Services.logger().log(
            f"Section {section_number}: could not acknowledge "
            f"friction signal — file write failed"
        )


