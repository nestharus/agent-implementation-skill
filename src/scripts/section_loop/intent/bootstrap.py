"""Intent bootstrap: ensure philosophy and per-section intent packs exist."""

import hashlib
import json
import os
import re
from pathlib import Path

from ..communication import _log_artifact, log
from ..dispatch import (
    dispatch_agent, read_agent_signal, read_model_policy,
)
from ..types import Section


def _walk_md_bounded(
    root: Path,
    *,
    max_depth: int,
    exclude_top_dirs: frozenset[str] = frozenset(),
):
    """Yield ``*.md`` files under *root* with depth-bounded traversal.

    Uses ``os.walk`` with directory pruning so the full tree is never
    materialized.  Entries are sorted per-directory for determinism.
    *exclude_top_dirs* names are pruned at the first level only.
    """
    if not root.is_dir():
        return
    root_s = str(root)
    for dirpath, dirnames, filenames in os.walk(root_s):
        rel = os.path.relpath(dirpath, root_s)
        depth = 0 if rel == "." else rel.count(os.sep) + 1

        # Prune excluded top-level dirs; sort all levels for determinism.
        if depth == 0:
            dirnames[:] = sorted(
                d for d in dirnames if d not in exclude_top_dirs
            )
        else:
            dirnames.sort()

        # Stop descending when children would exceed max_depth.
        if depth + 1 >= max_depth:
            dirnames.clear()

        # Files here have (depth+1) relative-path parts; yield if ≤ max_depth.
        if depth + 1 > max_depth:
            continue

        for fname in sorted(filenames):
            if fname.endswith(".md"):
                yield Path(dirpath) / fname


def _build_philosophy_catalog(
    planspace: Path,
    codespace: Path,
    *,
    max_files: int = 50,
    max_size_kb: int = 100,
    max_depth: int = 3,
) -> list[dict]:
    """Build a mechanical catalog of candidate philosophy source files.

    Uses depth-bounded directory walks (``os.walk`` with pruning) to
    avoid materializing the full file tree.  Per-root quotas guarantee
    codespace coverage.  Planspace ``artifacts/`` is excluded.

    Returns a list of ``{path, size_kb, first_lines}`` entries.
    """
    # V1/R59: Per-root quotas guarantee codespace coverage.
    # V1/R60: Traversal is depth-bounded via os.walk with pruning —
    # the full tree is never materialized or sorted.
    codespace_quota = max(max_files * 4 // 5, 1)  # 80% for codespace
    planspace_quota = max(max_files - codespace_quota, 1)  # 20% for plan

    candidates: list[dict] = []
    seen: set[str] = set()

    for root_dir, quota, exclude_top in (
        (codespace, codespace_quota, frozenset()),
        (planspace, planspace_quota, frozenset({"artifacts"})),
    ):
        root_count = 0
        for md_file in _walk_md_bounded(
            root_dir, max_depth=max_depth, exclude_top_dirs=exclude_top,
        ):
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
            root_count += 1
            if root_count >= quota:
                break

    return candidates


def _validate_philosophy_grounding(
    philosophy_path: Path,
    source_map_path: Path,
    artifacts: Path,
) -> bool:
    """Validate that distilled philosophy is grounded in source files.

    Checks: source map exists, parses as JSON object, and every
    principle ID (``P\\d+``) found in philosophy.md has a mapping entry.
    Writes a ``philosophy-grounding-failed.json`` signal on failure.

    Returns ``True`` if grounding is valid; ``False`` otherwise.
    """
    signal_dir = artifacts / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)
    fail_signal = signal_dir / "philosophy-grounding-failed.json"

    # Check source map exists
    if not source_map_path.exists() or source_map_path.stat().st_size == 0:
        signal = {
            "state": "philosophy_grounding_failed",
            "detail": (
                "Philosophy source map is missing or empty. "
                "Distilled philosophy cannot be verified as grounded. "
                "Intent mode will downgrade to lightweight."
            ),
        }
        fail_signal.write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return False

    # Parse source map
    try:
        source_map = json.loads(
            source_map_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"Intent bootstrap: malformed source map ({exc}) — "
            f"preserving as .malformed.json")
        malformed = source_map_path.with_suffix(".malformed.json")
        try:
            source_map_path.rename(malformed)
        except OSError:
            pass
        signal = {
            "state": "philosophy_grounding_failed",
            "detail": (
                f"Philosophy source map is malformed ({exc}). "
                "Intent mode will downgrade to lightweight."
            ),
        }
        fail_signal.write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return False

    if not isinstance(source_map, dict):
        signal = {
            "state": "philosophy_grounding_failed",
            "detail": (
                "Philosophy source map is not a JSON object. "
                "Intent mode will downgrade to lightweight."
            ),
        }
        fail_signal.write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return False

    # Extract principle IDs from philosophy.md
    try:
        philosophy_text = philosophy_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    principle_ids = set(re.findall(r"\bP\d+\b", philosophy_text))
    if not principle_ids:
        # No principle IDs found — can't validate coverage, pass through
        return True

    # Check coverage: every principle ID must have a source map entry
    map_keys = set(source_map.keys())
    unmapped = principle_ids - map_keys
    if unmapped:
        signal = {
            "state": "philosophy_grounding_failed",
            "detail": (
                f"Principle IDs missing from source map: "
                f"{sorted(unmapped)}. Distilled philosophy may contain "
                f"invented principles. Intent mode will downgrade."
            ),
            "unmapped_principles": sorted(unmapped),
            "total_principles": len(principle_ids),
            "mapped_principles": len(principle_ids - unmapped),
        }
        fail_signal.write_text(
            json.dumps(signal, indent=2), encoding="utf-8")
        return False

    return True


