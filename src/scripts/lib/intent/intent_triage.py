"""Intent triage service."""

from __future__ import annotations

from pathlib import Path

from prompt_safety import write_validated_prompt

from lib.core.path_registry import PathRegistry
from lib.risk.history import read_history
from lib.risk.types import PostureProfile
from section_loop.communication import _log_artifact, log
from section_loop.dispatch import (
    dispatch_agent,
    read_agent_signal,
    read_model_policy,
)


def run_intent_triage(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    related_files_count: int = 0,
    incoming_notes_count: int = 0,
    solve_count: int = 0,
    section_summary: str = "",
) -> dict:
    """Dispatch intent-triager (GLM) and return the triage result.

    Returns a dict with at least ``intent_mode`` and ``budgets``.
    Falls back to full on failure.
    """
    policy = read_model_policy(planspace)
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    signals_dir = paths.signals_dir()
    signals_dir.mkdir(parents=True, exist_ok=True)

    triage_signal_path = signals_dir / f"intent-triage-{section_number}.json"
    triage_prompt_path = artifacts / f"intent-triage-{section_number}-prompt.md"
    triage_output_path = artifacts / f"intent-triage-{section_number}-output.md"

    section_spec = artifacts / "sections" / f"section-{section_number}.md"
    proposal_excerpt = (
        artifacts / "sections" / f"section-{section_number}-proposal-excerpt.md"
    )
    alignment_excerpt = (
        artifacts / "sections" / f"section-{section_number}-alignment-excerpt.md"
    )
    problem_brief = (
        artifacts / "sections" / f"section-{section_number}-problem-frame.md"
    )
    codemap_path = artifacts / "codemap.md"
    codemap_corrections_path = artifacts / "signals" / "codemap-corrections.json"

    triage_refs = []
    for label, path in [
        ("Section spec", section_spec),
        ("Proposal excerpt", proposal_excerpt),
        ("Alignment excerpt", alignment_excerpt),
        ("Problem brief", problem_brief),
        ("Codemap summary", codemap_path),
        ("Codemap corrections (authoritative)", codemap_corrections_path),
    ]:
        if path.exists():
            triage_refs.append(f"- {label}: `{path}`")
    triage_refs_block = "\n".join(triage_refs) if triage_refs else "- (none)"

    triage_prompt_text = f"""# Task: Intent Triage for Section {section_number}

## Context
Decide whether this section needs the full bidirectional intent cycle
(problem + philosophy alignment with surface discovery and expansion)
or lightweight alignment (no fresh intent expansion this cycle; if valid
intent artifacts already exist, alignment may still use intent-judge,
otherwise it falls back to alignment-judge).

## Section Artifacts (read these for grounded assessment)
{triage_refs_block}

## Section Characteristics
- Related files: {related_files_count}
- Incoming cross-section notes: {incoming_notes_count}
- Previous solve attempts: {solve_count}
- Summary: {section_summary[:500] if section_summary else "(none)"}

## Decision Factors

Consider these factors when choosing intent mode:

- **Integration breadth**: How many files and modules does this section touch?
- **Cross-section coupling**: Are there incoming notes or dependencies from other sections?
- **Environment uncertainty**: Are there unresolved related files or missing code references?
  Sections with zero related files have more unknowns to resolve than sections with many.
- **Failure history**: Have prior attempts at this section failed?
- **Risk of hidden constraints**: Does the summary suggest architectural complexity?

Weigh these factors heuristically. Sections that are narrow, well-understood,
and have no failure history lean lightweight. Sections with broad integration,
uncertainty, or prior failures lean full.

## Risk Handoff

- `risk_mode`: your assessment of how much ROAL scrutiny this section
  needs based on the section's problem structure, complexity, and
  history.
- `risk_budget_hint`: extra ROAL iteration budget (0 for simple work,
  2-4 for complex or uncertain work).

## Output
Write a JSON signal to: `{triage_signal_path}`

```json
{{
  "section": "{section_number}",
  "intent_mode": "full"|"lightweight"|"cached",
  "confidence": "high"|"medium"|"low",
  "risk_mode": "skip"|"light"|"full",
  "risk_budget_hint": 0,
  "escalate": false,
  "budgets": {{
    "proposal_max": 5,
    "implementation_max": 5,
    "intent_expansion_max": 2,
    "max_new_surfaces_per_cycle": 8,
    "max_new_axes_total": 6
  }},
  "reason": "<why this mode was chosen>"
}}
```
"""
    if not write_validated_prompt(triage_prompt_text, triage_prompt_path):
        return _augment_risk_hints(
            _full_default(section_number),
            section_number,
            planspace,
            related_files_count=related_files_count,
            incoming_notes_count=incoming_notes_count,
            solve_count=solve_count,
        )
    _log_artifact(planspace, f"prompt:intent-triage-{section_number}")

    result = dispatch_agent(
        policy.get("intent_triage", "glm"),
        triage_prompt_path,
        triage_output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file="intent-triager.md",
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return _augment_risk_hints(
            _full_default(section_number),
            section_number,
            planspace,
            related_files_count=related_files_count,
            incoming_notes_count=incoming_notes_count,
            solve_count=solve_count,
        )

    triage = read_agent_signal(
        triage_signal_path,
        expected_fields=["intent_mode"],
    )
    if triage:
        if triage.get("escalate"):
            log(
                f"Section {section_number}: triage flagged escalation — "
                f"re-dispatching with stronger model",
            )
            escalation_model = policy.get(
                "intent_triage_escalation",
                "claude-opus",
            )
            dispatch_agent(
                escalation_model,
                triage_prompt_path,
                triage_output_path,
                planspace,
                parent,
                codespace=codespace,
                section_number=section_number,
                agent_file="intent-triager.md",
            )
            escalated = read_agent_signal(
                triage_signal_path,
                expected_fields=["intent_mode"],
            )
            if escalated:
                log(
                    f"Section {section_number}: escalated triage → "
                    f"{escalated.get('intent_mode', 'unknown')}",
                )
                return _augment_risk_hints(
                    escalated,
                    section_number,
                    planspace,
                    related_files_count=related_files_count,
                    incoming_notes_count=incoming_notes_count,
                    solve_count=solve_count,
                )

        log(
            f"Section {section_number}: intent triage → "
            f"{triage.get('intent_mode', 'unknown')}",
        )
        return _augment_risk_hints(
            triage,
            section_number,
            planspace,
            related_files_count=related_files_count,
            incoming_notes_count=incoming_notes_count,
            solve_count=solve_count,
        )

    log(
        f"Section {section_number}: intent triage signal missing or "
        f"malformed — defaulting to full (uncertainty → more strategy)",
    )
    return _augment_risk_hints(
        _full_default(section_number),
        section_number,
        planspace,
        related_files_count=related_files_count,
        incoming_notes_count=incoming_notes_count,
        solve_count=solve_count,
    )


def load_triage_result(
    section_number: str,
    planspace: Path,
) -> dict | None:
    """Load a previously-written triage result from signal file."""
    signals_dir = PathRegistry(planspace).signals_dir()
    triage_signal_path = signals_dir / f"intent-triage-{section_number}.json"
    triage = read_agent_signal(
        triage_signal_path,
        expected_fields=["intent_mode"],
    )
    if triage is None:
        return None
    return _augment_risk_hints(triage, section_number, planspace)


def _full_default(section_number: str) -> dict:
    """Default to full mode on triage failure."""
    return {
        "section": section_number,
        "intent_mode": "full",
        "confidence": "low",
        "budgets": {
            "proposal_max": 5,
            "implementation_max": 5,
            "intent_expansion_max": 2,
            "max_new_surfaces_per_cycle": 8,
            "max_new_axes_total": 6,
        },
        "reason": "default full (triage unavailable — uncertainty favors strategy)",
        "risk_mode": "full",
        "risk_confidence": "low",
        "risk_budget_hint": 4,
        "posture_floor": None,
    }


def _augment_risk_hints(
    triage: dict,
    section_number: str,
    planspace: Path,
    **_kwargs: object,
) -> dict:
    result = dict(triage)
    confidence = str(result.get("confidence", "low")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    result["confidence"] = confidence
    result.setdefault("risk_mode", "full")
    result.setdefault("risk_budget_hint", 0)
    result.setdefault("risk_confidence", confidence)
    result["posture_floor"] = _derive_posture_floor(section_number, planspace)
    return result


def _derive_posture_floor(section_number: str, planspace: Path) -> str | None:
    history = read_history(PathRegistry(planspace).risk_history())
    relevant = [
        entry
        for entry in history
        if f"section-{section_number}" in entry.package_id
    ]
    if not relevant:
        return None

    for entry in relevant:
        outcome = entry.actual_outcome.strip().lower()
        verification = (entry.verification_outcome or "").strip().lower()
        if outcome in {"failure", "failed", "blocked", "reopen"}:
            return PostureProfile.P3_GUARDED.value
        if verification in {"failure", "failed", "blocked"}:
            return PostureProfile.P3_GUARDED.value

    if any(
        entry.actual_outcome.strip().lower() in {"mixed", "partial", "warning"}
        for entry in relevant
    ):
        return PostureProfile.P2_STANDARD.value
    return None
