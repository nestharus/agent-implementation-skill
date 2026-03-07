import re
from pathlib import Path

from lib.artifact_io import read_json, rename_malformed
from lib.alignment_change_tracker import set_flag
from lib.hash_service import content_hash, file_hash as hash_file
from lib.impact_analyzer import (
    analyze_impacts,
    build_section_number_map,
    normalize_section_number,
)
from lib.note_repository import (
    read_incoming_notes as load_incoming_notes,
    write_consequence_note,
)
from lib.path_registry import PathRegistry
from lib.snapshot_service import compute_text_diff, snapshot_modified_files

from .communication import (
    AGENT_NAME,
    DB_SH,
    _log_artifact,
    log,
)
from .types import Section


def post_section_completion(
    section: Section,
    modified_files: list[str],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
    impact_model: str = "glm",
    normalizer_model: str = "glm",
) -> None:
    """Post-completion steps after a section is ALIGNED.

    a) Snapshot modified files to artifacts/snapshots/section-NN/
    b) Run semantic impact analysis
    c) Leave consequence notes for materially impacted sections

    The ``impact_model`` and ``normalizer_model`` parameters default to
    ``"glm"`` but callers should pass ``policy["impact_analysis"]`` and
    ``policy["impact_normalizer"]`` for policy-driven selection.
    """
    artifacts = PathRegistry(planspace).artifacts
    sec_num = section.number

    # -----------------------------------------------------------------
    # (a) Snapshot modified files
    # -----------------------------------------------------------------
    snapshot_dir = snapshot_modified_files(
        planspace,
        sec_num,
        codespace,
        modified_files,
        warn=lambda msg: log(f"Section {sec_num}: WARNING — {msg}"),
    )

    log(f"Section {sec_num}: snapshotted {len(modified_files)} files "
        f"to {snapshot_dir}")
    _log_artifact(planspace, f"snapshot:section-{sec_num}")

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

    integration_proposal = (artifacts / "proposals"
                            / f"section-{sec_num}-integration-proposal.md")

    # Compute a mechanical fingerprint from modified file contents.
    # This stabilizes note IDs against LLM wording variance — IDs only
    # change when the actual file content changes, not when the agent
    # rephrases its reasoning.
    file_fingerprint_parts = []
    for rel_path in sorted(modified_files):
        src = codespace / rel_path
        if src.exists():
            file_digest = hash_file(src)
            file_hash = file_digest if file_digest else "unreadable"
        else:
            file_hash = "missing"
        file_fingerprint_parts.append(f"{rel_path}:{file_hash}")
    files_fingerprint = content_hash("\n".join(file_fingerprint_parts))

    for target_num, reason, contract_risk, note_md in impacted_sections:
        note_path = (
            PathRegistry(planspace).notes_dir()
            / f"from-{sec_num}-to-{target_num}.md"
        )

        # Build the list of modified files with brief context
        file_changes = "\n".join(
            f"- `{rel_path}`" for rel_path in modified_files
        )
        heading = (
            f"# Consequence Note: Section {sec_num}"
            f" -> Section {target_num}"
        )

        # Use agent-provided note_markdown as primary contract delta
        # content. Falls back to reason if agent didn't provide it.
        delta_content = (
            note_md if note_md
            else f"Impact reason: {reason}"
        )

        # Stable note ID from mechanical state (file fingerprint + target)
        # so repeated edits to the same files produce consistent IDs.
        note_id = content_hash(
            f"{note_path.name}:{files_fingerprint}",
        )[:12]

        note_path = write_consequence_note(planspace, sec_num, target_num, f"""{heading}

**Note ID**: `{note_id}`

## What Changed (read this first)
{delta_content}

## What Section {target_num} Must Accommodate
{reason}

## Acknowledgment Required

When you process this note, write an acknowledgment to
`{planspace}/artifacts/signals/note-ack-{target_num}.json`:
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
""")
        _log_artifact(planspace, f"note:from-{sec_num}-to-{target_num}")
        log(f"Section {sec_num}: left note for section {target_num} "
            f"at {note_path}")

    # Trigger targeted requeue for completed target sections.
    # When a note targets a section that already has a baseline hash,
    # that section needs to rerun alignment with the new input.
    # Setting the flag is mechanical — the main loop's requeue logic
    # determines which sections actually changed (hash comparison).
    baseline_hash_dir = PathRegistry(planspace).section_inputs_hashes_dir()
    completed_targets = [
        t for t, _r, _cr, _nm in impacted_sections
        if (baseline_hash_dir / f"{t}.hash").exists()
    ]
    if completed_targets:
        set_flag(planspace, db_sh=DB_SH, agent_name=AGENT_NAME)
        log(f"Section {sec_num}: set alignment_changed_pending — "
            f"{len(completed_targets)} target section(s) have baseline "
            f"hashes: {completed_targets}")

    # P8: Generate contract artifact for contract-risk impacts
    contract_risk_targets = [
        (t, r) for t, r, cr, _nm in impacted_sections if cr
    ]
    if contract_risk_targets:
        contracts_dir = artifacts / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        # Build lookup for target section files
        target_files_map: dict[str, set[str]] = {}
        for s in all_sections:
            target_files_map[s.number] = set(s.related_files)
        for target_num, reason in contract_risk_targets:
            shared = sorted(
                modified_set & target_files_map.get(target_num, set()))
            contract_path = (
                contracts_dir / f"contract-{sec_num}-{target_num}.md")
            if not contract_path.exists():
                shared_text = (
                    "\n".join(f"- `{f}`" for f in shared)
                    if shared else "- (indirect coupling)"
                )
                contract_path.write_text(
                    f"# Contract: Section {sec_num} ↔ Section {target_num}\n\n"
                    f"## Risk\n{reason}\n\n"
                    f"## Shared Surface\n{shared_text}\n\n"
                    f"## Invariants\n"
                    f"(To be filled by bridge agent or next alignment check)\n",
                    encoding="utf-8",
                )
                log(f"Section {sec_num}: contract artifact written for "
                    f"section {target_num}")


