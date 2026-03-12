"""Problem-frame validation gate for section-loop runner."""

from __future__ import annotations

from pathlib import Path

from signals.repository.artifact_io import write_json
from proposal.repository.excerpts import exists as excerpt_exists
from staleness.helpers.hashing import file_hash
from orchestrator.path_registry import PathRegistry
from signals.service.communication import _record_traceability, log, mailbox_send
from containers import Services
from dispatch.prompt.writers import write_section_setup_prompt
from signals.service.blockers import _update_blocker_rollup
from implementation.service.reexplore import _write_alignment_surface
from orchestrator.types import Section
from taskrouter import agent_for


def validate_problem_frame(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> str | None:
    """Ensure the problem frame exists, has content, and is tracked."""
    paths = PathRegistry(planspace)
    problem_frame_path = paths.problem_frame(section.number)
    if not problem_frame_path.exists():
        log(f"Section {section.number}: problem frame missing — retrying setup once")
        retry_prompt = write_section_setup_prompt(
            section,
            planspace,
            codespace,
            section.global_proposal_path,
            section.global_alignment_path,
        )
        retry_output = paths.artifacts / f"setup-{section.number}-retry-output.md"
        retry_result = Services.dispatcher().dispatch(
            policy["setup"],
            retry_prompt,
            retry_output,
            planspace,
            parent,
            f"setup-{section.number}-retry",
            codespace=codespace,
            section_number=section.number,
            agent_file=agent_for("proposal.section_setup"),
        )
        if retry_result == "ALIGNMENT_CHANGED_PENDING":
            return None

    if not problem_frame_path.exists():
        log(
            f"Section {section.number}: problem frame still missing after retry "
            "— emitting needs_parent signal",
        )
        _write_problem_frame_signal(
            paths.setup_signal(section.number),
            {
                "state": "needs_parent",
                "detail": (
                    f"Setup agent failed to create problem frame for section "
                    f"{section.number} after 2 attempts. The pipeline requires "
                    f"a problem frame before integration work can begin."
                ),
                "needs": (
                    "Parent must either provide a problem frame or resolve "
                    "why the setup agent cannot produce one."
                ),
                "why_blocked": (
                    "Problem frame is a mandatory gate — without it, the "
                    "pipeline cannot validate that the section is solving the "
                    "right problem."
                ),
            },
        )
        _update_blocker_rollup(planspace)
        mailbox_send(
            planspace,
            parent,
            f"pause:needs_parent:{section.number}:problem frame missing after retry",
        )
        return None

    pf_content = problem_frame_path.read_text(encoding="utf-8").strip()
    if not pf_content:
        log(f"Section {section.number}: problem frame is empty")
        _write_problem_frame_signal(
            paths.setup_signal(section.number),
            {
                "state": "needs_parent",
                "detail": (
                    f"Problem frame for section {section.number} exists but is empty"
                ),
                "needs": (
                    "Parent must ensure the setup agent produces a non-empty "
                    "problem frame."
                ),
                "why_blocked": "Empty problem frame cannot validate problem understanding",
            },
        )
        _update_blocker_rollup(planspace)
        mailbox_send(
            planspace,
            parent,
            f"pause:needs_parent:{section.number}:problem frame empty",
        )
        return None

    log(f"Section {section.number}: problem frame present and validated")
    pf_hash_path = paths.problem_frame_hash(section.number)
    pf_hash_path.parent.mkdir(parents=True, exist_ok=True)
    current_pf_hash = file_hash(problem_frame_path)
    if pf_hash_path.exists():
        prev_pf_hash = pf_hash_path.read_text(encoding="utf-8").strip()
        if prev_pf_hash != current_pf_hash:
            log(
                f"Section {section.number}: problem frame changed — forcing "
                "integration proposal re-run",
            )
            existing_proposal = paths.proposal(section.number)
            if existing_proposal.exists():
                existing_proposal.unlink()
                log(
                    f"Section {section.number}: invalidated existing integration "
                    "proposal due to problem frame change",
                )
    pf_hash_path.write_text(current_pf_hash, encoding="utf-8")

    if (
        excerpt_exists(planspace, section.number, "proposal")
        and excerpt_exists(planspace, section.number, "alignment")
    ):
        log(f"Section {section.number}: setup — excerpts ready")
        _record_traceability(
            planspace,
            section.number,
            f"section-{section.number}-proposal-excerpt.md",
            str(section.global_proposal_path),
            "excerpt extraction from global proposal",
        )
        _record_traceability(
            planspace,
            section.number,
            f"section-{section.number}-alignment-excerpt.md",
            str(section.global_alignment_path),
            "excerpt extraction from global alignment",
        )
        _write_alignment_surface(planspace, section)

    return "ok"


def _write_problem_frame_signal(signal_path: Path, payload: dict) -> None:
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(signal_path, payload)
