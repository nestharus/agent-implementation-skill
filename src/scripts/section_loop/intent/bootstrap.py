"""Intent bootstrap: ensure philosophy and per-section intent packs exist."""

from pathlib import Path

from lib.core.artifact_io import write_json
from lib.core.hash_service import content_hash, file_hash
from lib.intent import philosophy_bootstrap as _philosophy_bootstrap
from lib.core.path_registry import PathRegistry

from ..communication import _log_artifact, log
from ..dispatch import (
    dispatch_agent, read_agent_signal, read_model_policy,
)
from ..types import Section
from prompt_safety import write_validated_prompt


def _sync_philosophy_bootstrap_overrides() -> None:
    """Propagate monkeypatched wrapper dependencies into the lib module."""
    _philosophy_bootstrap.dispatch_agent = dispatch_agent
    _philosophy_bootstrap.read_agent_signal = read_agent_signal
    _philosophy_bootstrap.read_model_policy = read_model_policy
    _philosophy_bootstrap.write_validated_prompt = write_validated_prompt


def _walk_md_bounded(
    root: Path,
    *,
    max_depth: int,
    exclude_top_dirs: frozenset[str] = frozenset(),
    extensions: frozenset[str] = frozenset({".md"}),
):
    return _philosophy_bootstrap.walk_md_bounded(
        root,
        max_depth=max_depth,
        exclude_top_dirs=exclude_top_dirs,
        extensions=extensions,
    )


def _build_philosophy_catalog(
    planspace: Path,
    codespace: Path,
    *,
    max_files: int = 50,
    max_size_kb: int = 100,
    max_depth: int = 3,
    extensions: frozenset[str] = frozenset({".md"}),
) -> list[dict]:
    return _philosophy_bootstrap.build_philosophy_catalog(
        planspace,
        codespace,
        max_files=max_files,
        max_size_kb=max_size_kb,
        max_depth=max_depth,
        extensions=extensions,
    )


def _validate_philosophy_grounding(
    philosophy_path: Path,
    source_map_path: Path,
    artifacts: Path,
) -> bool:
    return _philosophy_bootstrap.validate_philosophy_grounding(
        philosophy_path,
        source_map_path,
        artifacts,
    )


def _sha256_file(path: Path) -> str:
    return _philosophy_bootstrap.sha256_file(path)


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
        content_hash(incoming_notes),
    ]
    combined = ":".join(parts)
    return content_hash(combined)


def ensure_global_philosophy(
    planspace: Path,
    codespace: Path,
    parent: str,
) -> Path | None:
    _sync_philosophy_bootstrap_overrides()
    return _philosophy_bootstrap.ensure_global_philosophy(
        planspace,
        codespace,
        parent,
    )


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
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    sec = section.number
    intent_sec = paths.intent_section_dir(sec)
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

    pack_prompt_text = f"""# Task: Generate Intent Pack for Section {sec}

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
"""
    if not write_validated_prompt(pack_prompt_text, prompt_path):
        return None
    _log_artifact(planspace, f"prompt:intent-pack-{sec}")

    result = dispatch_agent(
        policy.get("intent_pack", "gpt-5.4-high"),
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
        write_json(
            registry_path,
            {"section": sec, "next_id": 1, "surfaces": []},
        )

    if problem_path.exists() and rubric_path.exists():
        log(f"Section {sec}: intent pack generated")
        # V3/R59: Write input hash so future runs can detect changes
        hash_file.write_text(input_hash, encoding="utf-8")
    else:
        log(f"Section {sec}: intent pack generation incomplete")

    return intent_sec
