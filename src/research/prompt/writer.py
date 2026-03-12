"""Research prompt writer — builds runtime prompts for research agents."""

from __future__ import annotations

from pathlib import Path

from orchestrator.path_registry import PathRegistry
from containers import Services


def _optional_input_lines(
    labelled_paths: list[tuple[str, Path]],
    *,
    start: int = 1,
) -> list[str]:
    """Render numbered input references for files that currently exist."""
    lines: list[str] = []
    input_num = start
    for label, path in labelled_paths:
        if path.exists():
            lines.append(f"{input_num}. {label}: `{path}`")
            input_num += 1
    return lines


def write_research_plan_prompt(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    trigger_path: Path,
) -> Path | None:
    """Write a self-contained prompt for the research planner agent."""
    paths = PathRegistry(planspace)
    paths.research_section_dir(section_number).mkdir(parents=True, exist_ok=True)

    prompt_lines = [
        "# Research Planning Prompt",
        "",
        f"## Section: {section_number}",
        "",
        "You are planning research only. Do not submit follow-on tasks directly.",
        "Write the semantic plan artifact and let scripts translate it into flow submissions.",
        "",
        "## Inputs",
        "",
        f"1. Research trigger (blocking questions): `{trigger_path}`",
    ]

    optional_inputs = _optional_input_lines(
        [
            ("Section spec", paths.section_spec(section_number)),
            ("Problem frame", paths.problem_frame(section_number)),
            ("Proposal state", paths.proposal_state(section_number)),
            ("Existing dossier (prior research)", paths.research_dossier(section_number)),
            ("Project codemap", paths.codemap()),
            ("Codemap corrections (authoritative fixes)", paths.corrections()),
            ("Intent surfaces", paths.intent_surfaces_signal(section_number)),
            (
                "Implementation feedback surfaces",
                paths.impl_feedback_surfaces(section_number),
            ),
            (
                "Existing research-derived surfaces",
                paths.research_derived_surfaces(section_number),
            ),
        ],
        start=2,
    )
    if optional_inputs:
        prompt_lines.extend(optional_inputs)

    if codespace is not None:
        prompt_lines.extend(
            [
                "",
                "## Codespace",
                "",
                f"`{codespace}`",
            ]
        )

    prompt_lines.extend(
        [
            "",
            "## Output Paths",
            "",
            f"- Research plan: `{paths.research_plan(section_number)}`",
            f"- Research status: `{paths.research_status(section_number)}`",
            f"- Ticket directory: `{paths.research_tickets_dir(section_number)}`",
            "",
            "## Planning Notes",
            "",
            "- Use `not_researchable[].route` to classify each blocked item as `need_decision` or `needs_parent`.",
            "- Tickets are semantic only. Scripts choose the concrete queued tasks and payload paths.",
        ]
    )

    prompt_path = paths.research_plan_prompt(section_number)
    if not Services.prompt_guard().write_validated("\n".join(prompt_lines), prompt_path):
        return None
    return prompt_path


