"""Thin prompt-writer functions.

Each function: build context → add prompt-specific keys → load template →
render → write file → log artifact.
"""

from pathlib import Path

from dispatch.prompt.context_assembler import (
    build_impl_context_extras,
    build_proposal_context_extras,
)
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.helpers import (
    agent_mail_instructions,
    format_existing_file_listing,
    scoped_context_block,
    signal_instructions,
)

from dispatch.service.prompt_safety import validate_dynamic_content
from dispatch.service.prompt_safety import write_validated_prompt
from staleness.service.section_alignment import collect_modified_files
from signals.service.communication import WORKFLOW_HOME, _log_artifact, log
from orchestrator.service.context_assembly import materialize_context_sidecar
from orchestrator.types import Section
from dispatch.prompt.context import build_prompt_context
from dispatch.prompt.template import load_template, render


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
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sections_dir = paths.sections_dir()
    sections_dir.mkdir(parents=True, exist_ok=True)
    sec = section.number
    a_name = f"setup-{sec}"
    m_name = f"{a_name}-monitor"

    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "global_proposal": global_proposal,
        "global_alignment": global_alignment,
        "proposal_excerpt": paths.proposal_excerpt(sec),
        "alignment_excerpt": paths.alignment_excerpt(sec),
        "problem_frame_path": paths.problem_frame(sec),
        "signal_block": signal_instructions(
            artifacts / "signals" / f"setup-{sec}-signal.json",
        ),
        "mail_block": agent_mail_instructions(planspace, a_name, m_name),
    })

    prompt_path = artifacts / f"setup-{sec}-prompt.md"
    tpl = load_template("section-setup.md")
    if not write_validated_prompt(render(tpl, ctx), prompt_path):
        log(f"  ERROR: prompt {prompt_path.name} blocked — template violations")
        return None
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
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    proposals_dir = paths.proposals_dir()
    proposals_dir.mkdir(parents=True, exist_ok=True)
    sec = section.number
    a_name = f"intg-proposal-{sec}"
    m_name = f"{a_name}-monitor"

    proposal_excerpt = paths.proposal_excerpt(sec)
    alignment_excerpt = paths.alignment_excerpt(sec)
    integration_proposal = paths.proposal(sec)
    proposal_state_path = paths.proposal_state(sec)

    ctx = build_prompt_context(section, planspace, codespace)
    ctx.update({
        "proposal_excerpt": proposal_excerpt,
        "alignment_excerpt": alignment_excerpt,
        "integration_proposal": integration_proposal,
        "proposal_state_path": proposal_state_path,
        "task_submission_path": str(
            artifacts / "signals" / f"task-requests-proposal-{sec}.json"),
        "allowed_tasks": (
            "scan_explore, impact_analysis, "
            "integration_proposal, research_plan"
        ),
        "signal_block": signal_instructions(
            artifacts / "signals" / f"proposal-{sec}-signal.json",
        ),
        "mail_block": agent_mail_instructions(planspace, a_name, m_name),
    })
    ctx.update(
        build_proposal_context_extras(
            section,
            planspace,
            alignment_problems,
            incoming_notes,
            base_context=ctx,
        )
    )

    # Research context (dossier + addendum from research flow)
    research_addendum = paths.research_addendum(sec)
    research_dossier = paths.research_dossier(sec)
    research_ref = ""
    if research_addendum.exists():
        research_ref += (
            f"\n   - Research addendum (domain knowledge): "
            f"`{research_addendum}`"
        )
    if research_dossier.exists():
        research_ref += (
            f"\n   - Research dossier (full findings): "
            f"`{research_dossier}`"
        )
    ctx["research_ref"] = research_ref

    # Materialize sidecar BEFORE rendering so it exists at prompt-write time
    sidecar_path = materialize_context_sidecar(
        str(Path(WORKFLOW_HOME) / "agents" / "integration-proposer.md"),
        planspace, section=section.number,
    )

    prompt_path = artifacts / f"intg-proposal-{sec}-prompt.md"
    tpl = load_template("integration-proposal.md")
    rendered = render(tpl, ctx)

    # V6: Validate dynamic content — violations block dispatch
    violations = validate_dynamic_content(rendered)
    if violations:
        log(f"  ERROR: prompt {prompt_path.name} blocked — template "
            f"violations: {violations}")
        return None

    prompt_path.write_text(rendered, encoding="utf-8")

    # Append context sidecar reference (materialized before rendering)
    if sidecar_path:
        with prompt_path.open("a", encoding="utf-8") as f:
            f.write(scoped_context_block(sidecar_path))

    _log_artifact(planspace, f"prompt:proposal-{sec}")
    return prompt_path


