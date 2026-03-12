"""Cross-section note helpers extracted from coordination.cross_section."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from staleness.service.change_tracker import set_flag
from signals.repository.artifact_io import read_json, rename_malformed
from coordination.repository.notes import (
    read_incoming_notes as load_incoming_notes,
    write_consequence_note,
)
from implementation.service.impact_analyzer import analyze_impacts
from orchestrator.path_registry import PathRegistry
from orchestrator.service.section_decisions import extract_section_summary
from implementation.service.snapshot import compute_text_diff, snapshot_modified_files

from containers import Services
from signals.service.communication import AGENT_NAME, DB_SH

if TYPE_CHECKING:
    from orchestrator.types import Section


def post_section_completion(
    section: Section,
    modified_files: list[str],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
    impact_model: str,
    normalizer_model: str,
) -> None:
    """Post-completion steps after a section is aligned."""
    paths = PathRegistry(planspace)
    sec_num = section.number

    snapshot_dir = snapshot_modified_files(
        planspace,
        sec_num,
        codespace,
        modified_files,
        warn=lambda msg: Services.logger().log(f"Section {sec_num}: WARNING — {msg}"),
    )

    Services.logger().log(f"Section {sec_num}: snapshotted {len(modified_files)} files to {snapshot_dir}")
    Services.communicator().log_artifact(planspace, f"snapshot:section-{sec_num}")

    section_summary = extract_section_summary(section.path)
    impacted_sections = analyze_impacts(
        planspace,
        sec_num,
        section_summary,
        modified_files,
        all_sections,
        codespace,
        parent,
        summary_reader=extract_section_summary,
        impact_model=impact_model,
        normalizer_model=normalizer_model,
    )
    if not impacted_sections:
        return
    modified_set = set(modified_files)

    integration_proposal = paths.proposal(sec_num)

    file_fingerprint_parts = []
    for rel_path in sorted(modified_files):
        src = codespace / rel_path
        if src.exists():
            file_digest = Services.hasher().file_hash(src)
            file_hash = file_digest if file_digest else "unreadable"
        else:
            file_hash = "missing"
        file_fingerprint_parts.append(f"{rel_path}:{file_hash}")
    files_fingerprint = Services.hasher().content_hash("\n".join(file_fingerprint_parts))

    for target_num, reason, contract_risk, note_md in impacted_sections:
        note_path = (
            paths.notes_dir()
            / f"from-{sec_num}-to-{target_num}.md"
        )
        file_changes = "\n".join(f"- `{rel_path}`" for rel_path in modified_files)
        heading = f"# Consequence Note: Section {sec_num} -> Section {target_num}"
        delta_content = note_md if note_md else f"Impact reason: {reason}"
        note_id = Services.hasher().content_hash(f"{note_path.name}:{files_fingerprint}")[:12]

        note_path = write_consequence_note(
            planspace,
            sec_num,
            target_num,
            f"""{heading}

**Note ID**: `{note_id}`

## What Changed (read this first)
{delta_content}

## What Section {target_num} Must Accommodate
{reason}

## Acknowledgment Required

When you process this note, write an acknowledgment to
`{paths.note_ack_signal(target_num)}`:
```json
{{"acknowledged": [{{"note_id": "{note_id}", "action": "accepted|rejected|deferred", "reason": "..."}}]}}
```

## Why This Happened
Section {sec_num} ({section_summary}) implemented changes to solve its
designated problem.

## Files Modified (for reference)
{file_changes}

