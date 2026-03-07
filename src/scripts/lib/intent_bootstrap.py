"""Intent bootstrap helpers for section-loop runner."""

from __future__ import annotations

from pathlib import Path

from lib.alignment_change_tracker import check_pending as alignment_changed_pending
from lib.artifact_io import read_json, write_json
from lib.path_registry import PathRegistry
from section_loop.communication import _record_traceability, log
from section_loop.intent import (
    ensure_global_philosophy,
    generate_intent_pack,
    run_intent_triage,
)
from section_loop.types import Section


def run_intent_bootstrap(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    incoming_notes: str | None,
) -> dict | None:
    """Run intent triage, TODO surfacing, philosophy, and budget assembly."""
    del policy

    artifacts = PathRegistry(planspace).artifacts
    problem_frame_path = (
        artifacts / "sections" / f"section-{section.number}-problem-frame.md"
    )
    pf_content = (
        problem_frame_path.read_text(encoding="utf-8").strip()
        if problem_frame_path.exists()
        else ""
    )

    notes_count = 0
    notes_dir_check = artifacts / "notes"
    if notes_dir_check.exists():
        notes_count = len(list(notes_dir_check.glob(f"from-*-to-{section.number}.md")))

    triage_result = run_intent_triage(
        section.number,
        planspace,
        codespace,
        parent,
        related_files_count=len(section.related_files),
        incoming_notes_count=notes_count,
        solve_count=section.solve_count,
        section_summary=pf_content[:500] if pf_content else "",
    )
    intent_mode = triage_result.get("intent_mode", "lightweight")
    intent_budgets = triage_result.get("budgets", {})

    todos_path = artifacts / "todos" / f"section-{section.number}-todos.md"
    if section.related_files:
        todos_path.parent.mkdir(parents=True, exist_ok=True)
        todo_entries = _extract_todos_from_files(codespace, section.related_files)
        if todo_entries:
            todos_path.write_text(todo_entries, encoding="utf-8")
            log(f"Section {section.number}: extracted TODOs from related files")
            _record_traceability(
                planspace,
                section.number,
                f"section-{section.number}-todos.md",
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        elif todos_path.exists():
            todos_path.unlink()
            log(
                f"Section {section.number}: removed stale TODO extraction "
                "(no TODOs remaining)",
            )
            _record_traceability(
                planspace,
                section.number,
                f"section-{section.number}-todos.md",
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        else:
            log(f"Section {section.number}: no TODOs found in related files")

    philosophy_result = ensure_global_philosophy(planspace, codespace, parent)
    if alignment_changed_pending(planspace):
        return None

    if philosophy_result is None:
        log(
            f"Section {section.number}: philosophy unavailable — blocking "
            "section (project-level invariant)",
        )
        write_json(
            artifacts / "signals" / f"philosophy-blocker-{section.number}.json",
            {
                "section": section.number,
                "blocker": "philosophy_unavailable",
                "reason": (
                    "Global philosophy could not be established. Section "
                    "execution blocked until resolved."
                ),
            },
        )
        return None

    if intent_mode == "full":
        generate_intent_pack(
            section,
            planspace,
            codespace,
            parent,
            incoming_notes=incoming_notes or "",
        )
        if alignment_changed_pending(planspace):
            return None
        log(f"Section {section.number}: intent bootstrap complete (full mode)")

    if intent_mode == "lightweight":
        log(f"Section {section.number}: lightweight intent mode")

    if intent_budgets:
        triage_budget_keys = frozenset(("proposal_max", "implementation_max"))
        cycle_budget_path = (
            artifacts / "signals" / f"section-{section.number}-cycle-budget.json"
        )
        existing_budget = read_json(cycle_budget_path)
        if existing_budget is not None:
            existing_budget.update({
                key: value
                for key, value in intent_budgets.items()
                if (
                    key.startswith("intent_")
                    or key.startswith("max_new_")
                    or key in triage_budget_keys
                )
            })
            write_json(cycle_budget_path, existing_budget)

    cycle_budget_path = (
        artifacts / "signals" / f"section-{section.number}-cycle-budget.json"
    )
    cycle_budget = {"proposal_max": 5, "implementation_max": 5}
    loaded_budget = read_json(cycle_budget_path)
    if loaded_budget is not None:
        cycle_budget.update(loaded_budget)
    return cycle_budget


def _extract_todos_from_files(codespace: Path, related_files: list[str]) -> str:
    from section_loop.section_engine.todos import (
        _extract_todos_from_files as extract_todos,
    )

    return extract_todos(codespace, related_files)
