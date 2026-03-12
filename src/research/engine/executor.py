"""Script-owned research plan execution into flow fanout submissions."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from signals.repository.artifact_io import write_json
from orchestrator.path_registry import PathRegistry
from flow.engine.submitter import new_flow_id, submit_fanout
from staleness.service.freshness import compute_section_freshness
from research.engine.orchestrator import load_research_status, validate_research_plan, write_research_status
from research.prompt.writer import (
    write_research_synthesis_prompt,
    write_research_ticket_prompt,
    write_research_verify_prompt,
)
from containers import Services
from signals.service.blockers import _update_blocker_rollup


def _ordered_ticket_ids(plan: dict) -> list[str]:
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


def _emit_not_researchable_signals(
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
        route = str(item.get("route") or item.get("state") or "needs_parent").strip()
        if route not in {"need_decision", "needs_parent"}:
            route = "needs_parent"
        signal = {
            "state": route,
            "section": section_number,
            "detail": question or f"Planner returned not_researchable[{index}]",
            "needs": (
                "User decision on a non-researchable question"
                if route == "need_decision"
                else "Parent/coordination resolution for a non-researchable blocker"
            ),
            "why_blocked": reason or "Planner marked this question as not researchable",
            "source": "research-plan:not_researchable",
        }
        write_json(
            signals_dir / f"section-{section_number}-research-blocker-{index}.json",
            signal,
        )

    if items:
        _update_blocker_rollup(planspace)


def _build_branch(
    *,
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    ticket: dict,
    ticket_index: int,
) -> BranchSpec | None:
    """Translate one semantic ticket into a concrete branch spec."""
    ticket_id = str(ticket.get("ticket_id", f"T-{ticket_index:02d}"))
    concern_scope = f"section-{section_number}"
    problem_id = f"research-{section_number}-{ticket_id}"
    research_type = str(ticket.get("research_type", "web"))

    if research_type == "web":
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

    if research_type == "code":
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

    if research_type == "both":
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

    return None


def execute_research_plan(
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    plan_output_path: Path,
) -> bool:
    """Translate semantic research plan into flow submissions."""
    paths = PathRegistry(planspace)
    status = load_research_status(section_number, planspace) or {}
    trigger_hash = str(status.get("trigger_hash", ""))
    cycle_id = str(status.get("cycle_id", ""))

    plan = _validate_plan(paths, section_number, trigger_hash, cycle_id, planspace)
    if plan is None:
        return False

    not_researchable = [
        item for item in plan.get("not_researchable", []) if isinstance(item, dict)
    ]
    _emit_not_researchable_signals(section_number, planspace, not_researchable)

    branches = _collect_branches(
        plan, section_number, planspace, codespace, trigger_hash, cycle_id,
    )
    if branches is None:
        return False

    if not branches:
        _fail_status(section_number, planspace, trigger_hash, cycle_id,
                     "planner returned no researchable tickets")
        return bool(not_researchable)

    synthesis_prompt = _write_synthesis(
        section_number, planspace, len(branches), trigger_hash, cycle_id,
    )
    if synthesis_prompt is None:
        return False

    _submit_fanout(
        paths, section_number, planspace, branches, synthesis_prompt,
        plan_output_path, trigger_hash, cycle_id,
    )
    return True


# ---------------------------------------------------------------------------
# Extracted concerns: status validation, branch collection, synthesis,
# freshness computation, fanout submission, status writing
# ---------------------------------------------------------------------------


def _fail_status(
    section_number: str,
    planspace: Path,
    trigger_hash: str,
    cycle_id: str,
    detail: str,
) -> None:
    """Write a failure status entry."""
    write_research_status(
        section_number, planspace, "failed",
        detail=detail, trigger_hash=trigger_hash, cycle_id=cycle_id,
    )


def _validate_plan(
    paths: PathRegistry,
    section_number: str,
    trigger_hash: str,
    cycle_id: str,
    planspace: Path,
) -> dict | None:
    """Validate the research plan, writing a failure status if invalid."""
    plan = validate_research_plan(paths.research_plan(section_number))
    if plan is None:
        _fail_status(section_number, planspace, trigger_hash, cycle_id,
                     "research-plan.json missing or malformed")
    return plan


def _collect_branches(
    plan: dict,
    section_number: str,
    planspace: Path,
    codespace: Path | None,
    trigger_hash: str,
    cycle_id: str,
) -> list[BranchSpec] | None:
    """Build branch specs from ordered tickets.

    Returns ``None`` if any branch fails to build (and writes failure status).
    Returns an empty list if no tickets matched.
    """
    tickets_by_id = {
        str(ticket.get("ticket_id", "")): ticket
        for ticket in plan.get("tickets", [])
        if isinstance(ticket, dict) and str(ticket.get("ticket_id", ""))
    }
    branches: list[BranchSpec] = []

    for ticket_index, ticket_id in enumerate(_ordered_ticket_ids(plan), start=1):
        ticket = tickets_by_id.get(ticket_id)
        if ticket is None:
            continue
        branch = _build_branch(
            section_number=section_number,
            planspace=planspace,
            codespace=codespace,
            ticket=ticket,
            ticket_index=ticket_index,
        )
        if branch is None:
            _fail_status(section_number, planspace, trigger_hash, cycle_id,
                         f"failed to build research branch for {ticket_id}")
            return None
        branches.append(branch)

    return branches


def _write_synthesis(
    section_number: str,
    planspace: Path,
    branch_count: int,
    trigger_hash: str,
    cycle_id: str,
) -> Path | None:
    """Write the synthesis prompt, returning ``None`` on failure."""
    synthesis_prompt = write_research_synthesis_prompt(
        section_number, planspace, branch_count,
    )
    if synthesis_prompt is None:
        _fail_status(section_number, planspace, trigger_hash, cycle_id,
                     "failed to write research synthesis prompt")
    return synthesis_prompt


def _submit_fanout(
    paths: PathRegistry,
    section_number: str,
    planspace: Path,
    branches: list[BranchSpec],
    synthesis_prompt: Path,
    plan_output_path: Path,
    trigger_hash: str,
    cycle_id: str,
) -> None:
    """Write submission status, compute freshness, and submit the fanout."""
    # Write status BEFORE computing freshness so the hash includes
    # research-status.json at both submission and dispatch time.
    write_research_status(
        section_number, planspace, "tickets_submitted",
        detail=f"submitted {len(branches)} research ticket branches",
        trigger_hash=trigger_hash, cycle_id=cycle_id,
    )

    # Compute freshness AFTER all writes (prompts, specs, status) so
    # the token matches what the dispatcher will see.
    post_write_freshness = compute_section_freshness(planspace, section_number)

    flow_id = new_flow_id()
    gate = GateSpec(
        mode="all",
        failure_policy="include",
        synthesis=TaskSpec(
            task_type="research.synthesis",
            concern_scope=f"section-{section_number}",
            payload_path=str(synthesis_prompt),
            problem_id=f"research-{section_number}",
        ),
    )
    origin_refs = [str(paths.research_plan(section_number)), str(plan_output_path)]
    submit_fanout(
        paths.run_db(),
        f"research-{section_number}",
        branches,
        flow_id=flow_id,
        declared_by_task_id=None,
        origin_refs=origin_refs,
        gate=gate,
        planspace=planspace,
        freshness_token=post_write_freshness,
    )


def submit_research_verify(
    section_number: str,
    planspace: Path,
    *,
    db_path: Path,
    declared_by_task_id: int | None,
    origin_refs: list[str] | None = None,
) -> bool:
    """Submit the research verifier as a follow-on task."""
    status = load_research_status(section_number, planspace) or {}
    verify_prompt = write_research_verify_prompt(section_number, planspace)
    if verify_prompt is None:
        write_research_status(
            section_number,
            planspace,
            "failed",
            detail="failed to write research verification prompt",
            trigger_hash=str(status.get("trigger_hash", "")),
            cycle_id=str(status.get("cycle_id", "")),
        )
        return False

    Services.flow_ingestion().submit_chain(
        db_path,
        f"research-{section_number}",
        [
            TaskSpec(
                task_type="research.verify",
                concern_scope=f"section-{section_number}",
                payload_path=str(verify_prompt),
                problem_id=f"research-{section_number}",
            )
        ],
        declared_by_task_id=declared_by_task_id,
        origin_refs=origin_refs or [str(PathRegistry(planspace).research_claims(section_number))],
        planspace=planspace,
    )
    write_research_status(
        section_number,
        planspace,
        "verifying",
        detail="submitted research verification",
        trigger_hash=str(status.get("trigger_hash", "")),
        cycle_id=str(status.get("cycle_id", "")),
    )
    return True
