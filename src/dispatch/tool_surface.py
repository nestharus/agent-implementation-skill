from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from signals.artifact_io import read_json, write_json
from staleness.hash_service import file_hash
from dispatch.model_policy import resolve
from orchestrator.path_registry import PathRegistry
from dispatch.prompt_safety import write_validated_prompt


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
    policy: dict[str, Any],
    dispatch_agent: Callable[..., Any],
    log: Callable[[str], None],
    update_blocker_rollup: Callable[[Path], None],
) -> int:
    """Load the tool registry, repair if needed, and write the tool surface."""
    pre_tool_total = 0
    if not tool_registry_path.exists():
        return pre_tool_total

    try:
        registry = json.loads(tool_registry_path.read_text(encoding="utf-8"))
        all_tools = registry if isinstance(registry, list) else registry.get("tools", [])
        pre_tool_total = len(all_tools)
        relevant_count = write_tool_surface(
            all_tools,
            section_number,
            tools_available_path,
        )
        if relevant_count:
            log(
                f"Section {section_number}: {relevant_count} "
                f"relevant tools (of {len(all_tools)} total)"
            )
        elif tools_available_path.exists():
            log(
                f"Section {section_number}: removed stale "
                f"tools-available surface (no relevant tools)"
            )
    except (json.JSONDecodeError, ValueError) as exc:
        if tools_available_path.exists():
            tools_available_path.unlink()
            log(
                f"Section {section_number}: removed stale "
                f"tools-available surface (malformed registry)"
            )
        malformed_path = _preserve_tool_registry(tool_registry_path)
        log(
            f"Section {section_number}: tool-registry.json "
            f"malformed ({exc}) — dispatching repair "
            f"(original preserved as {malformed_path.name})"
        )
        repair_prompt = artifacts / f"tool-registry-repair-{section_number}-prompt.md"
        repair_output = artifacts / f"tool-registry-repair-{section_number}-output.md"
        if not write_validated_prompt(
            f"# Task: Repair Tool Registry\n\n"
            f"The tool registry at `{tool_registry_path}` contains "
            f"malformed JSON.\n\nError: {exc}\n\n"
            f"Read the file, reconstruct valid JSON preserving all "
            f"tool entries, and write back to the same path.\n",
            repair_prompt,
        ):
            return pre_tool_total
        dispatch_agent(
            resolve(policy, "tool_registrar"),
            repair_prompt,
            repair_output,
            planspace,
            parent,
            codespace=codespace,
            section_number=section_number,
            agent_file="tool-registrar.md",
        )
        registry = read_json(tool_registry_path)
        if registry is not None:
            all_tools = registry if isinstance(registry, list) else registry.get("tools", [])
            pre_tool_total = len(all_tools)
            log(
                f"Section {section_number}: tool registry "
                f"repaired ({len(all_tools)} tools)"
            )
            relevant_count = write_tool_surface(
                all_tools,
                section_number,
                tools_available_path,
            )
            if relevant_count:
                log(
                    f"Section {section_number}: rebuilt tools "
                    f"surface ({relevant_count} relevant tools)"
                )
        else:
            log(
                f"Section {section_number}: tool registry "
                f"repair failed — writing blocker signal"
            )
            blocker = {
                "state": "needs_parent",
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
            write_json(
                PathRegistry(planspace).blocker_signal(section_number),
                blocker,
            )
            update_blocker_rollup(planspace)

    return pre_tool_total


def validate_tool_registry_after_implementation(
    *,
    section_number: str,
    pre_tool_total: int,
    tool_registry_path: Path,
    artifacts: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
    policy: dict[str, Any],
    dispatch_agent: Callable[..., Any],
    log: Callable[[str], None],
    update_blocker_rollup: Callable[[Path], None],
) -> Path:
    """Validate the tool registry after implementation and return the friction path."""
    friction_signal_path = (
        artifacts / "signals" / f"section-{section_number}-tool-friction.json"
    )
    if not tool_registry_path.exists():
        return friction_signal_path

    try:
        post_registry = json.loads(tool_registry_path.read_text(encoding="utf-8"))
        post_tools = (
            post_registry if isinstance(post_registry, list)
            else post_registry.get("tools", [])
        )
        if len(post_tools) > pre_tool_total:
            log(
                f"Section {section_number}: new tools registered — "
                f"dispatching tool-registrar for validation"
            )
            registrar_prompt = artifacts / f"tool-registrar-{section_number}-prompt.md"
            write_validated_prompt(
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
                f"`{artifacts / 'tool-digest.md'}`\n"
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
                f"If no friction detected, do NOT write a friction signal file.\n",
                registrar_prompt,
            )
            registrar_output = artifacts / f"tool-registrar-{section_number}-output.md"
            dispatch_agent(
                resolve(policy, "tool_registrar"),
                registrar_prompt,
                registrar_output,
                planspace,
                parent,
                f"tool-registrar-{section_number}",
                codespace=codespace,
                agent_file="tool-registrar.md",
                section_number=section_number,
            )
    except (json.JSONDecodeError, ValueError) as exc:
        malformed_path = _preserve_tool_registry(tool_registry_path)
        log(
            f"Section {section_number}: post-impl registry "
            f"malformed ({exc}) — dispatching repair "
            f"(original preserved as {malformed_path.name})"
        )
        repair_prompt = (
            artifacts / f"tool-registry-post-repair-{section_number}-prompt.md"
        )
        repair_output = (
            artifacts / f"tool-registry-post-repair-{section_number}-output.md"
        )
        write_validated_prompt(
            f"# Task: Repair Tool Registry (Post-Implementation)\n\n"
            f"The tool registry at `{tool_registry_path}` became "
            f"malformed after section {section_number} implementation.\n\n"
            f"Error: {exc}\n\n"
            f"Read the file, reconstruct valid JSON preserving all "
            f"tool entries, and write back to the same path.\n",
            repair_prompt,
        )
        dispatch_agent(
            resolve(policy, "tool_registrar"),
            repair_prompt,
            repair_output,
            planspace,
            parent,
            codespace=codespace,
            section_number=section_number,
            agent_file="tool-registrar.md",
        )
        if read_json(tool_registry_path) is not None:
            log(f"Section {section_number}: post-impl tool registry repaired")
        else:
            log(
                f"Section {section_number}: post-impl tool "
                f"registry repair failed — writing blocker"
            )
            blocker = {
                "state": "needs_parent",
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
            write_json(
                PathRegistry(planspace).post_impl_blocker_signal(section_number),
                blocker,
            )
            update_blocker_rollup(planspace)

    return friction_signal_path


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
    policy: dict[str, Any],
    dispatch_agent: Callable[..., Any],
    log: Callable[[str], None],
    write_consequence_note: Callable[[Path, str, str, str], None],
    update_blocker_rollup: Callable[[Path], None],
) -> None:
    """Handle tool-friction signals and dispatch bridge-tools when needed."""
    tool_friction_detected = False
    if friction_signal_path.exists():
        friction = read_json(friction_signal_path)
        if friction is not None:
            tool_friction_detected = friction.get("friction", False)
        else:
            tool_friction_detected = True

    if not (tool_friction_detected and tool_registry_path.exists()):
        return

    log(
        f"Section {section_number}: tooling friction detected — "
        f"dispatching bridge-tools agent"
    )
    bridge_tools_prompt = artifacts / f"bridge-tools-{section_number}-prompt.md"
    bridge_tools_output = artifacts / f"bridge-tools-{section_number}-output.md"
    bridge_signal_path = (
        artifacts / "signals" / f"section-{section_number}-tool-bridge.json"
    )
    default_proposal_path = (
        artifacts / "proposals" / f"section-{section_number}-tool-bridge.md"
    )
    if not write_validated_prompt(
        f"""# Task: Bridge Tool Islands for Section {section_number}

## Context
Section {section_number} has signaled tooling friction — tools don't compose
cleanly or a needed tool doesn't exist.

## Files to Read
1. Tool registry: `{tool_registry_path}`
2. Section specification: `{section_path}`
3. Integration proposal: `{artifacts / "proposals" / f"section-{section_number}-integration-proposal.md"}`

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
""",
        bridge_tools_prompt,
    ):
        return

    pre_bridge_registry_hash = ""
    if tool_registry_path.exists():
        pre_bridge_registry_hash = file_hash(tool_registry_path)

    dispatch_agent(
        resolve(policy, "bridge_tools"),
        bridge_tools_prompt,
        bridge_tools_output,
        planspace,
        parent,
        f"bridge-tools-{section_number}",
        codespace=codespace,
        agent_file="bridge-tools.md",
        section_number=section_number,
    )

    bridge_valid = False
    bridge_data = read_json(bridge_signal_path)
    if bridge_data is not None:
        if bridge_data.get("status") in ("bridged", "no_action", "needs_parent"):
            proposal_path = Path(
                bridge_data.get("proposal_path", str(default_proposal_path))
            )
            if bridge_data["status"] == "no_action" or proposal_path.exists():
                bridge_valid = True

    if not bridge_valid:
        log(
            f"Section {section_number}: bridge signal missing or "
            f"invalid — retrying with escalation model"
        )
        escalation_output = (
            artifacts / f"bridge-tools-{section_number}-escalation-output.md"
        )
        dispatch_agent(
            resolve(policy, "escalation_model"),
            bridge_tools_prompt,
            escalation_output,
            planspace,
            parent,
            f"bridge-tools-{section_number}-escalation",
            codespace=codespace,
            agent_file="bridge-tools.md",
            section_number=section_number,
        )
        bridge_data = read_json(bridge_signal_path)
        if bridge_data is not None:
            if bridge_data.get("status") in ("bridged", "no_action", "needs_parent"):
                proposal_path = Path(
                    bridge_data.get("proposal_path", str(default_proposal_path))
                )
                if bridge_data["status"] == "no_action" or proposal_path.exists():
                    bridge_valid = True

    if bridge_valid:
        bridge_proposal = bridge_data.get("proposal_path", str(default_proposal_path))
        inputs_dir = artifacts / "inputs" / f"section-{section_number}"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        ref_file = inputs_dir / "tool-bridge.ref"
        ref_file.write_text(str(bridge_proposal), encoding="utf-8")
        log(f"Section {section_number}: bridge proposal registered as input ref")

        targets = bridge_data.get("targets", [])
        broadcast = bridge_data.get("broadcast", False)
        note_md = bridge_data.get("note_markdown", "")
        if note_md and (targets or broadcast):
            if broadcast and all_sections:
                targets = [section.number for section in all_sections if section.number != section_number]
            for target in targets:
                write_consequence_note(
                    planspace,
                    f"bridge-{section_number}",
                    str(target),
                    f"# Bridge Note from Section {section_number}\n\n"
                    f"{note_md}\n\n"
                    f"See full proposal: `{bridge_proposal}`\n",
                )
            if targets:
                log(
                    f"Section {section_number}: bridge notes routed "
                    f"to {len(targets)} section(s)"
                )

        post_bridge_registry_hash = ""
        if tool_registry_path.exists():
            post_bridge_registry_hash = file_hash(tool_registry_path)
        if post_bridge_registry_hash and pre_bridge_registry_hash != post_bridge_registry_hash:
            log(
                f"Section {section_number}: tool registry modified "
                f"by bridge-tools — regenerating digest"
            )
            digest_prompt = artifacts / f"tool-digest-regen-{section_number}-prompt.md"
            digest_output = artifacts / f"tool-digest-regen-{section_number}-output.md"
            write_validated_prompt(
                f"# Task: Regenerate Tool Digest\n\n"
                f"The tool registry at `{tool_registry_path}` was "
                f"modified by bridge-tools for section "
                f"{section_number}.\n\n"
                f"Read the registry and write an updated tool digest "
                f"to: `{artifacts / 'tool-digest.md'}`\n\n"
                f"Format: one line per tool grouped by scope "
                f"(cross-section, section-local, test-only).\n",
                digest_prompt,
            )
            dispatch_agent(
                resolve(policy, "tool_registrar"),
                digest_prompt,
                digest_output,
                planspace,
                parent,
                f"tool-digest-regen-{section_number}",
                codespace=codespace,
                section_number=section_number,
                agent_file="tool-registrar.md",
            )
    else:
        log(
            f"Section {section_number}: bridge-tools dispatch "
            f"failed after escalation — writing failure artifact"
        )
        failure_artifact = (
            artifacts / "signals" / f"section-{section_number}-bridge-tools-failure.json"
        )
        write_json(
            failure_artifact,
            {
                "section": section_number,
                "status": "failed",
                "reason": "bridge-tools agent did not produce valid "
                "signal after primary + escalation dispatch",
            },
        )
        write_json(
            PathRegistry(planspace).post_impl_blocker_signal(section_number),
            {
                "state": "needs_parent",
                "detail": (
                    "Bridge-tools agent failed to produce valid output "
                    "after primary + escalation dispatch. Tool friction "
                    "remains unresolved."
                ),
                "needs": "Manual review of tool composition gaps",
                "why_blocked": f"See failure details: {failure_artifact}",
            },
        )
        update_blocker_rollup(planspace)

    try:
        write_json(
            friction_signal_path,
            {
                "friction": False,
                "status": "handled",
            },
        )
    except OSError:
        log(
            f"Section {section_number}: could not acknowledge "
            f"friction signal — file write failed"
        )


def _preserve_tool_registry(tool_registry_path: Path) -> Path:
    malformed_path = tool_registry_path.with_suffix(".malformed.json")
    try:
        shutil.copy2(tool_registry_path, malformed_path)
    except OSError:
        pass
    return malformed_path