def read_incoming_notes(
    section: Section,
    planspace: Path,
    codespace: Path,
) -> str:
    """Read incoming consequence notes from other sections.

    Filters out acknowledged notes and bounds diff sizes to prevent
    context bloat. Returns combined context string for prompts.
    """
    artifacts = PathRegistry(planspace).artifacts
    sec_num = section.number

    note_entries = load_incoming_notes(planspace, sec_num)

    if not note_entries:
        return ""

    # P13: Load acknowledged note IDs for filtering.
    # Only filter notes that were accepted (resolved) or deferred.
    # Rejected notes remain visible so the section sees the disagreement.
    ack_path = (artifacts / "signals" / f"note-ack-{sec_num}.json")
    resolved_ids: set[str] = set()
    if ack_path.exists():
        ack_data = read_json(ack_path)
        if isinstance(ack_data, dict):
            for entry in ack_data.get("acknowledged", []):
                nid = entry.get("note_id", "")
                action = entry.get("action", "accepted")
                if nid and action in ("accepted", "deferred"):
                    resolved_ids.add(nid)
        else:
            # Preserve corrupted note-ack for diagnosis
            malformed_path = ack_path.with_suffix(".malformed.json")
            rename_malformed(ack_path)
            log(
                f"Section {sec_num}: note-ack malformed — "
                f"preserved as {malformed_path.name}, treating as "
                f"no acknowledgements"
            )

    log(f"Section {sec_num}: found {len(note_entries)} incoming notes"
        + (f" ({len(resolved_ids)} resolved)" if resolved_ids else ""))

    parts: list[str] = []
    for note in note_entries:
        note_path = note["path"]
        note_text = note["content"]

        # P13: Skip resolved notes (accepted or deferred).
        # Rejected notes are NOT filtered — the section must see them.
        note_id_match = re.search(
            r'\*\*Note ID\*\*:\s*`([^`]+)`', note_text)
        if note_id_match and note_id_match.group(1) in resolved_ids:
            continue

        parts.append(note_text)

        # Extract the source section number from the filename
        source_num = note["source"]
        if not re.fullmatch(r"\d+", source_num):
            continue

        # Compute diffs for files this section shares with the source
        source_snapshot_dir = (artifacts / "snapshots"
                               / f"section-{source_num}")
        if not source_snapshot_dir.exists():
            continue

        diff_parts: list[str] = []
        max_diff_lines = 100  # P13: bound diff size
        for rel_path in section.related_files:
            snapshot_file = source_snapshot_dir / rel_path
            current_file = codespace / rel_path
            if not snapshot_file.exists():
                continue
            diff_text = compute_text_diff(snapshot_file, current_file)
            if diff_text:
                # P13: Truncate large diffs
                diff_lines = diff_text.split("\n")
                if len(diff_lines) > max_diff_lines:
                    diff_text = "\n".join(diff_lines[:max_diff_lines])
                    diff_text += (
                        f"\n... (truncated — {len(diff_lines) - max_diff_lines}"
                        f" more lines)")
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


