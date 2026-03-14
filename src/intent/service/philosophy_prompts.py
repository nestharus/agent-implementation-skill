"""Prompt text composers for philosophy bootstrap stages.

Pure text-building functions with no side effects — each takes data
parameters and returns a prompt string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def compose_bootstrap_guidance_text(artifacts_block: str, guidance_path: Path) -> str:
    """Build the bootstrap guidance prompt text."""
    return f"""# Task: Generate Optional Philosophy Bootstrap Guidance

## Context
The repository bootstrap confirmed that no authoritative philosophy
source is currently usable. The user must provide philosophy input in
their own words. Your job is to surface project-shaped tensions that may
help the user articulate that philosophy.

## Available Project-Shaping Artifacts
{artifacts_block}

Read only what you need. Guidance must be shaped by these artifacts,
not by generic software doctrine.

## Output
Write JSON to: `{guidance_path}`

```json
{{
  "project_frame": "Brief summary of the project context relevant to philosophy",
  "prompts": [
    {{
      "prompt": "How should the system handle uncertainty in this project?",
      "why_this_matters": "Project materials suggest risk around acting before certainty."
    }}
  ],
  "notes": [
    "These prompts are optional guidance, not required categories.",
    "Write philosophy in any form — prose, bullets, fragments, examples."
  ]
}}
```

## Rules
- Do NOT decide the philosophy for the user
- Do NOT require a fixed response shape
- Prefer 2-6 prompts that surface likely tensions specific to this project
- Focus on reasoning principles, tradeoffs, authority boundaries, uncertainty handling, escalation, and scope doctrine
- Avoid implementation tactics, framework choices, and feature requirements
- If the artifacts do not support meaningful project-shaped prompts, write an empty `prompts` list and explain the context in `project_frame`
"""


def compose_source_selector_text(catalog_path: Path, selected_signal: Path) -> str:
    """Build the source selector prompt text."""
    return f"""# Task: Select Philosophy Source Files

## Context
Select which files from the candidate catalog contain execution
philosophy that should be distilled into the project's operational
philosophy.

Philosophy means cross-cutting reasoning about how the system should
think before it knows what to build: tradeoff rules, uncertainty rules,
escalation rules, authority boundaries, exploration doctrine, scope
doctrine, and durable strategic constraints.

## Input
Read the candidate catalog at: `{catalog_path}`

Each entry includes:
- `path`
- `size_kb`
- `preview_start` (first 15 lines)
- `preview_middle` (excerpt from the middle of the file)
- `headings` (all markdown headings found mechanically)

The previews are starting points only. You MAY read candidate files
directly before deciding. Do not use script-side heuristics; make the
semantic decision yourself from the catalog and any file reads you do.

## Selection Criteria
- Include only files that contain cross-cutting reasoning philosophy
- Exclude feature specs, API or schema docs, local architecture plans,
  framework choices, coding-style notes, checklists, and file-level
  tactics unless a specific section states durable doctrine
- Mixed documents are allowed only when your reason cites the exact
  philosophy-bearing section(s)
- Prefer fewer, higher-quality sources over many marginal ones
- Select 1-10 files maximum

## Output
Write a JSON signal to: `{selected_signal}`

```json
{{
  "status": "selected",
  "sources": [
    {{"path": "...", "reason": "Tradeoffs and Escalation sections define cross-cutting decision rules"}}
  ],
  "ambiguous": [
    {{"path": "...", "reason": "Preview suggests uncertainty-handling doctrine, but exact philosophy-bearing section is unclear"}}
  ],
  "additional_extensions": [".txt", ".rst"]
}}
```

The ``ambiguous`` field is **optional**. Include it only when the
catalog previews are genuinely insufficient to classify a candidate.
All selected sources plus any ambiguous candidates will be sent for
full-read verification. Do not nominate files you can classify from
the catalog and direct file reads.

The ``additional_extensions`` field is **optional**. Include it only
if you believe philosophy sources may exist in non-markdown formats
that were not included in the catalog. The catalog will be rebuilt
with these extensions and you will be re-invoked once.

