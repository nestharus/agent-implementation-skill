from __future__ import annotations

from pathlib import Path

from signals.repository.artifact_io import read_json, rename_malformed
from containers import Services
from orchestrator.path_registry import PathRegistry
from signals.types import SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE, SIGNAL_NEED_DECISION


_SHARED_SEAM_PREFIX = (
    "shared seam candidate requires cross-section substrate work:"
)
_SEAM_HASH_LENGTH = 12

# Signal state → blocker category mapping (used in _update_blocker_rollup)
_STATE_TO_CATEGORY: dict[str, str] = {
    "underspecified": "missing_info",
    "underspec": "missing_info",
    SIGNAL_NEED_DECISION: "decision_required",
    SIGNAL_OUT_OF_SCOPE: "scope_expansion",
    "out-of-scope": "scope_expansion",
    SIGNAL_NEEDS_PARENT: SIGNAL_NEEDS_PARENT,
    "dependency": "dependency",
}

# Proposal-state blocker type → category mapping
_BTYPE_TO_CATEGORY: dict[str, str] = {
    "user_root_questions": "decision_required",
    "unresolved_contracts": "dependency",
    "unresolved_anchors": "dependency",
    "shared_seam_candidates": SIGNAL_NEEDS_PARENT,
}


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


def _dedupe_rollup_blockers(blockers: list[dict]) -> list[dict]:
    """Collapse duplicated shared-seam blockers across signal/readiness inputs."""
    deduped: list[dict] = []
    seen_keys: set[str] = set()

    for blocker in blockers:
        source = str(blocker.get("source", ""))
        if source == "proposal-state:shared_seam_candidates":
            detail = str(blocker.get("detail", "")).strip()
            if detail.lower().startswith(_SHARED_SEAM_PREFIX):
                detail = detail[len(_SHARED_SEAM_PREFIX):].strip()
            normalized = " ".join(detail.lower().split())
            seam_key = Services.hasher().content_hash(
                f"{blocker.get('section', 'unknown')}::{normalized}"
            )[:_SEAM_HASH_LENGTH]
            dedupe_key = f"shared-seam::{seam_key}"
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
        deduped.append(blocker)

    return deduped


def _collect_signal_blockers(signals_dir: Path) -> list[dict]:
    """Collect blockers from signal JSON files."""
    blockers: list[dict] = []
    if not signals_dir.exists():
        return blockers
    for sig_path in sorted(signals_dir.glob("*-signal.json")):
        data = read_json(sig_path)
        if data is None:
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
            rename_malformed(sig_path)
            continue
        state = data.get("state", "").lower()
        category = _STATE_TO_CATEGORY.get(state)
        if category is not None:
            blockers.append({
                "signal_file": sig_path.name,
                "state": state,
                "category": category,
                "source": data.get("source", ""),
                "section": data.get("section", "unknown"),
                "detail": data.get("detail", ""),
                "needs": data.get("needs", ""),
                "why_blocked": data.get("why_blocked", ""),
            })
    return blockers


def _collect_readiness_blockers(readiness_dir: Path | None) -> list[dict]:
    """Collect blockers from readiness artifacts."""
    blockers: list[dict] = []
    if not readiness_dir or not readiness_dir.exists():
        return blockers
    for rdy_path in sorted(readiness_dir.glob("section-*-execution-ready.json")):
        rdy = read_json(rdy_path)
        if rdy is None or rdy.get("ready"):
            continue
        sec_match = rdy_path.stem.replace("section-", "").replace("-execution-ready", "")
        for b in rdy.get("blockers", []):
            btype = b.get("type") or b.get("state", "unknown")
            category = _BTYPE_TO_CATEGORY.get(btype)
            if category is None:
                category = "governance" if btype.startswith("governance_") else "missing_info"
            blockers.append({
                "signal_file": rdy_path.name,
                "state": b.get("state", f"proposal-state:{btype}"),
                "category": category,
                "source": b.get("source", f"proposal-state:{btype}"),
                "section": sec_match,
                "detail": b.get("description") or b.get("detail", ""),
                "needs": b.get("needs", ""),
                "why_blocked": b.get(
                    "why_blocked",
                    f"Proposal-state field '{btype}' has unresolved items",
                ),
            })
    return blockers


def _update_blocker_rollup(planspace: Path) -> None:
    """Auto-generate a decision-surface rollup from blocker signals.

    Scans for UNDERSPECIFIED/NEED_DECISION/DEPENDENCY/OUT_OF_SCOPE/
    NEEDS_PARENT signals across sections and writes a consolidated
    needs-input.md for the parent. Blockers are grouped by category:
    missing_info, decision_required, dependency, scope_expansion,
    needs_parent.
    """
    paths = PathRegistry(planspace)
    blockers = _collect_signal_blockers(paths.signals_dir())
    blockers.extend(_collect_readiness_blockers(paths.readiness_dir()))

    blockers = _dedupe_rollup_blockers(blockers)

    if not blockers:
        return

    decisions_dir = paths.decisions_dir()
    decisions_dir.mkdir(parents=True, exist_ok=True)
    rollup_path = decisions_dir / "needs-input.md"

    # Group blockers by category
    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list, {
        "missing_info": [],
        "decision_required": [],
        "dependency": [],
        "scope_expansion": [],
        SIGNAL_NEEDS_PARENT: [],
        "malformed_signal": [],
        "governance": [],
    })
    for b in blockers:
        groups[b["category"]].append(b)

    category_titles = {
        "missing_info": "Missing Information (UNDERSPECIFIED)",
        "decision_required": "Decisions Required (NEED_DECISION)",
        "dependency": "Dependencies (DEPENDENCY)",
        "scope_expansion": "Scope Expansion (OUT_OF_SCOPE)",
        SIGNAL_NEEDS_PARENT: "Parent Coordination / Decision Required (NEEDS_PARENT)",
        "malformed_signal": "Malformed Signal Files (parse error)",
        "governance": "Governance (GOVERNANCE)",
    }

    lines = ["# Blocker Rollup (auto-generated)\n",
             f"**{len(blockers)} sections need input:**\n"]
    for cat_key in ("missing_info", "decision_required", "dependency",
                    "scope_expansion", SIGNAL_NEEDS_PARENT,
                    "malformed_signal", "governance"):
        cat_blockers = groups[cat_key]
        if not cat_blockers:
            continue
        lines.append(f"# {category_titles[cat_key]}\n")
        for b in cat_blockers:
            if str(b["section"]).lower() == "global":
                heading = "## Global — philosophy bootstrap"
            else:
                heading = f"## Section {b['section']} — {b['state']}"
            lines.append(heading)
            lines.append(f"- **Detail**: {b['detail']}")
            if b["why_blocked"]:
                lines.append(f"- **Why blocked**: {b['why_blocked']}")
            if b["needs"]:
                lines.append(f"- **Needs**: {b['needs']}")
            lines.append(f"- **Signal file**: `{b['signal_file']}`")
            lines.append("")
    rollup_path.write_text("\n".join(lines), encoding="utf-8")
