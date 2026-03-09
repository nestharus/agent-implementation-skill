"""Research prompt writer — builds runtime prompts for research agents."""

from __future__ import annotations

from pathlib import Path

from lib.core.path_registry import PathRegistry
from prompt_safety import write_validated_prompt


def write_research_plan_prompt(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    trigger_path: Path,
) -> Path | None:
    """Write a self-contained prompt for the research planner agent."""
    paths = PathRegistry(planspace)
    research_dir = paths.research_section_dir(section_number)
    research_dir.mkdir(parents=True, exist_ok=True)

    section_spec = paths.section_spec(section_number)
    problem_frame = paths.problem_frame(section_number)
    proposal_state_path = (
        paths.proposals_dir()
        / f"section-{section_number}-proposal-state.json"
    )
    existing_dossier = paths.research_dossier(section_number)
    codemap = paths.codemap()

    lines = [
        "# Research Planning Prompt",
        "",
        f"## Section: {section_number}",
        "",
        "## Inputs",
        "",
        f"1. Research trigger (blocking questions): `{trigger_path}`",
    ]

    input_num = 2
    for label, path in [
        ("Section spec", section_spec),
        ("Problem frame", problem_frame),
        ("Proposal state", proposal_state_path),
        ("Existing dossier (prior research)", existing_dossier),
        ("Project codemap", codemap),
    ]:
        if path.exists():
            lines.append(f"{input_num}. {label}: `{path}`")
            input_num += 1

    lines.extend([
        "",
        "## Output Paths",
        "",
        f"- Research plan: `{paths.research_plan(section_number)}`",
        f"- Research status: `{paths.research_status(section_number)}`",
        f"- Ticket output directory: `{paths.research_tickets_dir(section_number)}`",
        "",
        "## Task Submission",
        "",
        "You may submit follow-on tasks by writing a JSON array to:",
        f"`{paths.signals_dir() / f'task-requests-research-{section_number}.json'}`",
        "",
        "Allowed task types: `research_domain_ticket`, `research_synthesis`, `research_verify`",
    ])

    if codespace is not None:
        lines.extend([
            "",
            "## Codespace",
            "",
            f"`{codespace}`",
        ])

    prompt_content = "\n".join(lines)
    prompt_path = paths.artifacts / f"research-plan-{section_number}-prompt.md"
    if not write_validated_prompt(prompt_content, prompt_path):
        return None
    return prompt_path
