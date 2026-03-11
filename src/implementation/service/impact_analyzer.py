"""Impact analysis pipeline for cross-section completion."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from dispatch.service.prompt_safety import validate_dynamic_content, write_validated_prompt
from signals.service.communication import (
    AGENT_NAME,
    DB_SH,
    _log_artifact,
    log,
)
from orchestrator.service.context_assembly import materialize_context_sidecar
from dispatch.engine.section_dispatch import dispatch_agent
from orchestrator.path_registry import PathRegistry
from taskrouter.agents import resolve_agent_path

if TYPE_CHECKING:
    from orchestrator.types import Section

MaterialImpact = tuple[str, str, bool, str]


def build_section_number_map(sections: list[Section]) -> dict[int, str]:
    """Build a mapping from int section number to canonical string form."""
    return {int(section.number): section.number for section in sections}


def normalize_section_number(
    raw_num: str,
    sec_num_map: dict[int, str],
) -> str:
    """Normalize a parsed section number to its canonical form."""
    try:
        return sec_num_map.get(int(raw_num), raw_num)
    except ValueError:
        return raw_num


def collect_impact_candidates(
    planspace: Path,
    section_number: str,
    modified_files: list[str],
    all_sections: list[Section],
) -> list[Section]:
    """Return mechanically-derived candidate sections for impact analysis."""
    artifacts = PathRegistry(planspace).artifacts
    other_sections = [section for section in all_sections if section.number != section_number]
    notes_dir = artifacts / "notes"
    contracts_dir = artifacts / "contracts"
    modified_set = set(modified_files)

    source_inputs = artifacts / "inputs" / f"section-{section_number}"
    source_refs = set()
    if source_inputs.is_dir():
        source_refs = {entry.name for entry in source_inputs.iterdir() if entry.suffix == ".ref"}

    candidates: list[Section] = []
    for other in other_sections:
        other_files = set(other.related_files)
        if modified_set & other_files:
            candidates.append(other)
            continue

        note_path = notes_dir / f"from-{section_number}-to-{other.number}.md"
        if note_path.exists():
            candidates.append(other)
            continue

        other_snapshot = artifacts / "snapshots" / f"section-{other.number}"
        if other_snapshot.exists():
            snapshot_match = False
            for mod_file in modified_files:
                if (other_snapshot / mod_file).exists():
                    candidates.append(other)
                    snapshot_match = True
                    break
            if snapshot_match:
                continue

        if source_refs:
            other_inputs = artifacts / "inputs" / f"section-{other.number}"
            if other_inputs.is_dir():
                other_refs = {entry.name for entry in other_inputs.iterdir() if entry.suffix == ".ref"}
                if source_refs & other_refs:
                    candidates.append(other)
                    continue

        if contracts_dir.is_dir():
            fwd = contracts_dir / f"contract-{section_number}-{other.number}.md"
            rev = contracts_dir / f"contract-{other.number}-{section_number}.md"
            if fwd.exists() or rev.exists():
                candidates.append(other)

    return candidates


def analyze_impacts(
    planspace: Path,
    section_number: str,
    section_summary: str,
    modified_files: list[str],
    all_sections: list[Section],
    codespace: Path,
    parent: str,
    *,
    summary_reader: Callable[[Path], str],
    impact_model: str,
    normalizer_model: str,
) -> list[MaterialImpact]:
    """Run the full impact analysis pipeline and return material impacts."""
    artifacts = PathRegistry(planspace).artifacts
    other_sections = [section for section in all_sections if section.number != section_number]
    if not other_sections:
        log(f"Section {section_number}: no other sections to check for impact")
        return []

    candidate_sections = collect_impact_candidates(
        planspace, section_number, modified_files, all_sections,
    )
    if not candidate_sections:
        log(f"Section {section_number}: no candidate sections for impact analysis")
        return []

    log(
        f"Section {section_number}: {len(candidate_sections)} candidate sections "
        f"(of {len(other_sections)} total) for impact analysis",
    )

    changes_text = "\n".join(f"- `{rel_path}`" for rel_path in modified_files) or "(none)"
    candidate_lines = []
    for other in candidate_sections:
        if other.related_files:
            files_str = ", ".join(f"`{path}`" for path in other.related_files[:10])
            if len(other.related_files) > 10:
                files_str += f" (+{len(other.related_files) - 10} more)"
        else:
            files_str = "(no current file hypothesis)"
        candidate_lines.append(
            f"- SECTION-{other.number}: {summary_reader(other.path)}\n"
            f"  Related files: {files_str}",
        )
    candidate_text = "\n".join(candidate_lines)

    skipped_nums = sorted(
        section.number for section in other_sections if section not in candidate_sections
    )
    skipped_note = ""
    if skipped_nums:
        skipped_note = (
            "\n\n**Not evaluated** (no seam signals — file overlap, prior notes, "
            "snapshots, shared refs, or contract artifacts): "
            f"sections {', '.join(skipped_nums)}"
        )

    impact_prompt_path = artifacts / f"impact-{section_number}-prompt.md"
    impact_output_path = artifacts / f"impact-{section_number}-output.md"
    impact_prompt_text = f"""# Task: Semantic Impact Analysis for Section {section_number}

