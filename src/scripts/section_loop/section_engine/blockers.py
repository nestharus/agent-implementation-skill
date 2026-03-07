from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import read_json, rename_malformed
from lib.core.path_registry import PathRegistry


def _append_open_problem(
    planspace: Path, section_number: str,
    problem: str, source: str,
) -> None:
    """Append an open problem to the section's spec file.

    Open problems are first-class artifacts — any agent (scan, proposal,
    implementation) can surface them. They represent issues that could not
    be resolved at the current level and need upward routing.
    """
    sec_file = PathRegistry(planspace).section_spec(section_number)
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
    paths = PathRegistry(planspace)
    signals_dir = paths.signals_dir()

    blockers: list[dict] = []
    if signals_dir.exists():
        for sig_path in sorted(signals_dir.glob("*-signal.json")):
            data = read_json(sig_path)
            if data is not None:
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
            else:
                blockers.append({
                    "signal_file": sig_path.name,
                    "state": "malformed",
                    "category": "malformed_signal",
                    "section": "unknown",
                    "detail": (
                        f"Signal file {sig_path.name} could not be parsed "
                        "or read; fix or regenerate this signal."
                    ),
                    "needs": "Valid signal JSON",
                    "why_blocked": "Signal JSON unreadable",
                })
                # Preserve corrupted signal for diagnosis (V5/R55)
                rename_malformed(sig_path)
                continue

    # Collect proposal-state blockers from readiness artifacts
    readiness_dir = paths.readiness_dir()
    if readiness_dir and readiness_dir.exists():
        for rdy_path in sorted(readiness_dir.glob(
                "section-*-execution-ready.json")):
            rdy = read_json(rdy_path)
            if rdy is None:
                continue
            if rdy.get("ready"):
                continue
            for b in rdy.get("blockers", []):
                btype = b.get("type", "unknown")
                desc = b.get("description", "")
                # Map proposal-state blocker types to categories
                if btype == "user_root_questions":
                    category = "decision_required"
                elif btype == "unresolved_contracts":
                    category = "dependency"
                elif btype == "unresolved_anchors":
                    category = "dependency"
                elif btype == "shared_seam_candidates":
                    category = "scope_expansion"
                else:
                    category = "missing_info"
                # Extract section number from filename
                sec_match = rdy_path.stem.replace(
                    "section-", "").replace(
                    "-execution-ready", "")
                blockers.append({
                    "signal_file": rdy_path.name,
                    "state": f"proposal-state:{btype}",
                    "category": category,
                    "section": sec_match,
                    "detail": desc,
                    "needs": "",
                    "why_blocked": (
                        f"Proposal-state field '{btype}' has "
                        f"unresolved items"
                    ),
                })

    if not blockers:
        return

    decisions_dir = paths.decisions_dir()
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
