"""Tool registry validator — post-implementation tool validation.

Public API: ``validate_tool_registry_after_implementation()``.
"""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from signals.service.blocker_manager import update_blocker_rollup
from signals.types import SIGNAL_NEEDS_PARENT

from dispatch.service.tool_surface_writer import extract_tools


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
    planspace: Path,
    parent: str,
    codespace: Path,
) -> None:
    """Dispatch the tool-registrar agent to validate newly registered tools."""
    paths = PathRegistry(planspace)
    tool_registry_path = paths.tool_registry()
    friction_signal_path = paths.tool_friction_signal(section_number)
    artifacts = paths.artifacts
    policy = Services.policies().load(planspace)
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
    planspace: Path,
    parent: str,
    codespace: Path,
) -> None:
    """Dispatch repair for a malformed post-implementation registry."""
    paths = PathRegistry(planspace)
    tool_registry_path = paths.tool_registry()
    artifacts = paths.artifacts
    policy = Services.policies().load(planspace)
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
        update_blocker_rollup(planspace)


def validate_tool_registry_after_implementation(
    *,
    section_number: str,
    pre_tool_total: int,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> Path:
    """Validate the tool registry after implementation and return the friction path."""
    paths = PathRegistry(planspace)
    tool_registry_path = paths.tool_registry()
    friction_signal_path = paths.tool_friction_signal(section_number)
    if not tool_registry_path.exists():
        return friction_signal_path

    post_registry = Services.artifact_io().read_json(tool_registry_path)
    if post_registry is not None:
        post_tools = extract_tools(post_registry)
        if len(post_tools) > pre_tool_total:
            _dispatch_new_tool_validation(
                section_number=section_number,
                planspace=planspace,
                parent=parent,
                codespace=codespace,
            )
    else:
        _dispatch_post_impl_repair(
            section_number=section_number,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
        )

    return friction_signal_path
