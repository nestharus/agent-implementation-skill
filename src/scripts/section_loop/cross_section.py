import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .communication import (
    AGENT_NAME,
    DB_SH,
    WORKFLOW_HOME,
    _log_artifact,
    log,
)
from .dispatch import dispatch_agent
from .pipeline_control import _set_alignment_changed_flag
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

    section_summary = extract_section_summary(section.path)

    # Build file-change description
    change_lines = []
    for rel_path in modified_files:
        change_lines.append(f"- `{rel_path}`")
    changes_text = "\n".join(change_lines) if change_lines else "(none)"

    # -----------------------------------------------------------------
    # (b) Two-stage impact analysis: candidate generation + semantic check
    # -----------------------------------------------------------------
    other_sections = [s for s in all_sections if s.number != sec_num]
    if not other_sections:
        log(f"Section {sec_num}: no other sections to check for impact")
        return

    # Stage A: Mechanical candidate generation (no agent call needed)
    # Find sections with overlapping files, recent note mentions,
    # shared snapshot files, shared input refs, or existing contracts
    notes_dir_path = artifacts / "notes"
    modified_set = set(modified_files)
    candidate_sections: list[Section] = []
    # Pre-compute source section's input refs for Check 4
    source_inputs = artifacts / "inputs" / f"section-{sec_num}"
    source_refs = set()
    if source_inputs.is_dir():
        source_refs = {f.name for f in source_inputs.iterdir()
                       if f.suffix == ".ref"}
    for other in other_sections:
        other_files = set(other.related_files)
        # Check 1: File overlap
        if modified_set & other_files:
            candidate_sections.append(other)
            continue
        # Check 2: Existing notes mentioning this section pair
        note_path = notes_dir_path / f"from-{sec_num}-to-{other.number}.md"
        if note_path.exists():
            candidate_sections.append(other)
            continue
        # Check 3: Snapshot overlap (source section touched files
        # that the other section previously snapshotted)
        snapshot_match = False
        other_snapshot = artifacts / "snapshots" / f"section-{other.number}"
        if other_snapshot.exists():
            for mod_file in modified_files:
                if (other_snapshot / mod_file).exists():
                    candidate_sections.append(other)
                    snapshot_match = True
                    break
        if snapshot_match:
            continue
        # Check 4: Shared input refs (structured seam artifacts —
        # both sections depend on the same substrate or contract ref)
        if source_refs:
            other_inputs = artifacts / "inputs" / f"section-{other.number}"
            if other_inputs.is_dir():
                other_refs = {f.name for f in other_inputs.iterdir()
                              if f.suffix == ".ref"}
                if source_refs & other_refs:
                    candidate_sections.append(other)
                    continue
        # Check 5: Existing contract artifacts linking this pair
        contracts_dir = artifacts / "contracts"
        if contracts_dir.is_dir():
            fwd = contracts_dir / f"contract-{sec_num}-{other.number}.md"
            rev = contracts_dir / f"contract-{other.number}-{sec_num}.md"
            if fwd.exists() or rev.exists():
                candidate_sections.append(other)
                continue

    if not candidate_sections:
        log(f"Section {sec_num}: no candidate sections for impact analysis")
        return

    log(f"Section {sec_num}: {len(candidate_sections)} candidate sections "
        f"(of {len(other_sections)} total) for impact analysis")

    # Stage B: Semantic impact analysis on candidates only (policy-controlled)
    candidate_lines = []
    for other in candidate_sections:
        if other.related_files:
            files_str = ", ".join(f"`{f}`" for f in other.related_files[:10])
            if len(other.related_files) > 10:
                files_str += f" (+{len(other.related_files) - 10} more)"
        else:
            files_str = "(no current file hypothesis)"
        summary = extract_section_summary(other.path)
        candidate_lines.append(
            f"- SECTION-{other.number}: {summary}\n"
            f"  Related files: {files_str}"
        )
    candidate_text = "\n".join(candidate_lines)

    # Also note which sections were NOT evaluated
    skipped_nums = sorted(
        s.number for s in other_sections if s not in candidate_sections)
    skipped_note = ""
    if skipped_nums:
        skipped_note = (
            f"\n\n**Not evaluated** (no seam signals — file overlap, prior notes, "
            f"snapshots, shared refs, or contract artifacts): "
            f"sections {', '.join(skipped_nums)}"
        )

    impact_prompt_path = artifacts / f"impact-{sec_num}-prompt.md"
    impact_output_path = artifacts / f"impact-{sec_num}-output.md"
    heading = f"# Task: Semantic Impact Analysis for Section {sec_num}"
    impact_prompt_path.write_text(f"""{heading}

## What Section {sec_num} Did
{section_summary}

## Files Modified by Section {sec_num}
{changes_text}

## Candidate Sections (pre-filtered by seam signals)
{candidate_text}
{skipped_note}

## Instructions

These sections were pre-selected because they share modified files, have
existing cross-section notes, have overlapping snapshots, share input refs,
or have contract artifacts linking them to section {sec_num}.
Candidate selection is a routing hypothesis — the seam signals identify
sections that MAY be affected, not sections that definitely are.
For each candidate, determine MATERIAL vs NO_IMPACT.

A change is MATERIAL if:
- It modifies an interface, contract, or API that the other section depends on
- It changes control flow or data structures the other section needs
- It introduces constraints the other section must accommodate

Reply with a JSON block:

```json
{{"impacts": [
  {{"to": "04", "impact": "MATERIAL", "reason": "Modified event model interface", "contract_risk": false, "note_markdown": "## Contract Delta\\nThe event model now uses X instead of Y. Section 04 must update its event handler to accept the new schema."}},
  {{"to": "07", "impact": "NO_IMPACT"}}
]}}
```

Each candidate section must appear. Include `contract_risk: true` if the
impact involves a shared interface or contract change.

For each MATERIAL impact, `note_markdown` is REQUIRED — a brief markdown
description of what changed and what the target section must accommodate.
This is the primary content of the consequence note the target receives.
""", encoding="utf-8")
    # V4: Append context sidecar reference if it exists
    context_sidecar = artifacts / "context-impact-analyzer.json"
    if context_sidecar.exists():
        with impact_prompt_path.open("a", encoding="utf-8") as f:
            f.write(
                f"\n## Scoped Context\n"
                f"Agent context sidecar with resolved inputs: "
                f"`{context_sidecar}`\n"
            )
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
        impact_model, impact_prompt_path, impact_output_path,
        planspace, parent, codespace=codespace,
        section_number=sec_num,
        agent_file="impact-analyzer.md",
    )

    # -----------------------------------------------------------------
    # (c) Parse impact results and leave consequence notes
    # -----------------------------------------------------------------
    # Normalize section numbers to canonical form (handles "4" vs "04")
    sec_num_map = build_section_number_map(all_sections)

    impacted_sections: list[tuple[str, str, bool, str]] = []
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
                    contract_risk = bool(entry.get("contract_risk", False))
                    note_md = entry.get("note_markdown", "")
                    impacted_sections.append(
                        (target, reason, contract_risk, note_md))
            json_parsed = True
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    # Fallback: dispatch GLM to normalize raw output into JSON
    if not json_parsed:
        log(f"Section {sec_num}: impact analysis did not produce valid "
            f"JSON — dispatching GLM to normalize raw output")
        artifacts = planspace / "artifacts"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", dir=str(artifacts),
            prefix=f"impact-normalize-{sec_num}-raw-", delete=False,
        ) as raw_f:
            raw_f.write(impact_result)
            raw_path = Path(raw_f.name)
        normalize_prompt_path = (
            artifacts / f"impact-normalize-{sec_num}-prompt.md"
        )
        normalize_output_path = (
            artifacts / f"impact-normalize-{sec_num}-output.md"
        )
        normalize_prompt_path.write_text(f"""# Task: Normalize Impact Analysis Output

## Raw Output File
`{raw_path}`

Read the file above. It contains the raw output from a previous impact
analysis that did not produce well-formed JSON.

## Instructions

Extract any MATERIAL impact entries from the raw text and return them
as structured JSON. Look for mentions of section numbers paired with
MATERIAL impact assessments, reasons, or notes.

Reply with ONLY a JSON block:

```json
{{"impacts": [
  {{"to": "<section_number>", "impact": "MATERIAL", "reason": "<reason>", "note_markdown": "<brief description of what changed and what the target must accommodate>"}},
  ...
]}}
```

If no material impacts can be extracted, reply:
```json
{{"impacts": []}}
```
""", encoding="utf-8")
        normalize_result = dispatch_agent(
            normalizer_model, normalize_prompt_path, normalize_output_path,
            planspace, parent, codespace=codespace,
            section_number=sec_num,
        )
        # Parse the normalizer's JSON output
        try:
            norm_json = None
            norm_start = normalize_result.find("{")
            norm_end = normalize_result.rfind("}")
            if norm_start >= 0 and norm_end > norm_start:
                candidate = normalize_result[norm_start:norm_end + 1]
                if '"impacts"' in candidate:
                    norm_json = candidate
            if norm_json:
                norm_data = json.loads(norm_json)
                for entry in norm_data.get("impacts", []):
                    if entry.get("impact") == "MATERIAL":
                        target = normalize_section_number(
                            str(entry["to"]), sec_num_map)
                        reason = entry.get("reason", "")
                        contract_risk = bool(
                            entry.get("contract_risk", False))
                        note_md = entry.get("note_markdown", "")
                        impacted_sections.append(
                            (target, reason, contract_risk, note_md))
        except (json.JSONDecodeError, KeyError, TypeError):
            log(f"Section {sec_num}: GLM normalizer also failed to "
                f"produce valid JSON — no material impacts recorded")

    if not impacted_sections:
        log(f"Section {sec_num}: no material impacts on other sections")
        return

    log(f"Section {sec_num}: material impact on sections "
        f"{[s[0] for s in impacted_sections]}")

    notes_dir = artifacts / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

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
            try:
                file_hash = hashlib.sha256(
                    src.read_bytes()).hexdigest()
            except OSError:
                file_hash = "unreadable"
        else:
            file_hash = "missing"
        file_fingerprint_parts.append(f"{rel_path}:{file_hash}")
    files_fingerprint = hashlib.sha256(
        "\n".join(file_fingerprint_parts).encode()
    ).hexdigest()

    for target_num, reason, contract_risk, note_md in impacted_sections:
        note_path = notes_dir / f"from-{sec_num}-to-{target_num}.md"

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
        note_id = hashlib.sha256(
            f"{note_path.name}:{files_fingerprint}".encode()
        ).hexdigest()[:12]

        note_path.write_text(f"""{heading}

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
""", encoding="utf-8")
        _log_artifact(planspace, f"note:from-{sec_num}-to-{target_num}")
        log(f"Section {sec_num}: left note for section {target_num} "
            f"at {note_path}")

    # Trigger targeted requeue for completed target sections.
    # When a note targets a section that already has a baseline hash,
    # that section needs to rerun alignment with the new input.
    # Setting the flag is mechanical — the main loop's requeue logic
    # determines which sections actually changed (hash comparison).
    baseline_hash_dir = planspace / "artifacts" / "section-inputs-hashes"
    completed_targets = [
        t for t, _r, _cr, _nm in impacted_sections
        if (baseline_hash_dir / f"{t}.hash").exists()
    ]
    if completed_targets:
        _set_alignment_changed_flag(planspace)
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
    artifacts = planspace / "artifacts"
    notes_dir = artifacts / "notes"
    sec_num = section.number

    if not notes_dir.exists():
        return ""

    note_pattern = f"from-*-to-{sec_num}.md"
    note_files = sorted(notes_dir.glob(note_pattern))

    if not note_files:
        return ""

    # P13: Load acknowledged note IDs for filtering.
    # Only filter notes that were accepted (resolved) or deferred.
    # Rejected notes remain visible so the section sees the disagreement.
    ack_path = (artifacts / "signals" / f"note-ack-{sec_num}.json")
    resolved_ids: set[str] = set()
    if ack_path.exists():
        try:
            ack_data = json.loads(ack_path.read_text(encoding="utf-8"))
            for entry in ack_data.get("acknowledged", []):
                nid = entry.get("note_id", "")
                action = entry.get("action", "accepted")
                if nid and action in ("accepted", "deferred"):
                    resolved_ids.add(nid)
        except (json.JSONDecodeError, OSError) as exc:
            # Preserve corrupted note-ack for diagnosis
            malformed_path = ack_path.with_suffix(".malformed.json")
            try:
                ack_path.rename(malformed_path)
            except OSError:
                pass  # Best-effort preserve
            log(
                f"Section {sec_num}: note-ack malformed ({exc}) — "
                f"preserved as {malformed_path.name}, treating as "
                f"no acknowledgements"
            )

    log(f"Section {sec_num}: found {len(note_files)} incoming notes"
        + (f" ({len(resolved_ids)} resolved)" if resolved_ids else ""))

    parts: list[str] = []
    for note_path in note_files:
        note_text = note_path.read_text(encoding="utf-8")

        # P13: Skip resolved notes (accepted or deferred).
        # Rejected notes are NOT filtered — the section must see them.
        note_id_match = re.search(
            r'\*\*Note ID\*\*:\s*`([^`]+)`', note_text)
        if note_id_match and note_id_match.group(1) in resolved_ids:
            continue

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
    decisions_file = (planspace / "artifacts" / "decisions"
                      / f"section-{section_number}.md")
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
    from .decisions import Decision, load_decisions, record_decision

    decisions_dir = planspace / "artifacts" / "decisions"
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


