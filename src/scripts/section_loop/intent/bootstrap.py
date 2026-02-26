"""Intent bootstrap: ensure philosophy and per-section intent packs exist."""

import json
from pathlib import Path

from ..communication import _log_artifact, log
from ..dispatch import (
    dispatch_agent, read_agent_signal, read_model_policy,
)
from ..types import Section


def _build_philosophy_catalog(
    planspace: Path,
    codespace: Path,
    *,
    max_files: int = 50,
    max_size_kb: int = 100,
    max_depth: int = 3,
) -> list[dict]:
    """Build a mechanical catalog of candidate philosophy source files.

    Collects markdown docs within bounded depth/size from planspace and
    codespace. No semantic filtering — purely mechanical collection.
    Returns a list of ``{path, size_kb, first_lines}`` entries.
    """
    candidates: list[dict] = []
    seen: set[str] = set()

    for root_dir in (planspace, codespace):
        if not root_dir.exists():
            continue
        for md_file in sorted(root_dir.rglob("*.md")):
            # Depth check
            try:
                rel = md_file.relative_to(root_dir)
            except ValueError:
                continue
            if len(rel.parts) > max_depth:
                continue
            # Size check
            try:
                size = md_file.stat().st_size
            except OSError:
                continue
            if size == 0 or size > max_size_kb * 1024:
                continue
            # Dedup by resolved path
            resolved = str(md_file.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            # Read first N lines for catalog preview
            try:
                lines = md_file.read_text(encoding="utf-8").splitlines()[:10]
            except (OSError, UnicodeDecodeError):
                continue
            candidates.append({
                "path": str(md_file),
                "size_kb": round(size / 1024, 1),
                "first_lines": "\n".join(lines),
            })
            if len(candidates) >= max_files:
                return candidates

    return candidates


def ensure_global_philosophy(
    planspace: Path,
    codespace: Path,
    parent: str,
) -> Path | None:
    """Ensure the operational philosophy exists; distill if missing.

    Returns the path to ``artifacts/intent/global/philosophy.md``,
    or ``None`` if no philosophy source was found (fail-closed).
    """
    policy = read_model_policy(planspace)
    artifacts = planspace / "artifacts"
    intent_global = artifacts / "intent" / "global"
    intent_global.mkdir(parents=True, exist_ok=True)
    philosophy_path = intent_global / "philosophy.md"

    if philosophy_path.exists() and philosophy_path.stat().st_size > 0:
        return philosophy_path

    # V2/R56: Build mechanical catalog of candidate docs, then let an
    # agent select which ones are philosophy sources. No hardcoded
    # filename assumptions in scripts.
    catalog = _build_philosophy_catalog(planspace, codespace)
    if not catalog:
        log("Intent bootstrap: no markdown files found for philosophy "
            "catalog — skipping distillation (fail-closed)")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal = {
            "state": "philosophy_source_missing",
            "detail": (
                "No markdown files found in planspace or codespace. "
                "Intent mode will downgrade to lightweight."
            ),
        }
        (signal_dir / "philosophy-source-missing.json").write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return None

    # Write catalog for source selector agent
    catalog_path = artifacts / "philosophy-candidate-catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, indent=2), encoding="utf-8")

    # Dispatch source selector to pick philosophy files from catalog
    selector_prompt = artifacts / "philosophy-select-prompt.md"
    selector_output = artifacts / "philosophy-select-output.md"
    selected_signal = (
        artifacts / "signals" / "philosophy-selected-sources.json"
    )
    selected_signal.parent.mkdir(parents=True, exist_ok=True)

    selector_prompt.write_text(f"""# Task: Select Philosophy Source Files

## Context
Select which files from the candidate catalog contain execution
philosophy, design constraints, or operational principles that should
be distilled into the project's operational philosophy.

## Input
Read the candidate catalog at: `{catalog_path}`

Each entry has a path, size, and first 10 lines as a preview.

## Selection Criteria
- Files that describe HOW to build (design principles, constraints,
  operational rules) — not WHAT to build (requirements, specs)
- Files that contain explicit principles, constraints, or philosophy
- Prefer fewer, higher-quality sources over many marginal ones
- Select 1-10 files maximum

## Output
Write a JSON signal to: `{selected_signal}`

```json
{{
  "sources": [
    {{"path": "...", "reason": "Contains design constraints"}}
  ]
}}
```

If NO files contain philosophy or constraints, write:
```json
{{"sources": []}}
```
""", encoding="utf-8")
    _log_artifact(planspace, "prompt:philosophy-select")

    dispatch_agent(
        policy.get("intent_philosophy_selector", "glm"),
        selector_prompt,
        selector_output,
        planspace,
        parent,
        codespace=codespace,
        agent_file="philosophy-source-selector.md",
    )

    # Read selected sources; fail-closed on malformed/missing
    selected = read_agent_signal(selected_signal)
    if not selected or not selected.get("sources"):
        log("Intent bootstrap: source selector found no philosophy "
            "files — skipping distillation (fail-closed)")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal = {
            "state": "philosophy_source_missing",
            "detail": (
                "Source selector found no philosophy files in the "
                "candidate catalog. Intent mode will downgrade to "
                "lightweight."
            ),
        }
        (signal_dir / "philosophy-source-missing.json").write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return None

    sources = [
        Path(s["path"]) for s in selected["sources"]
        if Path(s["path"]).exists()
    ]
    if not sources:
        log("Intent bootstrap: selected source paths do not exist — "
            "skipping distillation (fail-closed)")
        return None

    log(f"Intent bootstrap: distilling operational philosophy from "
        f"{len(sources)} agent-selected source(s)")

    prompt_path = artifacts / "philosophy-distill-prompt.md"
    output_path = artifacts / "philosophy-distill-output.md"
    source_map_path = intent_global / "philosophy-source-map.json"

    sources_block = "\n".join(f"- `{s}`" for s in sources)
    prompt_path.write_text(f"""# Task: Distill Operational Philosophy

## Context
Convert the execution philosophy into an operational philosophy document
that alignment agents can use for per-section philosophy checks.

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
Format: JSON mapping principle ID to source file/section.

## Rules
- Keep principles short and operational (1-2 sentences each)
- Number them P1..PN for machine-stable references
- Note known tensions between principles explicitly
- Include expansion guidance: what classifies as absorbable vs tension vs contradiction
- Do NOT invent principles — every principle must trace to one of the source files
""", encoding="utf-8")
    _log_artifact(planspace, "prompt:philosophy-distill")

    result = dispatch_agent(
        policy.get("intent_philosophy", "claude-opus"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        agent_file="philosophy-distiller.md",
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return philosophy_path

    if not philosophy_path.exists() or philosophy_path.stat().st_size == 0:
        log("Intent bootstrap: philosophy distillation failed — "
            "no output (fail-closed, downgrading to lightweight)")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal = {
            "state": "philosophy_distillation_failed",
            "detail": (
                "Philosophy distiller did not produce output despite "
                "source files being available. Intent mode will "
                "downgrade to lightweight."
            ),
            "sources": [str(s) for s in sources],
        }
        (signal_dir / "philosophy-distillation-failed.json").write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return None

    return philosophy_path


def generate_intent_pack(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    incoming_notes: str = "",
) -> Path:
    """Generate the per-section intent pack (problem.md + rubric).

    Returns the path to the section's intent directory.
    """
    policy = read_model_policy(planspace)
    artifacts = planspace / "artifacts"
    sec = section.number
    intent_sec = artifacts / "intent" / "sections" / f"section-{sec}"
    intent_sec.mkdir(parents=True, exist_ok=True)

    problem_path = intent_sec / "problem.md"
    rubric_path = intent_sec / "problem-alignment.md"

    # If both exist with content, skip regeneration
    if (problem_path.exists() and problem_path.stat().st_size > 0
            and rubric_path.exists() and rubric_path.stat().st_size > 0):
        log(f"Section {sec}: intent pack already exists — skipping generation")
        return intent_sec

    log(f"Section {sec}: generating intent pack")

    # Gather input references
    sections_dir = artifacts / "sections"
    proposal_excerpt = sections_dir / f"section-{sec}-proposal-excerpt.md"
    alignment_excerpt = sections_dir / f"section-{sec}-alignment-excerpt.md"
    problem_frame = sections_dir / f"section-{sec}-problem-frame.md"
    codemap_path = artifacts / "codemap.md"
    philosophy_path = artifacts / "intent" / "global" / "philosophy.md"
    todos_path = artifacts / "todos" / f"section-{sec}-todos.md"

    inputs_block = f"1. Section spec: `{section.path}`\n"
    if proposal_excerpt.exists():
        inputs_block += f"2. Proposal excerpt: `{proposal_excerpt}`\n"
    if alignment_excerpt.exists():
        inputs_block += f"3. Alignment excerpt: `{alignment_excerpt}`\n"
    if problem_frame.exists():
        inputs_block += f"4. Problem frame: `{problem_frame}`\n"
    corrections_path = artifacts / "signals" / "codemap-corrections.json"
    if codemap_path.exists():
        inputs_block += f"5. Codemap: `{codemap_path}`\n"
        if corrections_path.exists():
            inputs_block += (
                f"   Codemap corrections (authoritative fixes): "
                f"`{corrections_path}`\n"
            )
    if philosophy_path.exists():
        inputs_block += f"6. Operational philosophy: `{philosophy_path}`\n"
    if todos_path.exists():
        inputs_block += f"7. TODOs: `{todos_path}`\n"

    file_list = "\n".join(
        f"- `{codespace / rp}`" for rp in section.related_files
    )

    notes_block = ""
    if incoming_notes:
        notes_file = artifacts / f"intent-pack-{sec}-notes.md"
        notes_file.write_text(incoming_notes, encoding="utf-8")
        notes_block = f"\n8. Incoming notes: `{notes_file}`\n"

    prompt_path = artifacts / f"intent-pack-{sec}-prompt.md"
    output_path = artifacts / f"intent-pack-{sec}-output.md"

    prompt_path.write_text(f"""# Task: Generate Intent Pack for Section {sec}

## Files to Read
{inputs_block}{notes_block}

## Related Files
{file_list}

## Output Files

### 1. Problem Definition → `{problem_path}`

Structure:
```md
# Problem Definition — Section {sec}

## Problem statement (seed)
<from problem frame + excerpts>

## Constraints (seed)
<explicit constraints from alignment>

## Axes

### §A1 <axis title>
- **Core difficulty**: ...
- **Evidence**: ...
- **Constraints**: ...
- **Success criteria**: ...
- **Out of scope**: ...

### §A2 ...
```

### 2. Problem Alignment Rubric → `{rubric_path}`

Structure:
```md
# Problem Alignment Rubric — Section {sec}

## Method
Axis alignment pass → per-axis coherence check → surface discovery

## Axis reference

| Axis ID | Axis | Problem Definition Anchor |
|---------|------|--------------------------|
| A1 | <title> | §A1 |
| A2 | <title> | §A2 |
```

### 3. (Optional) Philosophy Excerpt → `{intent_sec / "philosophy-excerpt.md"}`

If the operational philosophy has 10+ principles, write a focused excerpt
with only the 5-12 most relevant principles for this section.

## Axis Selection Guidance

Select axes based on evidence from the section spec, excerpts, code
context, and problem frame. Each axis should represent a dimension
where the solution could independently succeed or fail.

Do not treat axes as a checklist. Include only axes justified by
evidence in the provided inputs. Missing common axes (like error
handling or testing) can be a signal that those dimensions are not
relevant to this section — that is fine.

Each axis describes a CORE DIFFICULTY, not a solution wishlist.

## Initialize Surface Registry

Write an empty surface registry to: `{intent_sec / "surface-registry.json"}`
```json
{{"section": "{sec}", "next_id": 1, "surfaces": []}}
```
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:intent-pack-{sec}")

    result = dispatch_agent(
        policy.get("intent_pack", "gpt-5.3-codex-high"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        section_number=sec,
        agent_file="intent-pack-generator.md",
    )

    if result == "ALIGNMENT_CHANGED_PENDING":
        return intent_sec

    # Ensure surface registry exists
    registry_path = intent_sec / "surface-registry.json"
    if not registry_path.exists():
        registry_path.write_text(
            json.dumps({"section": sec, "next_id": 1, "surfaces": []},
                       indent=2),
            encoding="utf-8",
        )

    if problem_path.exists() and rubric_path.exists():
        log(f"Section {sec}: intent pack generated")
    else:
        log(f"Section {sec}: intent pack generation incomplete")

    return intent_sec
