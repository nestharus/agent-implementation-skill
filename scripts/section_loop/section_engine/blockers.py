import json
from pathlib import Path


def _append_open_problem(
    planspace: Path, section_number: str,
    problem: str, source: str,
) -> None:
    """Append an open problem to the section's spec file.

    Open problems are first-class artifacts — any agent (scan, proposal,
    implementation) can surface them. They represent issues that could not
    be resolved at the current level and need upward routing.
    """
    sec_file = (planspace / "artifacts" / "sections"
                / f"section-{section_number}.md")
    if not sec_file.exists():
        return
    content = sec_file.read_text(encoding="utf-8")
    entry = f"- **[{source}]** {problem}\n"
    if "## Open Problems" in content:
        # Append to existing section
        content = content.replace(
            "## Open Problems\n",
            f"## Open Problems\n{entry}",
        )
    else:
        # Add new section at the end
        content = content.rstrip() + f"\n\n## Open Problems\n{entry}"
    sec_file.write_text(content, encoding="utf-8")


def _update_blocker_rollup(planspace: Path) -> None:
    """Auto-generate a decision-surface rollup from blocker signals.

    Scans for UNDERSPECIFIED/NEED_DECISION/DEPENDENCY/OUT_OF_SCOPE/
    NEEDS_PARENT signals across sections and writes a consolidated
    needs-input.md for the parent. Blockers are grouped by category:
    missing_info, decision_required, dependency, scope_expansion,
    needs_parent.
    """
    signals_dir = planspace / "artifacts" / "signals"
    if not signals_dir.exists():
        return

    blockers: list[dict] = []
    for sig_path in sorted(signals_dir.glob("*-signal.json")):
        try:
            data = json.loads(sig_path.read_text(encoding="utf-8"))
            state = data.get("state", "").lower()
            if state in ("underspecified", "underspec", "need_decision",
                         "dependency", "out_of_scope", "out-of-scope",
                         "needs_parent"):
                # Map state to category
                if state in ("underspecified", "underspec"):
                    category = "missing_info"
                elif state == "need_decision":
                    category = "decision_required"
                elif state in ("out_of_scope", "out-of-scope"):
                    category = "scope_expansion"
                elif state == "needs_parent":
                    category = "needs_parent"
                else:
                    category = "dependency"
                blockers.append({
                    "signal_file": sig_path.name,
                    "state": state,
                    "category": category,
                    "section": data.get("section", "unknown"),
                    "detail": data.get("detail", ""),
                    "needs": data.get("needs", ""),
                    "why_blocked": data.get("why_blocked", ""),
                })
        except (json.JSONDecodeError, OSError) as exc:
            blockers.append({
                "signal_file": sig_path.name,
                "state": "malformed",
                "category": "malformed_signal",
                "section": "unknown",
                "detail": (
                    f"Signal file {sig_path.name} could not be parsed "
                    f"({exc}); fix or regenerate this signal."
                ),
                "needs": "Valid signal JSON",
                "why_blocked": str(exc),
            })
            continue

    if not blockers:
        return

    decisions_dir = planspace / "artifacts" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    rollup_path = decisions_dir / "needs-input.md"

    # Group blockers by category
    groups: dict[str, list[dict]] = {
        "missing_info": [],
        "decision_required": [],
        "dependency": [],
        "scope_expansion": [],
        "needs_parent": [],
        "malformed_signal": [],
    }
    for b in blockers:
        groups[b["category"]].append(b)

    category_titles = {
        "missing_info": "Missing Information (UNDERSPECIFIED)",
        "decision_required": "Decisions Required (NEED_DECISION)",
        "dependency": "Dependencies (DEPENDENCY)",
        "scope_expansion": "Scope Expansion (OUT_OF_SCOPE)",
        "needs_parent": "Parent Decision Required (NEEDS_PARENT)",
        "malformed_signal": "Malformed Signal Files (parse error)",
    }

    lines = ["# Blocker Rollup (auto-generated)\n",
             f"**{len(blockers)} sections need input:**\n"]
    for cat_key in ("missing_info", "decision_required", "dependency",
                    "scope_expansion", "needs_parent",
                    "malformed_signal"):
        cat_blockers = groups[cat_key]
        if not cat_blockers:
            continue
        lines.append(f"# {category_titles[cat_key]}\n")
        for b in cat_blockers:
            lines.append(f"## Section {b['section']} — {b['state']}")
            lines.append(f"- **Detail**: {b['detail']}")
            if b["why_blocked"]:
                lines.append(f"- **Why blocked**: {b['why_blocked']}")
            if b["needs"]:
                lines.append(f"- **Needs**: {b['needs']}")
            lines.append(f"- **Signal file**: `{b['signal_file']}`")
            lines.append("")
    rollup_path.write_text("\n".join(lines), encoding="utf-8")