def write_research_ticket_prompt(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> Path | None:
    """Write prompt for a single research ticket."""
    paths = PathRegistry(planspace)
    phase = str(ticket.get("_phase", ""))
    spec_path = paths.research_ticket_spec(section_number, ticket_index, phase)
    prompt_path = paths.research_ticket_prompt(section_number, ticket_index, phase)
    result_path = Path(
        str(
            ticket.get(
                "output_path",
                paths.research_ticket_result(section_number, ticket_index, phase),
            )
        )
    )

    ticket_payload = dict(ticket)
    ticket_payload.pop("_phase", None)
    ticket_payload["section"] = section_number
    ticket_payload["output_path"] = str(result_path)
    Services.artifact_io().write_json(spec_path, ticket_payload)

    research_type = str(ticket_payload.get("research_type", "web"))
    phase_note = ""
    if phase == "web":
        phase_note = (
            "This is the web stage of a `both` ticket. Gather source-backed findings "
            "for later synthesis with scan evidence."
        )
    elif research_type in {"code", "both"}:
        phase_note = (
            "Use codemap, codemap corrections, and scan evidence from flow context. "
            "Do not do ad hoc codebase exploration beyond the prepared evidence."
        )

    lines = [
        "# Research Ticket Prompt",
        "",
        f"## Section: {section_number}",
        "",
        "## Inputs",
        "",
        f"1. Ticket spec: `{spec_path}`",
    ]

    optional_inputs = _optional_input_lines(
        [
            ("Section spec", paths.section_spec(section_number)),
            ("Problem frame", paths.problem_frame(section_number)),
            ("Proposal state", paths.proposal_state(section_number)),
            ("Project codemap", paths.codemap()),
            ("Codemap corrections (authoritative fixes)", paths.corrections()),
            ("Intent surfaces", paths.intent_surfaces_signal(section_number)),
            (
                "Implementation feedback surfaces",
                paths.impl_feedback_surfaces(section_number),
            ),
            (
                "Research addendum",
                paths.research_addendum(section_number),
            ),
            ("Research dossier", paths.research_dossier(section_number)),
        ],
        start=2,
    )
    if optional_inputs:
        lines.extend(optional_inputs)

    if codespace is not None:
        lines.extend(
            [
                "",
                "## Codespace",
                "",
                f"`{codespace}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Output Path",
            "",
            f"- Ticket result JSON: `{result_path}`",
        ]
    )
    if phase_note:
        lines.extend(
            [
                "",
                "## Execution Notes",
                "",
                phase_note,
                "If your prompt is wrapped with `<flow-context>`, read the referenced flow context and use any previous result manifest as prepared evidence.",
            ]
        )

    if not Services.prompt_guard().write_validated("\n".join(lines), prompt_path):
        return None
    return prompt_path


def write_research_synthesis_prompt(
    section_number: str,
    planspace: Path,
    ticket_count: int,
) -> Path | None:
    """Write prompt for research synthesis."""
    paths = PathRegistry(planspace)
    prompt_lines = [
        "# Research Synthesis Prompt",
        "",
        f"## Section: {section_number}",
        "",
        "## Inputs",
        "",
        f"1. Research plan: `{paths.research_plan(section_number)}`",
        f"2. Ticket directory ({ticket_count} planned tickets): `{paths.research_tickets_dir(section_number)}`",
        "",
        "If your prompt is wrapped with `<flow-context>`, read the gate aggregate manifest to discover the completed ticket result manifests.",
        "",
        "## Output Paths",
        "",
        f"- Dossier: `{paths.research_dossier(section_number)}`",
        f"- Structured claims: `{paths.research_claims(section_number)}`",
        f"- Research-derived surfaces: `{paths.research_derived_surfaces(section_number)}`",
        f"- Proposal addendum: `{paths.research_addendum(section_number)}`",
    ]

    prompt_path = paths.research_synthesis_prompt(section_number)
    if not Services.prompt_guard().write_validated("\n".join(prompt_lines), prompt_path):
        return None
    return prompt_path


def write_research_verify_prompt(
    section_number: str,
    planspace: Path,
) -> Path | None:
    """Write prompt for research verification."""
    paths = PathRegistry(planspace)
    prompt_lines = [
        "# Research Verification Prompt",
        "",
        f"## Section: {section_number}",
        "",
        "## Inputs",
        "",
        f"1. Research plan: `{paths.research_plan(section_number)}`",
        f"2. Research dossier: `{paths.research_dossier(section_number)}`",
        f"3. Structured dossier claims: `{paths.research_claims(section_number)}`",
        f"4. Ticket directory: `{paths.research_tickets_dir(section_number)}`",
        "",
        "Use `dossier-claims.json` as the authoritative structured claims input rather than re-parsing the dossier markdown.",
        "",
        "## Output Path",
        "",
        f"- Verification report: `{paths.research_verify_report(section_number)}`",
    ]

    prompt_path = paths.research_verify_prompt(section_number)
    if not Services.prompt_guard().write_validated("\n".join(prompt_lines), prompt_path):
        return None
    return prompt_path
