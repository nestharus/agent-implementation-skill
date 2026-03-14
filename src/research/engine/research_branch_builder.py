"""Branch building — translates semantic tickets into concrete BranchSpec objects.

Public API: ``build_branch()``, ``ordered_ticket_ids()``,
``emit_not_researchable_signals()``.
"""

from __future__ import annotations

from pathlib import Path

from flow.types.schema import BranchSpec, TaskSpec
from orchestrator.path_registry import PathRegistry
from research.prompt.writers import write_research_ticket_prompt
from containers import Services
from signals.service.blocker_manager import _update_blocker_rollup
from signals.types import SIGNAL_NEEDS_PARENT, SIGNAL_NEED_DECISION


def ordered_ticket_ids(plan: dict) -> list[str]:
    """Return ticket IDs in flow order, appending any ungrouped tickets."""
    ordered: list[str] = []
    seen: set[str] = set()

    flow = plan.get("flow", {})
    for group in flow.get("parallel_groups", []):
        if not isinstance(group, list):
            continue
        for ticket_id in group:
            tid = str(ticket_id)
            if tid and tid not in seen:
                ordered.append(tid)
                seen.add(tid)

    for ticket in plan.get("tickets", []):
        if not isinstance(ticket, dict):
            continue
        tid = str(ticket.get("ticket_id", ""))
        if tid and tid not in seen:
            ordered.append(tid)
            seen.add(tid)

    return ordered


def _write_research_scan_prompt(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> Path | None:
    """Write a targeted scan prompt for code-oriented research tickets."""
    paths = PathRegistry(planspace)
    prompt_path = paths.research_scan_prompt(section_number, ticket_index)
    spec_path = paths.research_ticket_spec(section_number, ticket_index)
    lines = [
        "# Research Scan Prompt",
        "",
        f"## Section: {section_number}",
        "",
        "## Inputs",
        "",
        f"1. Ticket spec: `{spec_path}`",
        f"2. Section spec: `{paths.section_spec(section_number)}`",
        f"3. Problem frame: `{paths.problem_frame(section_number)}`",
    ]
    if paths.codemap().exists():
        lines.append(f"4. Project codemap: `{paths.codemap()}`")
    if paths.corrections().exists():
        lines.append(f"5. Codemap corrections (authoritative fixes): `{paths.corrections()}`")
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
            "## Instructions",
            "",
            "Identify the files, interfaces, and subsystem seams needed to answer this research ticket.",
            "Use the codemap and codemap corrections as your routing surface before reading files.",
            "Produce focused scan evidence for the downstream research ticket; do not broaden the scope beyond the ticket questions.",
        ]
    )
    if ticket.get("research_type") == "both":
        lines.append(
            "If flow context includes a previous result manifest from a web stage, use it as context for what must be verified against code."
        )

    if not Services.prompt_guard().write_validated("\n".join(lines), prompt_path):
        return None
    return prompt_path


def emit_not_researchable_signals(
    section_number: str,
    planspace: Path,
    items: list[dict],
) -> None:
    """Route planner-declared non-researchable items into blocker signals."""
    paths = PathRegistry(planspace)
    signals_dir = paths.signals_dir()
    signals_dir.mkdir(parents=True, exist_ok=True)

    for index, item in enumerate(items):
        question = str(item.get("question", "")).strip()
        reason = str(item.get("reason", "")).strip()
        route = str(item.get("route") or item.get("state") or SIGNAL_NEEDS_PARENT).strip()
        if route not in {SIGNAL_NEED_DECISION, SIGNAL_NEEDS_PARENT}:
            route = SIGNAL_NEEDS_PARENT
        signal = {
            "state": route,
            "section": section_number,
            "detail": question or f"Planner returned not_researchable[{index}]",
            "needs": (
                "User decision on a non-researchable question"
                if route == SIGNAL_NEED_DECISION
                else "Parent/coordination resolution for a non-researchable blocker"
            ),
            "why_blocked": reason or "Planner marked this question as not researchable",
            "source": "research-plan:not_researchable",
        }
        Services.artifact_io().write_json(
            signals_dir / f"section-{section_number}-research-blocker-{index}.json",
            signal,
        )

    if items:
        _update_blocker_rollup(planspace)


