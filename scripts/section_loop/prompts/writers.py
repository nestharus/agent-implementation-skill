"""Thin prompt-writer functions.

Each function: build context → add prompt-specific keys → load template →
render → write file → log artifact.
"""

from pathlib import Path

from ..alignment import collect_modified_files
from ..communication import DB_SH, _log_artifact, log
from ..cross_section import extract_section_summary
from ..types import Section
from .context import build_prompt_context
from .renderer import load_template, render


# ---------------------------------------------------------------------------
# Instruction helpers (return strings, no file I/O)
# ---------------------------------------------------------------------------

def signal_instructions(signal_path: Path) -> str:
    """Return signal instructions for an agent prompt."""
    tpl = load_template("signal-instructions.md")
    return render(tpl, {"signal_path": signal_path})


def agent_mail_instructions(
    planspace: Path, agent_name: str, monitor_name: str,
) -> str:
    """Return narration-via-mailbox instructions for an agent."""
    mailbox_cmd = (
        f'bash "{DB_SH}" send "{planspace / "run.db"}" '
        f"{agent_name} --from {agent_name}"
    )
    tpl = load_template("mail-instructions.md")
    return render(tpl, {
        "agent_name": agent_name,
        "monitor_name": monitor_name,
        "mailbox_cmd": mailbox_cmd,
    })


# ---------------------------------------------------------------------------
# Prompt-file writers (write .md files, return Path)
# ---------------------------------------------------------------------------

def write_section_setup_prompt(
    section: Section,
    planspace: Path,
    codespace: Path,
    global_proposal: Path,
    global_alignment: Path,
) -> Path:
    """Write the prompt for extracting section-level excerpts from globals."""
    artifacts = planspace / "artifacts"
    sections_dir = artifacts / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    sec = section.number
    a_name = f"setup-{sec}"
    m_name = f"{a_name}-monitor"

    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "global_proposal": global_proposal,
        "global_alignment": global_alignment,
        "proposal_excerpt": (
            sections_dir / f"section-{sec}-proposal-excerpt.md"
        ),
        "alignment_excerpt": (
            sections_dir / f"section-{sec}-alignment-excerpt.md"
        ),
        "problem_frame_path": (
            sections_dir / f"section-{sec}-problem-frame.md"
        ),
        "signal_block": signal_instructions(
            artifacts / "signals" / f"setup-{sec}-signal.json",
        ),
        "mail_block": agent_mail_instructions(planspace, a_name, m_name),
    })

    prompt_path = artifacts / f"setup-{sec}-prompt.md"
    tpl = load_template("section-setup.md")
    prompt_path.write_text(render(tpl, ctx), encoding="utf-8")
    _log_artifact(planspace, f"prompt:setup-{sec}")
    return prompt_path


def write_integration_proposal_prompt(
    section: Section,
    planspace: Path,
    codespace: Path,
    alignment_problems: str | None = None,
    incoming_notes: str | None = None,
    model_policy: dict | None = None,
) -> Path:
    """Write the prompt for creating an integration proposal."""
    artifacts = planspace / "artifacts"
    proposals_dir = artifacts / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    sec = section.number
    a_name = f"intg-proposal-{sec}"
    m_name = f"{a_name}-monitor"

    proposal_excerpt = (
        artifacts / "sections" / f"section-{sec}-proposal-excerpt.md"
    )
    alignment_excerpt = (
        artifacts / "sections" / f"section-{sec}-alignment-excerpt.md"
    )
    integration_proposal = (
        proposals_dir / f"section-{sec}-integration-proposal.md"
    )

    # Write alignment problems to file if present
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

    # Write incoming notes to file if present
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

    _policy = model_policy or {}
    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "proposal_excerpt": proposal_excerpt,
        "alignment_excerpt": alignment_excerpt,
        "integration_proposal": integration_proposal,
        "problems_block": problems_block,
        "existing_note": existing_note,
        "notes_block": notes_block,
        "exploration_model": _policy.get("exploration", "glm"),
        "signal_block": signal_instructions(
            artifacts / "signals" / f"proposal-{sec}-signal.json",
        ),
        "mail_block": agent_mail_instructions(planspace, a_name, m_name),
    })

    prompt_path = artifacts / f"intg-proposal-{sec}-prompt.md"
    tpl = load_template("integration-proposal.md")
    prompt_path.write_text(render(tpl, ctx), encoding="utf-8")
    _log_artifact(planspace, f"prompt:proposal-{sec}")
    return prompt_path


