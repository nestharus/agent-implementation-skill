"""Intent bootstrap: ensure philosophy and per-section intent packs exist."""

from pathlib import Path
from typing import Any

from containers import Services
from intent.service.philosophy_bootstrapper import (
    ensure_global_philosophy as _ensure_global_philosophy,
    sha256_file as _sha256_file_impl,
    validate_philosophy_grounding as _validate_grounding,
)
from intent.service.philosophy_catalog import (
    build_philosophy_catalog as _build_catalog,
    walk_md_bounded as _walk_bounded,
)
from orchestrator.path_registry import PathRegistry

from orchestrator.types import Section



def _walk_md_bounded(
    root: Path,
    *,
    max_depth: int,
    exclude_top_dirs: frozenset[str] = frozenset(),
    extensions: frozenset[str] = frozenset({".md"}),
):
    return _walk_bounded(
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
    return _build_catalog(
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
    return _validate_grounding(
        philosophy_path,
        source_map_path,
        artifacts,
    )


def _sha256_file(path: Path) -> str:
    return _sha256_file_impl(path)


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
        Services.hasher().content_hash(incoming_notes),
    ]
    combined = ":".join(parts)
    return Services.hasher().content_hash(combined)


def ensure_global_philosophy(
    planspace: Path,
    codespace: Path,
    parent: str,
) -> dict[str, Any]:
    return _ensure_global_philosophy(
        planspace,
        codespace,
        parent,
    )


def _check_pack_freshness(
    problem_path: Path,
    rubric_path: Path,
    input_hash: str,
    hash_file: Path,
    sec: str,
) -> bool | None:
    """Check if the intent pack needs regeneration.

    Returns ``True`` if the pack is fresh (skip), ``False`` if stale
    (regenerate), or ``None`` if the pack doesn't exist yet.
    """
    prev_hash = ""
    if hash_file.exists():
        prev_hash = hash_file.read_text(encoding="utf-8").strip()

    both_exist = (
        problem_path.exists() and problem_path.stat().st_size > 0
        and rubric_path.exists() and rubric_path.stat().st_size > 0
    )
    if both_exist and input_hash == prev_hash and prev_hash:
        Services.logger().log(
            f"Section {sec}: intent pack exists, inputs unchanged "
            "— skipping generation"
        )
        return True
    if both_exist:
        Services.logger().log(f"Section {sec}: intent pack inputs changed — regenerating")
        return False
    Services.logger().log(f"Section {sec}: generating intent pack")
    return None


def _build_inputs_block(
    section_path: Path,
    paths: PathRegistry,
    sec: str,
    codespace: Path,
    related_files: list,
    incoming_notes: str,
) -> tuple[str, str, str]:
    """Build the inputs block, file list, and notes block for the prompt.

    Returns (inputs_block, file_list, notes_block).
    """
    proposal_excerpt = paths.proposal_excerpt(sec)
    alignment_excerpt = paths.alignment_excerpt(sec)
    problem_frame = paths.problem_frame(sec)
    codemap_path = paths.codemap()
    corrections_path = paths.corrections()
    philosophy_path = paths.philosophy()
    todos_path = paths.todos(sec)

    inputs_block = f"1. Section spec: `{section_path}`\n"
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
        f"- `{codespace / rp}`" for rp in related_files
    )

    notes_block = ""
    if incoming_notes:
        notes_file = paths.artifacts / f"intent-pack-{sec}-notes.md"
        notes_file.write_text(incoming_notes, encoding="utf-8")
        notes_block = f"\n8. Incoming notes: `{notes_file}`\n"

    return inputs_block, file_list, notes_block


def _compose_intent_pack_text(
    sec: str,
    inputs_block: str,
    file_list: str,
    notes_block: str,
    problem_path: Path,
    rubric_path: Path,
    philosophy_excerpt_path: Path,
    surface_registry_path: Path,
) -> str:
    """Return the intent pack generation prompt text."""
    return f"""# Task: Generate Intent Pack for Section {sec}

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

### 3. (Optional) Philosophy Excerpt → `{philosophy_excerpt_path}`

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

Write an empty surface registry to: `{surface_registry_path}`
```json
{{"section": "{sec}", "next_id": 1, "surfaces": []}}
```
"""


def _build_intent_pack_prompt(
    sec: str,
    inputs_block: str,
    file_list: str,
    notes_block: str,
    problem_path: Path,
    rubric_path: Path,
    intent_sec: Path,
) -> str:
    """Build the intent pack generation prompt."""
    return _compose_intent_pack_text(
        sec=sec,
        inputs_block=inputs_block,
        file_list=file_list,
        notes_block=notes_block,
        problem_path=problem_path,
        rubric_path=rubric_path,
        philosophy_excerpt_path=intent_sec / "philosophy-excerpt.md",
        surface_registry_path=intent_sec / "surface-registry.json",
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
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    sec = section.number
    intent_sec = paths.intent_section_dir(sec)
    intent_sec.mkdir(parents=True, exist_ok=True)

    problem_path = intent_sec / "problem.md"
    rubric_path = intent_sec / "problem-alignment.md"

    input_hash = _compute_intent_pack_hash(
        section_path=section.path,
        proposal_excerpt=paths.proposal_excerpt(sec),
        alignment_excerpt=paths.alignment_excerpt(sec),
        problem_frame=paths.problem_frame(sec),
        codemap_path=paths.codemap(),
        corrections_path=paths.corrections(),
        philosophy_path=paths.philosophy(),
        todos_path=paths.todos(sec),
        incoming_notes=incoming_notes,
    )
    hash_file = intent_sec / "intent-pack-input-hash.txt"

    freshness = _check_pack_freshness(problem_path, rubric_path, input_hash, hash_file, sec)
    if freshness is True:
        return intent_sec

    inputs_block, file_list, notes_block = _build_inputs_block(
        section.path, paths, sec, codespace, section.related_files, incoming_notes,
    )
    prompt_text = _build_intent_pack_prompt(
        sec, inputs_block, file_list, notes_block,
        problem_path, rubric_path, intent_sec,
    )

    prompt_path = paths.artifacts / f"intent-pack-{sec}-prompt.md"
    output_path = paths.artifacts / f"intent-pack-{sec}-output.md"
    if not Services.prompt_guard().write_validated(prompt_text, prompt_path):
        return None
    Services.communicator().log_artifact(planspace, f"prompt:intent-pack-{sec}")

    result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy, "intent_pack"),
        prompt_path, output_path, planspace, parent,
        codespace=codespace, section_number=sec,
        agent_file=Services.task_router().agent_for("intent.pack_generator"),
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return intent_sec

    registry_path = intent_sec / "surface-registry.json"
    if not registry_path.exists():
        Services.artifact_io().write_json(
            registry_path, {"section": sec, "next_id": 1, "surfaces": []},
        )

    if problem_path.exists() and rubric_path.exists():
        Services.logger().log(f"Section {sec}: intent pack generated")
        hash_file.write_text(input_hash, encoding="utf-8")
    else:
        Services.logger().log(f"Section {sec}: intent pack generation incomplete")

    return intent_sec
