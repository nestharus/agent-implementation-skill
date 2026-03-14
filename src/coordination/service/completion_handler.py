"""Post-section-completion orchestration: notes, snapshots, impact analysis.

Handles the cross-section side effects that occur after a section
achieves alignment — file snapshots, impact analysis, consequence
notes, contract artifact creation.

Previously lived in ``scan.service.section_notes``; moved here because
these are coordination concerns (cross-section consequence propagation),
not scanning concerns.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from containers import Services
from coordination.types import NoteAction

_NOTE_HASH_LENGTH = 12

from coordination.repository.notes import (
    read_incoming_notes as load_incoming_notes,
    write_consequence_note,
)
from implementation.service.impact_analyzer import analyze_impacts
from orchestrator.path_registry import PathRegistry
from orchestrator.service.section_decision_store import extract_section_summary
from implementation.service.file_snapshotter import compute_text_diff, snapshot_modified_files

if TYPE_CHECKING:
    from orchestrator.types import Section


def _compute_files_fingerprint(
    modified_files: list[str],
    codespace: Path,
) -> str:
    """Compute a combined hash fingerprint over all modified files."""
    parts = []
    for rel_path in sorted(modified_files):
        src = codespace / rel_path
        if src.exists():
            file_digest = Services.hasher().file_hash(src)
            file_hash = file_digest if file_digest else "unreadable"
        else:
            file_hash = "missing"
        parts.append(f"{rel_path}:{file_hash}")
    return Services.hasher().content_hash("\n".join(parts))


def _build_consequence_note(
    sec_num: str,
    target_num: str,
    reason: str,
    note_md: str | None,
    note_id: str,
    section_summary: str,
    modified_files: list[str],
    planspace: Path,
) -> str:
    """Build the markdown content for a consequence note."""
    paths = PathRegistry(planspace)
    snapshot_dir = paths.snapshot_section(sec_num)
    delta_content = note_md if note_md else f"Impact reason: {reason}"
    file_changes = "\n".join(f"- `{rel_path}`" for rel_path in modified_files)
    integration_proposal = paths.proposal(sec_num)
    ack_signal_path = paths.note_ack_signal(target_num)
    return f"""# Consequence Note: Section {sec_num} -> Section {target_num}

**Note ID**: `{note_id}`

## What Changed (read this first)
{delta_content}

## What Section {target_num} Must Accommodate
{reason}

## Acknowledgment Required

When you process this note, write an acknowledgment to
`{ack_signal_path}`:
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
"""


def _write_contract_artifacts(
    sec_num: str,
    impacted_sections: list,
    modified_files: list[str],
    all_sections: list[Section],
    paths: PathRegistry,
) -> None:
    """Write contract artifacts for impacted sections with contract risk."""
    contract_risk_targets = [
        (target, reason)
        for target, reason, contract_risk, _note_markdown in impacted_sections
        if contract_risk
    ]
    if not contract_risk_targets:
        return

    contracts_dir = paths.contracts_dir()
    modified_set = set(modified_files)
    target_files_map = {
        s.number: set(s.related_files) for s in all_sections
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


def post_section_completion(
    section: Section,
    modified_files: list[str],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> None:
    """Post-completion steps after a section is aligned."""
    paths = PathRegistry(planspace)
    sec_num = section.number

    snapshot_dir = snapshot_modified_files(
        planspace, sec_num, codespace, modified_files,
        warn=lambda msg: Services.logger().log(f"Section {sec_num}: WARNING — {msg}"),
    )
    Services.logger().log(f"Section {sec_num}: snapshotted {len(modified_files)} files to {snapshot_dir}")
    Services.communicator().log_artifact(planspace, f"snapshot:section-{sec_num}")

    section_summary = extract_section_summary(section.path)
    impacted_sections = analyze_impacts(
        planspace, sec_num, section_summary, modified_files, all_sections,
        codespace, parent,
    )
    if not impacted_sections:
        return

    files_fingerprint = _compute_files_fingerprint(modified_files, codespace)

    for target_num, reason, _contract_risk, note_md in impacted_sections:
        note_name = f"from-{sec_num}-to-{target_num}.md"
        note_id = Services.hasher().content_hash(f"{note_name}:{files_fingerprint}")[:_NOTE_HASH_LENGTH]
        note_content = _build_consequence_note(
            sec_num, target_num, reason, note_md, note_id,
            section_summary, modified_files, planspace,
        )
        note_path = write_consequence_note(planspace, sec_num, target_num, note_content)
        Services.communicator().log_artifact(planspace, f"note:from-{sec_num}-to-{target_num}")
        Services.logger().log(f"Section {sec_num}: left note for section {target_num} at {note_path}")

    baseline_hash_dir = paths.section_inputs_hashes_dir()
    completed_targets = [
        target
        for target, _reason, _contract_risk, _note_markdown in impacted_sections
        if (baseline_hash_dir / f"{target}.hash").exists()
    ]
    if completed_targets:
        Services.change_tracker().set_flag(planspace)
        Services.logger().log(
            f"Section {sec_num}: set alignment_changed_pending — "
            f"{len(completed_targets)} target section(s) have baseline hashes: "
            f"{completed_targets}"
        )

    _write_contract_artifacts(
        sec_num, impacted_sections, modified_files, all_sections, paths,
    )


_MAX_DIFF_LINES = 100


def _build_source_diffs(
    source_num: str,
    section: Section,
    paths: PathRegistry,
    codespace: Path,
) -> str | None:
    """Build diff text comparing a source section's snapshot to current files.

    Returns formatted markdown diff block, or ``None`` if no diffs found.
    """
    if not re.fullmatch(r"\d+", source_num):
        return None
    source_snapshot_dir = paths.snapshot_section(source_num)
    if not source_snapshot_dir.exists():
        return None

    diff_parts: list[str] = []
    for rel_path in section.related_files:
        snapshot_file = source_snapshot_dir / rel_path
        if not snapshot_file.exists():
            continue
        diff_text = compute_text_diff(snapshot_file, codespace / rel_path)
        if not diff_text:
            continue
        diff_lines = diff_text.split("\n")
        if len(diff_lines) > _MAX_DIFF_LINES:
            diff_text = "\n".join(diff_lines[:_MAX_DIFF_LINES])
            diff_text += (
                f"\n... (truncated — {len(diff_lines) - _MAX_DIFF_LINES}"
                f" more lines)"
            )
        diff_parts.append(
            f"### Diff: `{rel_path}` "
            f"(section {source_num}'s snapshot vs current)\n"
            f"```diff\n{diff_text}\n```"
        )

    if not diff_parts:
        return None
    return (
        f"### File Diffs Since Section {source_num}\n\n"
        + "\n\n".join(diff_parts)
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
        ack_data = Services.artifact_io().read_json(ack_path)
        if isinstance(ack_data, dict):
            for entry in ack_data.get("acknowledged", []):
                note_id = entry.get("note_id", "")
                action = entry.get("action", NoteAction.ACCEPTED)
                if note_id and action in (NoteAction.ACCEPTED, NoteAction.DEFERRED):
                    resolved_ids.add(note_id)
        else:
            malformed_path = ack_path.with_suffix(".malformed.json")
            Services.artifact_io().rename_malformed(ack_path)
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
        diff_section = _build_source_diffs(
            source_num, section, paths, codespace,
        )
        if diff_section:
            parts.append(diff_section)

    return "\n\n---\n\n".join(parts)