def write_integration_alignment_prompt(
    section: Section, planspace: Path, codespace: Path,
) -> Path:
    """Write the prompt for reviewing the integration proposal."""
    artifacts = planspace / "artifacts"
    sec = section.number

    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "proposal_excerpt": (
            artifacts / "sections" / f"section-{sec}-proposal-excerpt.md"
        ),
        "alignment_excerpt": (
            artifacts / "sections" / f"section-{sec}-alignment-excerpt.md"
        ),
        "integration_proposal": (
            artifacts / "proposals"
            / f"section-{sec}-integration-proposal.md"
        ),
    })

    prompt_path = artifacts / f"intg-align-{sec}-prompt.md"
    tpl = load_template("integration-alignment.md")
    prompt_path.write_text(render(tpl, ctx), encoding="utf-8")
    _log_artifact(planspace, f"prompt:proposal-align-{sec}")
    return prompt_path


def write_strategic_impl_prompt(
    section: Section,
    planspace: Path,
    codespace: Path,
    alignment_problems: str | None = None,
    model_policy: dict | None = None,
) -> Path:
    """Write the prompt for strategic implementation."""
    artifacts = planspace / "artifacts"
    sec = section.number
    a_name = f"impl-{sec}"
    m_name = f"{a_name}-monitor"

    proposal_excerpt = (
        artifacts / "sections" / f"section-{sec}-proposal-excerpt.md"
    )
    alignment_excerpt = (
        artifacts / "sections" / f"section-{sec}-alignment-excerpt.md"
    )
    integration_proposal = (
        artifacts / "proposals" / f"section-{sec}-integration-proposal.md"
    )
    modified_report = artifacts / f"impl-{sec}-modified.txt"

    # Write alignment problems to file if present
    problems_block = ""
    if alignment_problems:
        problems_file = artifacts / f"impl-{sec}-problems.md"
        problems_file.write_text(alignment_problems, encoding="utf-8")
        problems_block = (
            f"\n## Previous Implementation Alignment Problems\n\n"
            f"The alignment check found problems with your previous implementation.\n"
            f"Read them and address ALL of them: `{problems_file}`\n"
        )

    # Decisions block (implementation-specific wording)
    decisions_file = artifacts / "decisions" / f"section-{sec}.md"
    impl_decisions_block = ""
    if decisions_file.exists():
        impl_decisions_block = (
            f"\n## Decisions from Parent (answers to earlier questions)\n\n"
            f"Read decisions: `{decisions_file}`\n"
        )

    # Impl-specific corrections ref (numbered differently from proposal)
    codemap_corrections_path = (
        artifacts / "signals" / "codemap-corrections.json"
    )
    impl_corrections_ref = ""
    if codemap_corrections_path.exists():
        impl_corrections_ref = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    # Impl-specific codemap ref (numbered 7 instead of 5)
    codemap_path = artifacts / "codemap.md"
    impl_codemap_ref = ""
    if codemap_path.exists():
        impl_codemap_ref = (
            f"\n7. Codemap (project understanding): `{codemap_path}`"
        )

    # Impl-specific todos ref (numbered 8)
    todos_path = artifacts / "todos" / f"section-{sec}-todos.md"
    impl_todos_ref = ""
    if todos_path.exists():
        impl_todos_ref = (
            f"\n8. TODO extraction (in-code microstrategies): `{todos_path}`"
        )

    # Impl-specific tools ref (numbered 9)
    tools_path = (
        artifacts / "sections" / f"section-{sec}-tools-available.md"
    )
    impl_tools_ref = ""
    if tools_path.exists():
        impl_tools_ref = (
            f"\n9. Available tools from earlier sections: `{tools_path}`"
        )

    # Tool registry
    tool_registry_path = artifacts / "tool-registry.json"
    friction_signal_path = (
        artifacts / "signals" / f"section-{sec}-tool-friction.json"
    )
    tooling_block = (
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

    # Microstrategy ref (numbered 6 in impl)
    microstrategy_path = (
        artifacts / "proposals" / f"section-{sec}-microstrategy.md"
    )
    impl_micro_ref = ""
    if microstrategy_path.exists():
        impl_micro_ref = (
            f"\n6. Microstrategy (tactical per-file breakdown): "
            f"`{microstrategy_path}`"
        )

    _policy = model_policy or {}
    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "proposal_excerpt": proposal_excerpt,
        "alignment_excerpt": alignment_excerpt,
        "integration_proposal": integration_proposal,
        "modified_report": modified_report,
        "problems_block": problems_block,
        "decisions_block": impl_decisions_block,
        "impl_corrections_ref": impl_corrections_ref,
        "codemap_ref": impl_codemap_ref,
        "todos_ref": impl_todos_ref,
        "impl_tools_ref": impl_tools_ref,
        "tooling_block": tooling_block,
        "micro_ref": impl_micro_ref,
        "exploration_model": _policy.get("exploration", "glm"),
        "delegated_impl_model": _policy.get(
            "implementation", "gpt-5.3-codex-high"),
        "signal_block": signal_instructions(
            artifacts / "signals" / f"impl-{sec}-signal.json",
        ),
        "mail_block": agent_mail_instructions(planspace, a_name, m_name),
    })

    prompt_path = artifacts / f"impl-{sec}-prompt.md"
    tpl = load_template("strategic-implementation.md")
    prompt_path.write_text(render(tpl, ctx), encoding="utf-8")
    _log_artifact(planspace, f"prompt:impl-{sec}")
    return prompt_path