def extract_section_summary(section_path: Path) -> str:
    """Extract summary from YAML frontmatter of a section file."""
    text = section_path.read_text(encoding="utf-8")
    match = re.search(r'^---\s*\n.*?^summary:\s*(.+?)$.*?^---',
                      text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: first non-blank, non-heading line
    for line in text.split('\n'):
        line = line.strip()
        if line and not line.startswith('---') and not line.startswith('#'):
            return line[:200]
    return "(no summary available)"


def read_decisions(planspace: Path, section_number: str) -> str:
    """Read accumulated decisions from parent for a section.

    Returns the decisions text (may be multi-entry), or empty string
    if no decisions file exists.
    """
    decisions_file = (
        PathRegistry(planspace).decisions_dir() / f"section-{section_number}.md"
    )
    if decisions_file.exists():
        return decisions_file.read_text(encoding="utf-8")
    return ""


def persist_decision(planspace: Path, section_number: str,
                     payload: str) -> None:
    """Persist a resume payload as a decision for a section.

    Writes both the existing prose appendix **and** a structured JSON
    sidecar via :func:`decisions.record_decision` so the two formats
    stay in sync from a single write path.
    """
    from lib.decision_repository import Decision, load_decisions, record_decision

    decisions_dir = PathRegistry(planspace).decisions_dir()
    decisions_dir.mkdir(parents=True, exist_ok=True)

    # Generate a sequential decision ID based on existing count
    existing = load_decisions(decisions_dir, section=section_number)
    next_num = len(existing) + 1
    decision_id = f"d-{section_number}-{next_num:03d}"

    decision = Decision(
        id=decision_id,
        scope="section",
        section=section_number,
        problem_id=None,
        parent_problem_id=None,
        concern_scope="parent-resume",
        proposal_summary=payload,
        alignment_to_parent=None,
        status="decided",
    )

    # Single write path: record_decision writes both JSON sidecar
    # and prose appendix from the same Decision object.
    record_decision(decisions_dir, decision)
    _log_artifact(planspace, f"decision:section-{section_number}")


def normalize_section_number(
    raw_num: str,
    sec_num_map: dict[int, str],
) -> str:
    """Normalize a parsed section number to its canonical form.

    Handles mismatches like "4" vs "04" by mapping through int values.
    Falls back to the raw string if no canonical mapping exists.
    """
    try:
        return sec_num_map.get(int(raw_num), raw_num)
    except ValueError:
        return raw_num


def build_section_number_map(sections: list[Section]) -> dict[int, str]:
    """Build a mapping from int section number to canonical string form."""
    return {int(s.number): s.number for s in sections}