def write_integration_alignment_prompt(
    section: Section, planspace: Path, codespace: Path,
) -> Path:
    """Write the prompt for reviewing the integration proposal."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sec = section.number

    ctx = build_prompt_context(section, planspace, codespace)

    # Intent surfaces output path (for intent-judge in full mode).
    # Conditional: only add the block when intent pack exists.
    intent_surfaces_block = ""
    intent_pack = paths.intent_section_dir(sec) / "problem.md"
    if intent_pack.exists():
        surfaces_path = paths.intent_surfaces_signal(sec)
        intent_surfaces_block = (
            f"## Surfaces Signal Output\n\n"
            f"If you discover intent surfaces during alignment checking, "
            f"write them to:\n`{surfaces_path}`\n"
        )

    # Proposal-state artifact (machine-readable problem state)
    proposal_state_path = paths.proposal_state(sec)
    proposal_state_line = ""
    if proposal_state_path.exists():
        proposal_state_line = (
            f"\n5. Proposal-state artifact (machine-readable problem state): "
            f"`{proposal_state_path}`"
        )

    # Governance packet reference
    governance_packet_path = paths.governance_packet(sec)
    governance_packet_line = ""
    if governance_packet_path.exists():
        governance_packet_line = (
            f"\n6. Governance packet (applicable problems/patterns/profile): "
            f"`{governance_packet_path}`"
        )

    ctx.update({
        "proposal_excerpt": paths.proposal_excerpt(sec),
        "alignment_excerpt": paths.alignment_excerpt(sec),
        "integration_proposal": paths.proposal(sec),
        "proposal_state_line": proposal_state_line,
        "governance_packet_line": governance_packet_line,
        "intent_surfaces_block": intent_surfaces_block,
    })

    prompt_path = artifacts / f"intg-align-{sec}-prompt.md"
    tpl = load_template("integration-alignment.md")
    if not write_validated_prompt(render(tpl, ctx), prompt_path):
        log(f"  ERROR: prompt {prompt_path.name} blocked — template violations")
        return None
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
    artifacts = PathRegistry(planspace).artifacts
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

    # Proposal-state artifact (what the proposal resolved / left unresolved)
    proposal_state_path = (
        artifacts / "proposals" / f"section-{sec}-proposal-state.json"
    )
    proposal_state_ref = ""
    if proposal_state_path.exists():
        proposal_state_ref = (
            f"\n   - Proposal-state (resolved vs unresolved): "
            f"`{proposal_state_path}`"
        )

    # Reconciliation result (cross-section conflict detection)
    reconciliation_path = (
        artifacts / "reconciliation"
        / f"section-{sec}-reconciliation-result.json"
    )
    reconciliation_ref = ""
    if reconciliation_path.exists():
        reconciliation_ref = (
            f"\n   - Reconciliation result (cross-section conflicts): "
            f"`{reconciliation_path}`"
        )

    # Execution-readiness artifact (blocker summary)
    readiness_path = (
        artifacts / "readiness"
        / f"section-{sec}-execution-ready.json"
    )
    readiness_ref = ""
    if readiness_path.exists():
        readiness_ref = (
            f"\n   - Execution readiness (blocker summary): "
            f"`{readiness_path}`"
        )

    # Research context for implementation
    paths = PathRegistry(planspace)
    research_addendum = paths.research_addendum(sec)
    research_dossier = paths.research_dossier(sec)
    research_impl_ref = ""
    if research_addendum.exists():
        research_impl_ref += (
            f"\n   - Research addendum (domain constraints): "
            f"`{research_addendum}`"
        )
    if research_dossier.exists():
        research_impl_ref += (
            f"\n   - Research dossier (background knowledge): "
            f"`{research_dossier}`"
        )

    ctx = build_prompt_context(section, planspace, codespace)
    impl_extras = build_impl_context_extras(
        section,
        planspace,
        alignment_problems,
        base_context=ctx,
    )
    ctx.update({
        "proposal_excerpt": proposal_excerpt,
        "alignment_excerpt": alignment_excerpt,
        "integration_proposal": integration_proposal,
        "modified_report": modified_report,
        "problems_block": impl_extras["problems_block"],
        "decisions_block": impl_extras["decisions_block"],
        "impl_corrections_ref": impl_extras["corrections_ref"],
        "codemap_ref": impl_extras["codemap_ref"],
        "todos_ref": impl_extras["todos_ref"],
        "impl_tools_ref": impl_extras["tools_ref"],
        "governance_ref": impl_extras["governance_ref"],
        "tooling_block": impl_extras["tooling_block"],
        "micro_ref": impl_micro_ref,
        "proposal_state_ref": proposal_state_ref,
        "reconciliation_ref": reconciliation_ref,
        "readiness_ref": readiness_ref,
        "research_ref": research_impl_ref,
        "task_submission_path": str(
            artifacts / "signals" / f"task-requests-impl-{sec}.json"),
        "allowed_tasks": "scan_explore, scan_deep_analyze, strategic_implementation, alignment_check",
        "signal_block": signal_instructions(
            artifacts / "signals" / f"impl-{sec}-signal.json",
        ),
        "mail_block": agent_mail_instructions(planspace, a_name, m_name),
    })

    # Materialize sidecar BEFORE rendering so it exists at prompt-write time
    sidecar_path = materialize_context_sidecar(
        str(Path(WORKFLOW_HOME) / "agents" / "implementation-strategist.md"),
        planspace, section=section.number,
    )

    prompt_path = artifacts / f"impl-{sec}-prompt.md"
    tpl = load_template("strategic-implementation.md")
    rendered = render(tpl, ctx)

    # V6: Validate dynamic content — violations block dispatch
    violations = validate_dynamic_content(rendered)
    if violations:
        log(f"  ERROR: prompt {prompt_path.name} blocked — template "
            f"violations: {violations}")
        return None

    prompt_path.write_text(rendered, encoding="utf-8")

    # Append context sidecar reference (materialized before rendering)
    if sidecar_path:
        with prompt_path.open("a", encoding="utf-8") as f:
            f.write(scoped_context_block(sidecar_path))

    _log_artifact(planspace, f"prompt:impl-{sec}")
    return prompt_path


def write_impl_alignment_prompt(
    section: Section, planspace: Path, codespace: Path,
) -> Path:
    """Write the prompt for verifying implementation alignment."""
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
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
    impl_files_block = format_existing_file_listing(codespace, all_paths)

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

    # Governance packet reference
    governance_packet_path = paths.governance_packet(sec)
    impl_governance_line = ""
    if governance_packet_path.exists():
        impl_governance_line = (
            f"\n11. Governance packet (applicable problems/patterns/profile): "
            f"`{governance_packet_path}`"
        )

    impl_feedback_path = paths.impl_feedback_surfaces(sec)
    impl_feedback_block = (
        "\n\n## Implementation Feedback Surfaces\n\n"
        "If during your alignment review you discover constraints, "
        "unexpected behaviors, or problem dimensions that the current "
        "problem definition does not cover, write them to:\n"
        f"`{impl_feedback_path}`\n\n"
        "Use the same surfaces schema as intent surfaces:\n"
        '```json\n{"problem_surfaces": [...], "philosophy_surfaces": [...]}'
        "\n```\n"
        "Only write surfaces for genuinely new problem dimensions, not "
        "for implementation quality issues.\n"
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
        "governance_line": impl_governance_line,
        "impl_feedback_block": impl_feedback_block,
    })

    prompt_path = artifacts / f"impl-align-{sec}-prompt.md"
    tpl = load_template("implementation-alignment.md")
    if not write_validated_prompt(render(tpl, ctx), prompt_path):
        log(f"  ERROR: prompt {prompt_path.name} blocked — template violations")
        return None
    _log_artifact(planspace, f"prompt:impl-align-{sec}")
    return prompt_path