def write_impl_alignment_prompt(
    section: Section, planspace: Path, codespace: Path,
) -> Path:
    """Write the prompt for verifying implementation alignment."""
    artifacts = planspace / "artifacts"
    sec = section.number

    alignment_excerpt = (
        artifacts / "sections" / f"section-{sec}-alignment-excerpt.md"
    )
    proposal_excerpt = (
        artifacts / "sections" / f"section-{sec}-proposal-excerpt.md"
    )
    integration_proposal = (
        artifacts / "proposals" / f"section-{sec}-integration-proposal.md"
    )

    # Collect modified files and union with related files
    all_paths = set(section.related_files) | set(
        collect_modified_files(planspace, section, codespace)
    )
    file_list = []
    for rel_path in sorted(all_paths):
        full_path = codespace / rel_path
        if full_path.exists():
            file_list.append(f"   - `{full_path}`")
    impl_files_block = "\n".join(file_list) if file_list else "   (none)"

    # Alignment surface
    alignment_surface = (
        artifacts / "sections" / f"section-{sec}-alignment-surface.md"
    )
    impl_surface_line = ""
    if alignment_surface.exists():
        impl_surface_line = (
            f"\n6. Alignment surface (read first): `{alignment_surface}`"
        )

    # Codemap for alignment judge
    codemap_path = artifacts / "codemap.md"
    impl_codemap_line = ""
    if codemap_path.exists():
        impl_codemap_line = (
            f"\n7. Project codemap (for context): `{codemap_path}`"
        )

    # Codemap corrections
    codemap_corrections_path = (
        artifacts / "signals" / "codemap-corrections.json"
    )
    impl_corrections_line = ""
    if codemap_corrections_path.exists():
        impl_corrections_line = (
            f"\n   - Codemap corrections (authoritative fixes): "
            f"`{codemap_corrections_path}`"
        )

    # Microstrategy
    microstrategy_path = (
        artifacts / "proposals" / f"section-{sec}-microstrategy.md"
    )
    impl_micro_line = ""
    if microstrategy_path.exists():
        impl_micro_line = (
            f"\n8. Microstrategy (tactical per-file plan): "
            f"`{microstrategy_path}`"
        )

    # TODO extraction
    todo_path = artifacts / "todos" / f"section-{sec}-todos.md"
    impl_todo_line = ""
    if todo_path.exists():
        impl_todo_line = (
            f"\n9. TODO extractions (in-code microstrategies): `{todo_path}`"
        )
    else:
        todos_dir = artifacts / "todos"
        if todos_dir.is_dir() and any(todos_dir.iterdir()):
            log(
                f"Section {sec}: TODO file not found at "
                f"{todo_path} but todos/ directory is non-empty"
            )

    # TODO resolution signal
    todo_resolution_path = (
        artifacts / "signals" / f"section-{sec}-todo-resolution.json"
    )
    impl_todo_resolution_line = ""
    if todo_resolution_path.exists():
        impl_todo_resolution_line = (
            f"\n10. TODO resolution summary: `{todo_resolution_path}`"
        )

    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "proposal_excerpt": proposal_excerpt,
        "alignment_excerpt": alignment_excerpt,
        "integration_proposal": integration_proposal,
        "files_block": impl_files_block,
        "surface_line": impl_surface_line,
        "codemap_line": impl_codemap_line,
        "impl_corrections_line": impl_corrections_line,
        "micro_line": impl_micro_line,
        "todo_line": impl_todo_line,
        "todo_resolution_line": impl_todo_resolution_line,
    })

    prompt_path = artifacts / f"impl-align-{sec}-prompt.md"
    tpl = load_template("implementation-alignment.md")
    prompt_path.write_text(render(tpl, ctx), encoding="utf-8")
    _log_artifact(planspace, f"prompt:impl-align-{sec}")
    return prompt_path