def _sha256_file(path: Path) -> str:
    """Return hex sha256 of file contents, or empty string on error."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _compute_intent_pack_hash(
    *,
    section_path: Path,
    proposal_excerpt: Path,
    alignment_excerpt: Path,
    problem_frame: Path,
    codemap_path: Path,
    corrections_path: Path,
    philosophy_path: Path,
    todos_path: Path,
    incoming_notes: str,
) -> str:
    """Compute a combined hash over all intent pack input files.

    Used for V3/R59 hash-based invalidation — regenerate pack when
    any upstream input changes.
    """
    parts = [
        _sha256_file(section_path),
        _sha256_file(proposal_excerpt),
        _sha256_file(alignment_excerpt),
        _sha256_file(problem_frame),
        _sha256_file(codemap_path),
        _sha256_file(corrections_path),
        _sha256_file(philosophy_path),
        _sha256_file(todos_path),
        hashlib.sha256(incoming_notes.encode()).hexdigest(),
    ]
    combined = ":".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()


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

    # V2/R59: Validate philosophy source grounding — the distiller
    # prompt requires a source map, and we must mechanically verify
    # it exists, parses, and covers all principle IDs.
    grounding_ok = _validate_philosophy_grounding(
        philosophy_path, source_map_path, artifacts)
    if not grounding_ok:
        log("Intent bootstrap: philosophy grounding validation failed "
            "— downgrading to lightweight (fail-closed)")
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

    # Gather input references (needed for both hash check and prompt)
    sections_dir = artifacts / "sections"
    proposal_excerpt = sections_dir / f"section-{sec}-proposal-excerpt.md"
    alignment_excerpt = sections_dir / f"section-{sec}-alignment-excerpt.md"
    problem_frame = sections_dir / f"section-{sec}-problem-frame.md"
    codemap_path = artifacts / "codemap.md"
    corrections_path = artifacts / "signals" / "codemap-corrections.json"
    philosophy_path = artifacts / "intent" / "global" / "philosophy.md"
    todos_path = artifacts / "todos" / f"section-{sec}-todos.md"

    # V3/R59: Hash-based invalidation — regenerate if inputs changed
    # even when problem.md/rubric exist.
    input_hash = _compute_intent_pack_hash(
        section_path=section.path,
        proposal_excerpt=proposal_excerpt,
        alignment_excerpt=alignment_excerpt,
        problem_frame=problem_frame,
        codemap_path=codemap_path,
        corrections_path=corrections_path,
        philosophy_path=philosophy_path,
        todos_path=todos_path,
        incoming_notes=incoming_notes,
    )
    hash_file = intent_sec / "intent-pack-input-hash.txt"
    prev_hash = ""
    if hash_file.exists():
        prev_hash = hash_file.read_text(encoding="utf-8").strip()

    if (problem_path.exists() and problem_path.stat().st_size > 0
            and rubric_path.exists() and rubric_path.stat().st_size > 0
            and input_hash == prev_hash and prev_hash):
        log(f"Section {sec}: intent pack exists, inputs unchanged "
            "— skipping generation")
        return intent_sec

    if (problem_path.exists() and problem_path.stat().st_size > 0
            and rubric_path.exists() and rubric_path.stat().st_size > 0):
        log(f"Section {sec}: intent pack inputs changed — regenerating")
    else:
        log(f"Section {sec}: generating intent pack")

    inputs_block = f"1. Section spec: `{section.path}`\n"
    if proposal_excerpt.exists():
        inputs_block += f"2. Proposal excerpt: `{proposal_excerpt}`\n"
    if alignment_excerpt.exists():
        inputs_block += f"3. Alignment excerpt: `{alignment_excerpt}`\n"
    if problem_frame.exists():
        inputs_block += f"4. Problem frame: `{problem_frame}`\n"
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
        # V3/R59: Write input hash so future runs can detect changes
        hash_file.write_text(input_hash, encoding="utf-8")
    else:
        log(f"Section {sec}: intent pack generation incomplete")

    return intent_sec
