"""Prompt-specific context assembly for section loop prompt writers."""

from __future__ import annotations

from pathlib import Path

from orchestrator.types import Section

from orchestrator.path_registry import PathRegistry


def _compose_context_extras_text(
    tool_registry_path: Path,
    friction_signal_path: Path,
) -> str:
    """Return the tooling block text for implementation context extras."""
    return (
        f"\n## Tooling\n\n"
        f"If you create any new tool/script intended for reuse, you MUST append an\n"
        f"entry to the tool registry at: `{tool_registry_path}`\n"
        f"using the documented schema (id/path/created_by/scope/status/description/\n"
        f"registered_at). If you are unsure a script qualifies as a tool, register it\n"
        f"as `experimental` and note the uncertainty in the description.\n\n"
        f"### Tool Friction Detection\n\n"
        f"If you encounter tool composition friction (tools that don't compose\n"
        f"cleanly, a missing bridge tool, or disconnected tool islands), write a\n"
        f"friction signal to: `{friction_signal_path}`\n\n"
        f"Format:\n"
        f"```json\n"
        f'{{"friction": true, "islands": [["toolA","toolB"]], '
        f'"missing_bridge": "description of what is missing"}}\n'
        f"```\n"
        f"The runner reads this file to dispatch bridge-tool creation.\n"
    )


def build_proposal_context_extras(
    section: Section,
    planspace: Path,
    alignment_problems: str | None,
    incoming_notes: str | None,
    *,
    base_context: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build prompt-specific context keys for integration proposal prompts."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sec = section.number
    integration_proposal = paths.proposal(sec)

    problems_block = ""
    if alignment_problems:
        problems_file = artifacts / f"intg-proposal-{sec}-problems.md"
        problems_file.write_text(alignment_problems, encoding="utf-8")
        problems_block = (
            f"\n## Previous Alignment Problems\n\n"
            f"The alignment check found problems with your previous integration\n"
            f"proposal. Read them and address ALL of them in this revision:\n"
            f"`{problems_file}`\n"
        )

    existing_note = ""
    if integration_proposal.exists():
        existing_note = (
            f"\n## Existing Integration Proposal\n"
            f"There is an existing proposal from a previous round at:\n"
            f"`{integration_proposal}`\n"
            f"Read it and revise it to address the alignment problems above.\n"
        )

    notes_block = ""
    if incoming_notes:
        notes_file = artifacts / f"intg-proposal-{sec}-notes.md"
        notes_file.write_text(incoming_notes, encoding="utf-8")
        notes_block = (
            f"\n## Notes from Other Sections\n\n"
            f"Other sections have completed work that may affect this section. Read\n"
            f"these notes carefully — they describe consequences, contracts, and\n"
            f"interfaces that may constrain or inform your integration strategy:\n"
            f"`{notes_file}`\n"
        )

    return {
        "problems_block": problems_block,
        "existing_note": existing_note,
        "notes_block": notes_block,
        "governance_ref": (
            base_context.get("governance_ref", "") if base_context else ""
        ),
    }


def build_impl_context_extras(
    section: Section,
    planspace: Path,
    alignment_problems: str | None,
    *,
    base_context: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build prompt-specific context keys for implementation prompts."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sec = section.number

    problems_block = ""
    if alignment_problems:
        problems_file = artifacts / f"impl-{sec}-problems.md"
        problems_file.write_text(alignment_problems, encoding="utf-8")
        problems_block = (
            f"\n## Previous Implementation Alignment Problems\n\n"
            f"The alignment check found problems with your previous implementation.\n"
            f"Read them and address ALL of them: `{problems_file}`\n"
        )

    decisions_file = paths.decision_md(sec)
    decisions_block = ""
    if decisions_file.exists():
        decisions_block = (
            f"\n## Decisions from Parent (answers to earlier questions)\n\n"
            f"Read decisions: `{decisions_file}`\n"
        )

    codemap_corrections_path = paths.corrections()
    corrections_ref = ""
    if codemap_corrections_path.exists():
        corrections_ref = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    codemap_path = paths.codemap()
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = f"\n7. Codemap (project understanding): `{codemap_path}`"

    todos_path = paths.todos(sec)
    todos_ref = ""
    if todos_path.exists():
        todos_ref = (
            f"\n8. TODO extraction (in-code microstrategies): `{todos_path}`"
        )

    tools_path = paths.tools_available(sec)
    tools_ref = ""
    if tools_path.exists():
        tools_ref = f"\n9. Available tools from earlier sections: `{tools_path}`"

    tool_registry_path = paths.tool_registry()
    friction_signal_path = paths.tool_friction_signal(sec)
    tooling_block = _compose_context_extras_text(tool_registry_path, friction_signal_path)

    return {
        "problems_block": problems_block,
        "decisions_block": decisions_block,
        "corrections_ref": corrections_ref,
        "codemap_ref": codemap_ref,
        "todos_ref": todos_ref,
        "tools_ref": tools_ref,
        "tooling_block": tooling_block,
        "governance_ref": (
            base_context.get("governance_ref", "") if base_context else ""
        ),
    }