If NO files contain cross-cutting reasoning philosophy, write:
```json
{{"status": "empty", "sources": []}}
```
"""


def compose_verify_sources_text(candidates_block: str, verify_signal: Path) -> str:
    """Build the source verifier prompt text."""
    return f"""# Task: Verify Shortlisted Philosophy Sources

## Context
The source selector shortlisted these files as possible philosophy
sources for a project-wide invariant. Read EACH file in full and
confirm whether it contains execution philosophy.

Philosophy means cross-cutting reasoning about how the system should
think before it knows what to build: tradeoff rules, uncertainty rules,
escalation rules, authority boundaries, exploration doctrine, scope
doctrine, and durable strategic constraints.

## Candidates
{candidates_block}

## Instructions
For each candidate, read the FULL file and classify:
- **philosophy_source**: Contains cross-cutting reasoning philosophy.
  If mixed, cite the exact section(s) that justify inclusion.
- **not_philosophy**: Specification, requirements, architecture plans,
  implementation tactics, or irrelevant content without cross-cutting
  reasoning philosophy.

The verifier is authoritative. Every shortlisted file must be checked,
even if the selector already chose it.

## Output
Write a JSON signal to: `{verify_signal}`

```json
{{{{
  "verified_sources": [
    {{{{"path": "...", "reason": "Tradeoffs section contains cross-cutting reasoning philosophy"}}}}
  ],
  "rejected": [
    {{{{"path": "...", "reason": "Implementation plan only; no cross-cutting reasoning philosophy"}}}}
  ]
}}}}
```
"""


def compose_distiller_text(
    *,
    sources: list[dict[str, Any]],
    philosophy_path: Path,
    source_map_path: Path,
    decisions_path: Path,
) -> str:
    """Build the distiller prompt text from sources and paths."""
    sources_block = "\n".join(
        f"- `{source['path']}` (source_type: `{source['source_type']}`)"
        for source in sources
    )
    return f"""# Task: Distill Operational Philosophy

## Context
Convert the execution philosophy into an operational philosophy document
that alignment agents can use for per-section philosophy checks.

Philosophy means cross-cutting reasoning about how the system should
think before it knows what to build: tradeoff rules, uncertainty rules,
escalation rules, authority boundaries, exploration doctrine, scope
doctrine, and durable strategic constraints.

## Input
Read these philosophy source files:
{sources_block}

If a philosophy artifact already exists at `{philosophy_path}`, skip this task.

## Output
Write an operational philosophy to: `{philosophy_path}`

Structure:
1. Numbered principles (P1, P2, ...) — short, actionable
2. Interactions between principles (which ones tension with each other)
3. Expansion guidance (how new principles get added)

Write a source map to: `{source_map_path}`
Format: JSON mapping principle ID to an object with `source_type`,
`source_file`, and `source_section`.

If you are reading a user-authored bootstrap source and cannot extract
stable principles because the input is too thin, contradictory, or
genuinely ambiguous, do NOT invent filler. Instead:
- Rewrite `{decisions_path}` with concise follow-up clarification questions
- Leave `{philosophy_path}` empty
- Write `{{}}` to `{source_map_path}`

## Rules
- Extract only durable principles that apply across multiple tasks
- Extract only principles that constrain future decisions
- Extract only principles that are testable in alignment review
- Exclude implementation details unless they express genuine
  cross-cutting doctrine
- Keep principles short and operational (1-2 sentences each)
- Number them P1..PN for machine-stable references
- Note known tensions between principles explicitly
- Include expansion guidance: what classifies as absorbable vs tension vs contradiction
- Do NOT invent principles — every principle must trace to one of the source files
- Use `source_type: "user_source"` for user-authored bootstrap input and `source_type: "repo_source"` for repository files
- Do NOT target a fixed count; a small real seed philosophy is acceptable
- If the sources contain no extractable philosophy, leave
  `{philosophy_path}` empty and write `{{}}` to `{source_map_path}`
"""
