"""Tool bridge agent — handles cross-section tool friction.

Public API: ``handle_tool_friction()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from containers import Services
from orchestrator.path_registry import PathRegistry
from signals.service.blocker_manager import _update_blocker_rollup
from signals.types import SIGNAL_NEEDS_PARENT


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
    planspace, parent, codespace,
):
    paths = PathRegistry(planspace)
    policy = Services.policies().load(planspace)
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
    pre_bridge_registry_hash, planspace, parent,
    codespace,
):
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
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


def _handle_bridge_failure(*, section_number, planspace):
    Services.logger().log(
        f"Section {section_number}: bridge-tools dispatch "
        f"failed after escalation — writing failure artifact"
    )
    paths = PathRegistry(planspace)
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
    tool_registry_path: Path,
    friction_signal_path: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> None:
    """Handle tool-friction signals and dispatch bridge-tools when needed."""
    if not (_detect_friction(friction_signal_path) and tool_registry_path.exists()):
        return

    Services.logger().log(
        f"Section {section_number}: tooling friction detected — "
        f"dispatching bridge-tools agent"
    )

    result = _dispatch_bridge_agent(
        section_number=section_number, section_path=section_path,
        tool_registry_path=tool_registry_path,
        planspace=planspace, parent=parent, codespace=codespace,
    )
    if result[0] is None:
        return
    bridge_valid, bridge_data, pre_bridge_registry_hash = result

    if bridge_valid:
        _handle_bridge_success(
            bridge_data=bridge_data, section_number=section_number,
            all_sections=all_sections, tool_registry_path=tool_registry_path,
            pre_bridge_registry_hash=pre_bridge_registry_hash,
            planspace=planspace,
            parent=parent, codespace=codespace,
        )
    else:
        _handle_bridge_failure(
            section_number=section_number, planspace=planspace,
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
