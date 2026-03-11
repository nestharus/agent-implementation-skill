"""Impact triage service for section-loop runner."""

from __future__ import annotations

import re
from pathlib import Path

from signals.artifact_io import read_json, read_json_or_default, write_json
from dispatch.model_policy import resolve
from orchestrator.path_registry import PathRegistry
from dispatch.prompt_safety import write_validated_prompt
from staleness.section_alignment import (
    _parse_alignment_verdict,
    _run_alignment_check_with_retries,
    collect_modified_files,
)
from signals.section_loop_communication import _log_artifact, log
from dispatch.section_dispatch import dispatch_agent
from orchestrator.types import Section


def run_impact_triage(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    incoming_notes: str | None,
) -> tuple[str, list[str] | None]:
    """Classify note impact and optionally short-circuit to alignment."""
    if not incoming_notes or section.solve_count < 1:
        return ("continue", None)

    artifacts = PathRegistry(planspace).artifacts
    triage_dir = artifacts / "triage"
    triage_dir.mkdir(parents=True, exist_ok=True)
    triage_prompt_path = triage_dir / f"triage-{section.number}-prompt.md"
    triage_output_path = triage_dir / f"triage-{section.number}-output.md"
    triage_signal_path = artifacts / "signals" / f"triage-{section.number}.json"

    existing_proposal = (
        artifacts / "proposals" / f"section-{section.number}-integration-proposal.md"
    )
    proposal_ref = ""
    if existing_proposal.exists():
        proposal_ref = f"3. Existing proposal: `{existing_proposal}`"

    last_align = artifacts / f"intg-align-{section.number}-output.md"
    align_ref = ""
    if last_align.exists():
        align_ref = f"4. Last alignment verdict: `{last_align}`"

    triage_notes_path = triage_dir / f"triage-{section.number}-incoming-notes.md"
    triage_notes_path.write_text(incoming_notes, encoding="utf-8")

    triage_prompt_text = f"""# Task: Impact Triage for Section {section.number}

## Context
This section has already been solved once (attempt {section.solve_count}).
New notes/changes arrived from other sections. Determine if they require
re-planning or re-implementation, or if they can be acknowledged without
expensive rework.

## Files to Read
1. Section specification: `{section.path}`
2. Incoming notes: `{triage_notes_path}`
{proposal_ref}
{align_ref}

## Instructions
Classify the impact of these notes on this section:
- `needs_replan`: true if the notes change the problem or strategy
- `needs_code_change`: true if the notes require implementation changes
- Both false if the notes are informational or already handled

For every note you read, you MUST include an acknowledgment entry in the
`acknowledge` array. Each note contains a **Note ID** field — use that ID.

Write a JSON signal to: `{triage_signal_path}`
```json
{{
  "needs_replan": false,
  "needs_code_change": false,
  "acknowledge": [
    {{"note_id": "<note-id-from-note>", "action": "accepted", "reason": "informational; no action required"}}
  ],
  "reasons": ["notes are informational"]
}}
```

Valid actions: "accepted" (resolved/no-op), "rejected" (disagree with note),
"deferred" (will address later).
"""
    if not write_validated_prompt(triage_prompt_text, triage_prompt_path):
        return ("continue", None)
    _log_artifact(planspace, f"prompt:triage-{section.number}")

    dispatch_agent(
        resolve(policy, "triage"),
        triage_prompt_path,
        triage_output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section.number,
        agent_file="consequence-note-triager.md",
    )

    triage = read_json(triage_signal_path)
    if triage is None:
        return ("continue", None)

    needs_replan = triage.get("needs_replan", True)
    needs_code = triage.get("needs_code_change", True)
    if needs_replan or needs_code:
        return ("continue", None)

    triage_acks = triage.get("acknowledge", [])
    ack_path = artifacts / "signals" / f"note-ack-{section.number}.json"
    existing_acks: dict = read_json_or_default(ack_path, {"acknowledged": []})
    existing_ids = {
        entry.get("note_id")
        for entry in existing_acks.get("acknowledged", [])
    }
    for ack in triage_acks:
        note_id = ack.get("note_id")
        if note_id and note_id not in existing_ids:
            existing_acks.setdefault("acknowledged", []).append(ack)
            existing_ids.add(note_id)
    write_json(ack_path, existing_acks)

    incoming_note_ids = set(
        re.findall(r"\*\*Note ID\*\*:\s*`([^`]+)`", incoming_notes),
    )
    acked_ids = {ack.get("note_id") for ack in triage_acks} | existing_ids
    if incoming_note_ids and not incoming_note_ids.issubset(acked_ids):
        log(
            f"Section {section.number}: triage did not acknowledge all notes "
            "— full processing",
        )
        return ("continue", None)

    log(
        f"Section {section.number}: triage says no rework needed — "
        "skipping to alignment check",
    )
    verify_result = _run_alignment_check_with_retries(
        section,
        planspace,
        codespace,
        parent,
        section.number,
        output_prefix="triage-align",
        model=resolve(policy, "alignment"),
        adjudicator_model=resolve(policy, "adjudicator"),
    )
    if verify_result == "ALIGNMENT_CHANGED_PENDING":
        return ("abort", None)
    if verify_result:
        verdict = _parse_alignment_verdict(verify_result)
        if (
            verdict is not None
            and verdict.get("aligned") is True
            and verdict.get("frame_ok", True) is True
        ):
            log(
                f"Section {section.number}: triage + alignment confirms no "
                "rework needed",
            )
            reported = collect_modified_files(planspace, section, codespace)
            return ("skip", reported if reported else [])

    return ("continue", None)