## What Section {section_number} Did
{section_summary}

## Files Modified by Section {section_number}
{changes_text}

## Candidate Sections (pre-filtered by seam signals)
{candidate_text}
{skipped_note}

## Instructions

These sections were pre-selected because they share modified files, have
existing cross-section notes, have overlapping snapshots, share input refs,
or have contract artifacts linking them to section {section_number}.
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
"""
    if not write_validated_prompt(impact_prompt_text, impact_prompt_path):
        return []

    sidecar_path = materialize_context_sidecar(
        str(resolve_agent_path("impact-analyzer.md")),
        planspace,
        section=section_number,
    )
    if sidecar_path:
        with impact_prompt_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Scoped Context\n"
                "Agent context sidecar with resolved inputs: "
                f"`{sidecar_path}`\n",
            )
    _log_artifact(planspace, f"prompt:impact-{section_number}")

    violations = validate_dynamic_content(
        impact_prompt_path.read_text(encoding="utf-8"),
    )
    if violations:
        log(
            f"Section {section_number}: impact prompt safety violation: "
            f"{violations} — skipping dispatch",
        )
        return []

    log(f"Section {section_number}: running impact analysis")
    subprocess.run(  # noqa: S603
        [
            "bash",
            str(DB_SH),
            "log",
            str(planspace / "run.db"),
            "summary",
            f"glm-explore:{section_number}",
            "impact analysis",
            "--agent",
            AGENT_NAME,
        ],
        capture_output=True,
        text=True,
    )

    impact_result = dispatch_agent(
        impact_model,
        impact_prompt_path,
        impact_output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file="impact-analyzer.md",
    )

    sec_num_map = build_section_number_map(all_sections)
    impacted_sections = _parse_material_impacts(impact_result, sec_num_map)
    if impacted_sections is not None:
        if not impacted_sections:
            log(f"Section {section_number}: no material impacts on other sections")
        else:
            log(
                f"Section {section_number}: material impact on sections "
                f"{[section for section, _reason, _risk, _note in impacted_sections]}",
            )
        return impacted_sections

    log(
        f"Section {section_number}: impact analysis did not produce valid "
        "JSON — dispatching GLM to normalize raw output",
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        dir=str(artifacts),
        prefix=f"impact-normalize-{section_number}-raw-",
        delete=False,
    ) as raw_handle:
        raw_handle.write(impact_result)
        raw_path = Path(raw_handle.name)

    normalize_prompt_path = artifacts / f"impact-normalize-{section_number}-prompt.md"
    normalize_output_path = artifacts / f"impact-normalize-{section_number}-output.md"
    normalize_prompt_text = f"""# Task: Normalize Impact Analysis Output

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
"""
    if not write_validated_prompt(normalize_prompt_text, normalize_prompt_path):
        return []

    normalize_result = dispatch_agent(
        normalizer_model,
        normalize_prompt_path,
        normalize_output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file="impact-output-normalizer.md",
    )
    impacted_sections = _parse_material_impacts(normalize_result, sec_num_map)
    if impacted_sections is None:
        log(
            f"Section {section_number}: GLM normalizer also failed to "
            "produce valid JSON — no material impacts recorded",
        )
        return []
    if not impacted_sections:
        log(f"Section {section_number}: no material impacts on other sections")
        return []

    log(
        f"Section {section_number}: material impact on sections "
        f"{[section for section, _reason, _risk, _note in impacted_sections]}",
    )
    return impacted_sections


def _parse_material_impacts(
    output: str,
    sec_num_map: dict[int, str],
) -> list[MaterialImpact] | None:
    json_text = _extract_json_block(output, marker='"impacts"')
    if json_text is None:
        return None

    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        return None

    impacts: list[MaterialImpact] = []
    try:
        for entry in data.get("impacts", []):
            if entry.get("impact") != "MATERIAL":
                continue
            impacts.append((
                normalize_section_number(str(entry["to"]), sec_num_map),
                entry.get("reason", ""),
                bool(entry.get("contract_risk", False)),
                entry.get("note_markdown", ""),
            ))
    except (KeyError, TypeError):
        return None
    return impacts


def _extract_json_block(output: str, *, marker: str) -> str | None:
    in_fence = False
    fence_lines: list[str] = []
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") and not in_fence:
            in_fence = True
            fence_lines = []
            continue
        if stripped.startswith("```") and in_fence:
            candidate = "\n".join(fence_lines)
            if marker in candidate:
                return candidate
            in_fence = False
            continue
        if in_fence:
            fence_lines.append(line)

    start = output.find("{")
    end = output.rfind("}")
    if start >= 0 and end > start:
        candidate = output[start:end + 1]
        if marker in candidate:
            return candidate
    return None