def _build_web_branch(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> BranchSpec | None:
    ticket_id = str(ticket.get("ticket_id", f"T-{ticket_index:02d}"))
    concern_scope = f"section-{section_number}"
    problem_id = f"research-{section_number}-{ticket_id}"
    prompt_path = write_research_ticket_prompt(
        section_number,
        planspace,
        codespace,
        ticket,
        ticket_index,
    )
    if prompt_path is None:
        return None
    return BranchSpec(
        label=ticket_id,
        chain_ref="research_ticket_package",
        args={
            "concern_scope": concern_scope,
            "payload_path": str(prompt_path),
            "priority": "normal",
            "problem_id": problem_id,
        },
    )


def _build_code_branch(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> BranchSpec | None:
    ticket_id = str(ticket.get("ticket_id", f"T-{ticket_index:02d}"))
    concern_scope = f"section-{section_number}"
    problem_id = f"research-{section_number}-{ticket_id}"
    scan_prompt = _write_research_scan_prompt(
        section_number,
        planspace,
        codespace,
        ticket,
        ticket_index,
    )
    ticket_prompt = write_research_ticket_prompt(
        section_number,
        planspace,
        codespace,
        ticket,
        ticket_index,
    )
    if scan_prompt is None or ticket_prompt is None:
        return None
    return BranchSpec(
        label=ticket_id,
        chain_ref="research_code_ticket_package",
        args={
            "concern_scope": concern_scope,
            "scan_payload_path": str(scan_prompt),
            "payload_path": str(ticket_prompt),
            "priority": "normal",
            "problem_id": problem_id,
        },
    )


def _build_both_branch(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> BranchSpec | None:
    ticket_id = str(ticket.get("ticket_id", f"T-{ticket_index:02d}"))
    concern_scope = f"section-{section_number}"
    problem_id = f"research-{section_number}-{ticket_id}"
    web_ticket = dict(ticket)
    web_ticket["research_type"] = "web"
    web_ticket["output_path"] = str(
        PathRegistry(planspace).research_ticket_result(
            section_number,
            ticket_index,
            "web",
        )
    )
    web_ticket["_phase"] = "web"
    web_prompt = write_research_ticket_prompt(
        section_number,
        planspace,
        codespace,
        web_ticket,
        ticket_index,
    )
    scan_prompt = _write_research_scan_prompt(
        section_number,
        planspace,
        codespace,
        ticket,
        ticket_index,
    )
    final_prompt = write_research_ticket_prompt(
        section_number,
        planspace,
        codespace,
        ticket,
        ticket_index,
    )
    if web_prompt is None or scan_prompt is None or final_prompt is None:
        return None
    return BranchSpec(
        label=ticket_id,
        steps=[
            TaskSpec(
                task_type="research.domain_ticket",
                concern_scope=concern_scope,
                payload_path=str(web_prompt),
                problem_id=problem_id,
            ),
            TaskSpec(
                task_type="scan.explore",
                concern_scope=concern_scope,
                payload_path=str(scan_prompt),
                problem_id=problem_id,
            ),
            TaskSpec(
                task_type="research.domain_ticket",
                concern_scope=concern_scope,
                payload_path=str(final_prompt),
                problem_id=problem_id,
            ),
        ],
    )


def build_branch(
    *,
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> BranchSpec | None:
    """Translate one semantic ticket into a concrete branch spec."""
    research_type = str(ticket.get("research_type", "web"))

    args = (section_number, planspace, codespace, ticket, ticket_index)

    if research_type == "web":
        return _build_web_branch(*args)

    if research_type == "code":
        return _build_code_branch(*args)

    if research_type == "both":
        return _build_both_branch(*args)

    return None
