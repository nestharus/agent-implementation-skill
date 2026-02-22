import difflib
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .communication import (
    AGENT_NAME,
    DB_SH,
    _log_artifact,
    log,
)
from .dispatch import dispatch_agent
from .types import Section


def compute_text_diff(old_path: Path, new_path: Path) -> str:
    """Compute a unified text diff between two files.

    Returns a human-readable unified diff string. If either file is
    missing, returns an appropriate message instead.
    """
    if not old_path.exists() and not new_path.exists():
        return ""
    if not old_path.exists():
        old_lines: list[str] = []
        old_label = "(did not exist)"
    else:
        old_lines = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
        old_label = str(old_path)
    if not new_path.exists():
        new_lines: list[str] = []
        new_label = "(deleted)"
    else:
        new_lines = new_path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_label = str(new_path)

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=old_label, tofile=new_label,
        lineterm="",
    )
    return "\n".join(diff)


def post_section_completion(
    section: Section,
    modified_files: list[str],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> None:
    """Post-completion steps after a section is ALIGNED.

    a) Snapshot modified files to artifacts/snapshots/section-NN/
    b) Run semantic impact analysis via GLM
    c) Leave consequence notes for materially impacted sections
    """
    artifacts = planspace / "artifacts"
    sec_num = section.number

    # -----------------------------------------------------------------
    # (a) Snapshot modified files
    # -----------------------------------------------------------------
    snapshot_dir = artifacts / "snapshots" / f"section-{sec_num}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    codespace_resolved = codespace.resolve()
    snapshot_resolved = snapshot_dir.resolve()
    for rel_path in modified_files:
        src = (codespace / rel_path).resolve()
        if not src.exists():
            continue
        # Verify src is under codespace (belt-and-suspenders)
        if not src.is_relative_to(codespace_resolved):
            log(f"Section {sec_num}: WARNING — snapshot path escapes "
                f"codespace, skipping: {rel_path}")
            continue
        # Preserve relative directory structure inside the snapshot
        dest = (snapshot_dir / rel_path).resolve()
        if not dest.is_relative_to(snapshot_resolved):
            log(f"Section {sec_num}: WARNING — dest path escapes "
                f"snapshot dir, skipping: {rel_path}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))

    log(f"Section {sec_num}: snapshotted {len(modified_files)} files "
        f"to {snapshot_dir}")
    _log_artifact(planspace, f"snapshot:section-{sec_num}")

    # -----------------------------------------------------------------
    # (b) Semantic impact analysis via GLM
    # -----------------------------------------------------------------
    other_sections = [s for s in all_sections if s.number != sec_num
                      and s.related_files]
    if not other_sections:
        log(f"Section {sec_num}: no other sections to check for impact")
        return

    # Build file-change description
    change_lines = []
    for rel_path in modified_files:
        change_lines.append(f"- `{rel_path}`")
    changes_text = "\n".join(change_lines) if change_lines else "(none)"

    # Build other-sections description
    other_section_lines = []
    for other in other_sections:
        files_str = ", ".join(f"`{f}`" for f in other.related_files[:10])
        if len(other.related_files) > 10:
            files_str += f" (+{len(other.related_files) - 10} more)"
        summary = extract_section_summary(other.path)
        other_section_lines.append(
            f"- SECTION-{other.number}: {summary}\n"
            f"  Related files: {files_str}"
        )
    other_text = "\n".join(other_section_lines)

    section_summary = extract_section_summary(section.path)

    # -----------------------------------------------------------------
    # (a2) Write contract summary for this section
    # -----------------------------------------------------------------
    contracts_dir = artifacts / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    contract_summary_path = contracts_dir / f"section-{sec_num}-contract-summary.md"

    # Read integration proposal for contract context
    integration_proposal = (artifacts / "proposals"
                            / f"section-{sec_num}-integration-proposal.md")
    contracts_context = ""
    if integration_proposal.exists():
        contracts_context = integration_proposal.read_text(encoding="utf-8")

    contracts_summary = _extract_contracts_summary(contracts_context)
    contract_summary_path.write_text(f"""# Contract Summary: Section {sec_num}

## Section Summary
{section_summary}

## Contracts and Interfaces
{contracts_summary if contracts_summary else "(No explicit contracts found in integration proposal.)"}

## Modified Files
{changes_text}
""", encoding="utf-8")
    _log_artifact(planspace, f"contract:section-{sec_num}")
    log(f"Section {sec_num}: wrote contract summary to {contract_summary_path}")

    impact_prompt_path = artifacts / f"impact-{sec_num}-prompt.md"
    impact_output_path = artifacts / f"impact-{sec_num}-output.md"
    heading = f"# Task: Semantic Impact Analysis for Section {sec_num}"
    impact_prompt_path.write_text(f"""{heading}

## What Section {sec_num} Did
{section_summary}

## Files Modified by Section {sec_num}
{changes_text}

## Other Sections and Their Files
{other_text}

## Instructions

For each other section listed above, determine if the changes made by
section {sec_num} have a MATERIAL impact on that section's problem, or
if it is just a coincidental file overlap that does not affect the other
section's work.

A change is MATERIAL if:
- It modifies an interface, contract, or API that the other section depends on
- It changes control flow or data structures the other section needs to work with
- It introduces constraints or assumptions the other section must accommodate

A change is NO_IMPACT if:
- The files overlap but the changes are in unrelated parts
- The other section only reads data that was not affected
- The change is purely cosmetic or stylistic

Reply with a JSON block containing your analysis:

```json
{{"impacts": [
  {{"to": "04", "impact": "MATERIAL", "reason": "Modified event model interface that section 04 depends on"}},
  {{"to": "07", "impact": "NO_IMPACT"}}
]}}
```

Every other section must appear in the impacts array with either
MATERIAL or NO_IMPACT. The `to` field is the section number.
For MATERIAL impacts, include a `reason` field.

**Also include a brief `note_markdown` for each MATERIAL impact** —
this will be written directly as the consequence note:
```json
{{"impacts": [
  {{"to": "04", "impact": "MATERIAL", "reason": "...", "note_markdown": "Section {sec_num} changed the event model interface. Section 04 must accommodate the new field `event_type` in `config.py`."}}
]}}
```
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:impact-{sec_num}")

    log(f"Section {sec_num}: running impact analysis")
    # Emit GLM exploration event for QA monitor rule C2
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
         "summary", f"glm-explore:{sec_num}",
         "impact analysis",
         "--agent", AGENT_NAME],
        capture_output=True, text=True,
    )
    impact_result = dispatch_agent(
        "glm", impact_prompt_path, impact_output_path,
        planspace, parent, codespace=codespace,
        section_number=sec_num,
    )

    # -----------------------------------------------------------------
    # (c) Parse impact results and leave consequence notes
    # -----------------------------------------------------------------
    # Normalize section numbers to canonical form (handles "4" vs "04")
    sec_num_map = build_section_number_map(all_sections)

    impacted_sections: list[tuple[str, str]] = []
    # Primary: parse structured JSON from agent output
    json_parsed = False
    try:
        # Find JSON block in output (may be in code fence)
        json_text = None
        in_fence = False
        fence_lines: list[str] = []
        for line in impact_result.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```") and not in_fence:
                in_fence = True
                fence_lines = []
                continue
            if stripped.startswith("```") and in_fence:
                in_fence = False
                candidate = "\n".join(fence_lines)
                if '"impacts"' in candidate:
                    json_text = candidate
                    break
                continue
            if in_fence:
                fence_lines.append(line)

        if json_text is None:
            # Try raw JSON (no code fence)
            start = impact_result.find("{")
            end = impact_result.rfind("}")
            if start >= 0 and end > start:
                candidate = impact_result[start:end + 1]
                if '"impacts"' in candidate:
                    json_text = candidate

        if json_text:
            data = json.loads(json_text)
            for entry in data.get("impacts", []):
                if entry.get("impact") == "MATERIAL":
                    target = normalize_section_number(
                        str(entry["to"]), sec_num_map)
                    reason = entry.get("reason", "")
                    impacted_sections.append((target, reason))
            json_parsed = True
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    # Fallback: regex parsing for backwards compatibility
    if not json_parsed:
        log(f"Section {sec_num}: WARNING — impact analysis did not "
            f"produce valid JSON, falling back to regex parsing")
        for line in impact_result.split("\n"):
            line = line.strip()
            match = re.match(r'SECTION-(\d+):\s*MATERIAL\s*(.*)', line)
            if match:
                canonical = normalize_section_number(
                    match.group(1), sec_num_map,
                )
                impacted_sections.append((canonical, match.group(2)))

    if not impacted_sections:
        log(f"Section {sec_num}: no material impacts on other sections")
        return

    log(f"Section {sec_num}: material impact on sections "
        f"{[s[0] for s in impacted_sections]}")

    notes_dir = artifacts / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Read the section's integration proposal for contract/interface context
    integration_proposal = (artifacts / "proposals"
                            / f"section-{sec_num}-integration-proposal.md")
    contracts_context = ""
    if integration_proposal.exists():
        contracts_context = integration_proposal.read_text(encoding="utf-8")

    # Extract contract/interface sections from proposal for inline notes
    contracts_summary = _extract_contracts_summary(contracts_context)

    for target_num, reason in impacted_sections:
        note_path = notes_dir / f"from-{sec_num}-to-{target_num}.md"

        # Build the list of modified files with brief context
        file_changes = "\n".join(
            f"- `{rel_path}`" for rel_path in modified_files
        )
        heading = (
            f"# Consequence Note: Section {sec_num}"
            f" -> Section {target_num}"
        )
        contracts = (
            contracts_summary
            if contracts_summary
            else "(No explicit contracts extracted "
                 "from integration proposal.)"
        )

        # Compute a stable note ID for the acknowledgment lifecycle
        note_content_draft = (
            f"{heading}\n{contracts}\n{reason}\n{file_changes}")
        note_id = hashlib.sha256(
            f"{note_path.name}:{hashlib.sha256(note_content_draft.encode()).hexdigest()}"
            .encode()
        ).hexdigest()[:12]

        note_path.write_text(f"""{heading}

**Note ID**: `{note_id}`

## Contract Deltas (read this first)
{contracts}

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
designated problem. Impact reason: {reason}

## Files Modified (for reference)
{file_changes}

Full integration proposal: `{integration_proposal}`
Snapshot directory: `{snapshot_dir}`
""", encoding="utf-8")
        _log_artifact(planspace, f"note:from-{sec_num}-to-{target_num}")
        log(f"Section {sec_num}: left note for section {target_num} "
            f"at {note_path}")


def read_incoming_notes(
    section: Section,
    planspace: Path,
    codespace: Path,
) -> str:
    """Read incoming consequence notes from other sections.

    Globs for artifacts/notes/from-*-to-{section.number}.md, reads each
    note, and computes text diffs for shared files that have changed
    since the authoring section last saw them.

    Returns a combined context string suitable for inclusion in prompts.
    Empty string if no notes exist.
    """
    artifacts = planspace / "artifacts"
    notes_dir = artifacts / "notes"
    sec_num = section.number

    if not notes_dir.exists():
        return ""

    note_pattern = f"from-*-to-{sec_num}.md"
    note_files = sorted(notes_dir.glob(note_pattern))

    if not note_files:
        return ""

    log(f"Section {sec_num}: found {len(note_files)} incoming notes")

    parts: list[str] = []
    for note_path in note_files:
        note_text = note_path.read_text(encoding="utf-8")
        parts.append(note_text)

        # Extract the source section number from the filename
        name_match = re.match(r'from-(\d+)-to-\d+\.md', note_path.name)
        if not name_match:
            continue
        source_num = name_match.group(1)

        # Compute diffs for files this section shares with the source
        source_snapshot_dir = (artifacts / "snapshots"
                               / f"section-{source_num}")
        if not source_snapshot_dir.exists():
            continue

        diff_parts: list[str] = []
        for rel_path in section.related_files:
            snapshot_file = source_snapshot_dir / rel_path
            current_file = codespace / rel_path
            if not snapshot_file.exists():
                continue
            diff_text = compute_text_diff(snapshot_file, current_file)
            if diff_text:
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
    decisions_file = (planspace / "artifacts" / "decisions"
                      / f"section-{section_number}.md")
    if decisions_file.exists():
        return decisions_file.read_text(encoding="utf-8")
    return ""


def persist_decision(planspace: Path, section_number: str,
                     payload: str) -> None:
    """Persist a resume payload as a decision for a section."""
    decisions_dir = planspace / "artifacts" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_file = decisions_dir / f"section-{section_number}.md"
    with decision_file.open("a", encoding="utf-8") as f:
        f.write(f"\n## Decision (from parent)\n{payload}\n")
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


def _extract_contracts_summary(proposal_text: str) -> str:
    """Extract contract/interface mentions from an integration proposal.

    Scans for headings containing 'contract', 'interface', 'api', or
    'integration point' and returns their content. Returns empty string
    if no relevant sections found.
    """
    if not proposal_text:
        return ""
    lines = proposal_text.split("\n")
    parts: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("#") and any(
            kw in stripped for kw in
            ["contract", "interface", "api", "integration point",
             "change strategy", "risks"]
        ):
            capturing = True
            parts.append(line)
        elif capturing and line.strip().startswith("#"):
            capturing = False
        elif capturing:
            parts.append(line)
    return "\n".join(parts).strip()
