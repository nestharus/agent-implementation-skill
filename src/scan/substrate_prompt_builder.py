"""Prompt builders for the three substrate agents."""

from __future__ import annotations

from pathlib import Path

from orchestrator.path_registry import PathRegistry
from dispatch.prompt_safety import write_validated_prompt


def write_shard_prompt(
    section_num: str,
    section_path: Path,
    planspace: Path,
    codespace: Path,
) -> Path:
    """Write the dispatch prompt for the shard explorer agent."""
    registry = PathRegistry(planspace)
    artifacts = registry.artifacts
    prompts_dir = registry.substrate_prompts_dir()
    prompts_dir.mkdir(parents=True, exist_ok=True)

    output_path = registry.substrate_dir() / "shards" / f"shard-{section_num}.json"
    codemap_path = registry.codemap()
    codemap_corrections_path = registry.corrections()
    proposal_excerpt = registry.proposal_excerpt(section_num)
    alignment_excerpt = registry.alignment_excerpt(section_num)
    problem_frame = registry.problem_frame(section_num)
    intent_problem = registry.intent_section_dir(section_num) / "problem.md"
    intent_rubric = (
        registry.intent_section_dir(section_num) / "problem-alignment.md"
    )

    refs: list[str] = []
    refs.append(f"- **Section spec**: `{section_path}`")
    if proposal_excerpt.exists():
        refs.append(f"- **Proposal excerpt**: `{proposal_excerpt}`")
    if alignment_excerpt.exists():
        refs.append(f"- **Alignment excerpt**: `{alignment_excerpt}`")
    if problem_frame.exists():
        refs.append(f"- **Problem frame**: `{problem_frame}`")
    if codemap_path.exists():
        refs.append(f"- **Codemap**: `{codemap_path}`")
    if codemap_corrections_path.exists():
        refs.append(f"- **Codemap corrections**: `{codemap_corrections_path}`")
    if intent_problem.exists():
        refs.append(f"- **Intent problem**: `{intent_problem}`")
    if intent_rubric.exists():
        refs.append(f"- **Intent rubric**: `{intent_rubric}`")
    refs.append(
        f"- **All section specs** (for cross-reference): "
        f"`{registry.sections_dir()}` directory"
    )

    refs_block = "\n".join(refs)

    prompt_path = prompts_dir / f"shard-{section_num}.md"
    write_validated_prompt(f"""# Shard Explorer: Section {section_num}

## Your Task

Analyze section {section_num} and produce a structured shard JSON.
Your agent definition file defines the output schema and reasoning
method — follow it exactly.

## Files to Read

{refs_block}

## Codespace

The project source code is at: `{codespace}`

Browse the codespace to understand existing patterns, types, and
integration points that this section will interact with.

## Output

Write your shard JSON to: `{output_path}`

Create parent directories as needed. Follow the schema from your
agent definition exactly.
""", prompt_path)

    return prompt_path


def write_pruner_prompt(
    planspace: Path,
    codespace: Path,
    target_sections: list[str],
) -> Path:
    """Write the dispatch prompt for the pruner agent."""
    registry = PathRegistry(planspace)
    prompts_dir = registry.substrate_prompts_dir()
    prompts_dir.mkdir(parents=True, exist_ok=True)

    shards_dir = registry.substrate_dir() / "shards"
    substrate_dir = registry.substrate_dir()
    codemap_path = registry.codemap()
    codemap_corrections_path = registry.corrections()
    proposal_path = registry.artifacts / "proposal.md"
    alignment_path = registry.artifacts / "alignment.md"
    philosophy_path = registry.intent_global_dir() / "philosophy.md"

    sections_list = ", ".join(target_sections)

    refs: list[str] = []
    refs.append(f"- **All shards**: `{shards_dir}/shard-*.json`")
    refs.append(f"- **Section specs**: `{registry.sections_dir()}` directory")
    if proposal_path.exists():
        refs.append(f"- **Global proposal**: `{proposal_path}`")
    if alignment_path.exists():
        refs.append(f"- **Global alignment**: `{alignment_path}`")
    if codemap_path.exists():
        refs.append(f"- **Codemap**: `{codemap_path}`")
    if codemap_corrections_path.exists():
        refs.append(f"- **Codemap corrections**: `{codemap_corrections_path}`")
    if philosophy_path.exists():
        refs.append(f"- **Philosophy**: `{philosophy_path}`")

    refs_block = "\n".join(refs)

    prompt_path = prompts_dir / "pruner.md"
    write_validated_prompt(f"""# Pruner: Strategic Merge

## Your Task

Read all shard files and produce the shared integration substrate.
Your agent definition file defines the three output artifacts and
reasoning method — follow it exactly.

## Target Sections

Only these sections are in scope: {sections_list}

## Files to Read

{refs_block}

## Codespace

The project source code is at: `{codespace}`

## Output

Write three artifacts to `{substrate_dir}/`:

1. `{substrate_dir / "substrate.md"}` — shared problem surface
2. `{substrate_dir / "seed-plan.json"}` — minimal anchors to create
3. `{substrate_dir / "prune-signal.json"}` — structured status (READY or NEEDS_PARENT)

Create parent directories as needed. Follow the schemas from your
agent definition exactly.
""", prompt_path)

    return prompt_path


def write_seeder_prompt(
    planspace: Path,
    codespace: Path,
) -> Path:
    """Write the dispatch prompt for the seeder agent."""
    registry = PathRegistry(planspace)
    prompts_dir = registry.substrate_prompts_dir()
    prompts_dir.mkdir(parents=True, exist_ok=True)

    substrate_dir = registry.substrate_dir()
    seed_plan_path = substrate_dir / "seed-plan.json"
    substrate_md_path = substrate_dir / "substrate.md"
    codemap_path = registry.codemap()
    codemap_corrections_path = registry.corrections()

    refs: list[str] = []
    refs.append(f"- **Seed plan**: `{seed_plan_path}`")
    refs.append(f"- **Substrate document**: `{substrate_md_path}`")
    if codemap_path.exists():
        refs.append(f"- **Codemap**: `{codemap_path}`")
    if codemap_corrections_path.exists():
        refs.append(f"- **Codemap corrections**: `{codemap_corrections_path}`")

    refs_block = "\n".join(refs)

    prompt_path = prompts_dir / "seeder.md"
    write_validated_prompt(f"""# Seeder: Create Anchors and Wire References

## Your Task

Read the seed plan and substrate document, then create anchor files
and wiring artifacts. Your agent definition file defines all four
output types — follow it exactly.

## Files to Read

{refs_block}

## Codespace

Create anchor files under: `{codespace}`

## Output

Your agent definition specifies four outputs:

1. **Anchor files** in codespace at `{codespace}` (paths from seed plan)
2. **Related-files-update signals** at `{registry.related_files_update_dir()}/section-<NN>.json`
3. **Substrate input refs** at `{registry.inputs_dir()}/section-<NN>/substrate.ref` (each containing the absolute path to `{substrate_md_path}`)
4. **Completion signal** at `{substrate_dir / "seed-signal.json"}`

Create parent directories as needed. Follow the schemas from your
agent definition exactly.
""", prompt_path)

    return prompt_path


__all__ = [
    "write_pruner_prompt",
    "write_seeder_prompt",
    "write_shard_prompt",
]
