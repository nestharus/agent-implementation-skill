"""Intent triage: decide lightweight vs full bidirectional intent cycle."""

import json
from pathlib import Path

from ..communication import _log_artifact, log
from ..dispatch import dispatch_agent, read_agent_signal, read_model_policy


def run_intent_triage(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    related_files_count: int = 0,
    incoming_notes_count: int = 0,
    mode: str = "brownfield",
    solve_count: int = 0,
    section_summary: str = "",
) -> dict:
    """Dispatch intent-triager (GLM) and return the triage result.

    Returns a dict with at least ``intent_mode`` ("full" or "lightweight")
    and ``budgets``.  Falls back to lightweight on failure.
    """
    policy = read_model_policy(planspace)
    artifacts = planspace / "artifacts"
    signals_dir = artifacts / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    triage_signal_path = signals_dir / f"intent-triage-{section_number}.json"
    triage_prompt_path = artifacts / f"intent-triage-{section_number}-prompt.md"
    triage_output_path = artifacts / f"intent-triage-{section_number}-output.md"

    triage_prompt_path.write_text(f"""# Task: Intent Triage for Section {section_number}

## Context
Decide whether this section needs the full bidirectional intent cycle
(problem + philosophy alignment with surface discovery and expansion)
or lightweight alignment (existing alignment judge only).

## Section Characteristics
- Related files: {related_files_count}
- Incoming cross-section notes: {incoming_notes_count}
- Mode: {mode}
- Previous solve attempts: {solve_count}
- Summary: {section_summary[:500] if section_summary else "(none)"}

## Full Mode Triggers (any TWO = full mode)
1. related_files >= 5
2. incoming_notes >= 2
3. mode is "greenfield" or "hybrid"
4. section contains architecture keywords (refactor, migration, schema, API, security, contract)
5. section has prior failure history (solve_count >= 2)

## Always Lightweight (unless user overrides)
- related_files == 1 AND no incoming notes AND brownfield
- doc-only, rename-only, or tiny local fix

## Output
Write a JSON signal to: `{triage_signal_path}`

```json
{{
  "section": "{section_number}",
  "intent_mode": "full"|"lightweight",
  "complexity": {{
    "related_files": {related_files_count},
    "incoming_notes": {incoming_notes_count},
    "mode": "{mode}",
    "keywords": [],
    "risk": "low"|"medium"|"high"
  }},
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
""", encoding="utf-8")
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
        return _lightweight_default(section_number)

    # Read the triage signal
    triage = read_agent_signal(
        triage_signal_path,
        expected_fields=["intent_mode"],
    )
    if triage:
        log(f"Section {section_number}: intent triage → "
            f"{triage.get('intent_mode', 'unknown')}")
        return triage

    # Fallback: lightweight
    log(f"Section {section_number}: intent triage signal missing or "
        f"malformed — defaulting to lightweight")
    return _lightweight_default(section_number)


def load_triage_result(
    section_number: str, planspace: Path,
) -> dict | None:
    """Load a previously-written triage result from signal file."""
    signals_dir = planspace / "artifacts" / "signals"
    triage_signal_path = signals_dir / f"intent-triage-{section_number}.json"
    return read_agent_signal(
        triage_signal_path, expected_fields=["intent_mode"],
    )


def _lightweight_default(section_number: str) -> dict:
    return {
        "section": section_number,
        "intent_mode": "lightweight",
        "budgets": {
            "proposal_max": 5,
            "implementation_max": 5,
            "intent_expansion_max": 0,
            "max_new_surfaces_per_cycle": 0,
            "max_new_axes_total": 0,
        },
        "reason": "default lightweight (triage unavailable)",
    }