Full integration proposal: `{integration_proposal}`
Snapshot directory: `{snapshot_dir}`
""",
        )
        Services.communicator().log_artifact(planspace, f"note:from-{sec_num}-to-{target_num}")
        Services.logger().log(f"Section {sec_num}: left note for section {target_num} at {note_path}")

    baseline_hash_dir = paths.section_inputs_hashes_dir()
    completed_targets = [
        target
        for target, _reason, _contract_risk, _note_markdown in impacted_sections
        if (baseline_hash_dir / f"{target}.hash").exists()
    ]
    if completed_targets:
        set_flag(planspace, db_sh=DB_SH, agent_name=AGENT_NAME)
        Services.logger().log(
            f"Section {sec_num}: set alignment_changed_pending — "
            f"{len(completed_targets)} target section(s) have baseline hashes: "
            f"{completed_targets}"
        )

    contract_risk_targets = [
        (target, reason)
        for target, reason, contract_risk, _note_markdown in impacted_sections
        if contract_risk
    ]
    if contract_risk_targets:
        contracts_dir = paths.contracts_dir()
        contracts_dir.mkdir(parents=True, exist_ok=True)
        target_files_map = {
            other_section.number: set(other_section.related_files)
            for other_section in all_sections
        }
        for target_num, reason in contract_risk_targets:
            shared = sorted(modified_set & target_files_map.get(target_num, set()))
            contract_path = contracts_dir / f"contract-{sec_num}-{target_num}.md"
            if not contract_path.exists():
                shared_text = (
                    "\n".join(f"- `{path}`" for path in shared)
                    if shared
                    else "- (indirect coupling)"
                )
                contract_path.write_text(
                    f"# Contract: Section {sec_num} ↔ Section {target_num}\n\n"
                    f"## Risk\n{reason}\n\n"
                    f"## Shared Surface\n{shared_text}\n\n"
                    f"## Invariants\n"
                    f"(To be filled by bridge agent or next alignment check)\n",
                    encoding="utf-8",
                )
                Services.logger().log(
                    f"Section {sec_num}: contract artifact written for "
                    f"section {target_num}"
                )


def read_incoming_notes(
    section: Section,
    planspace: Path,
    codespace: Path,
) -> str:
    """Read incoming consequence notes from other sections."""
    paths = PathRegistry(planspace)
    sec_num = section.number

    note_entries = load_incoming_notes(planspace, sec_num)
    if not note_entries:
        return ""

    ack_path = paths.note_ack_signal(sec_num)
    resolved_ids: set[str] = set()
    if ack_path.exists():
        ack_data = read_json(ack_path)
        if isinstance(ack_data, dict):
            for entry in ack_data.get("acknowledged", []):
                note_id = entry.get("note_id", "")
                action = entry.get("action", "accepted")
                if note_id and action in ("accepted", "deferred"):
                    resolved_ids.add(note_id)
        else:
            malformed_path = ack_path.with_suffix(".malformed.json")
            rename_malformed(ack_path)
            Services.logger().log(
                f"Section {sec_num}: note-ack malformed — "
                f"preserved as {malformed_path.name}, treating as "
                f"no acknowledgements"
            )

    Services.logger().log(
        f"Section {sec_num}: found {len(note_entries)} incoming notes"
        + (f" ({len(resolved_ids)} resolved)" if resolved_ids else "")
    )

    parts: list[str] = []
    for note in note_entries:
        note_text = note["content"]
        note_id_match = re.search(r"\*\*Note ID\*\*:\s*`([^`]+)`", note_text)
        if note_id_match and note_id_match.group(1) in resolved_ids:
            continue

        parts.append(note_text)
        source_num = note["source"]
        if not re.fullmatch(r"\d+", source_num):
            continue

        source_snapshot_dir = paths.snapshot_section(source_num)
        if not source_snapshot_dir.exists():
            continue

        diff_parts: list[str] = []
        max_diff_lines = 100
        for rel_path in section.related_files:
            snapshot_file = source_snapshot_dir / rel_path
            current_file = codespace / rel_path
            if not snapshot_file.exists():
                continue
            diff_text = compute_text_diff(snapshot_file, current_file)
            if diff_text:
                diff_lines = diff_text.split("\n")
                if len(diff_lines) > max_diff_lines:
                    diff_text = "\n".join(diff_lines[:max_diff_lines])
                    diff_text += (
                        f"\n... (truncated — {len(diff_lines) - max_diff_lines}"
                        f" more lines)"
                    )
                diff_parts.append(
                    f"### Diff: `{rel_path}` "
                    f"(section {source_num}'s snapshot vs current)\n"
                    f"```diff\n{diff_text}\n```"
                )

        if diff_parts:
            parts.append(
                f"### File Diffs Since Section {source_num}\n\n"
                + "\n\n".join(diff_parts)
            )

    return "\n\n---\n\n".join(parts)


def log_phase_failure(
    phase: str,
    section: str,
    error: str,
    planspace: Path,
) -> None:
    """Append a structured failure line and emit the same failure to stderr."""
    failure_log = planspace / "failures.log"
    ts = datetime.now(tz=timezone.utc).isoformat()
    line = f"{ts} phase={phase} context={section} message={error}\n"
    with failure_log.open("a") as f:
        f.write(line)
    print(
        f"[FAIL] phase={phase} context={section} message={error}",
        file=sys.stderr,
    )
