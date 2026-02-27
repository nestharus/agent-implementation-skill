"""Prompt builders for the three substrate agents.

Each builder writes a dispatch prompt to disk and returns the path.

**Contract boundary**: The agent method file (agents/*.md) defines the
output schema and reasoning method. The dynamic prompt supplies ONLY
runtime paths and context. Prompts must NOT redefine schemas — they
reference the agent file's contract and provide concrete output paths.
"""

from __future__ import annotations

from pathlib import Path


def write_shard_prompt(
    section_num: str,
    section_path: Path,
    planspace: Path,
    codespace: Path,
) -> Path:
    """Write the dispatch prompt for the shard explorer agent.

    Returns the path to the written prompt file.
    """
    artifacts = planspace / "artifacts"
    prompts_dir = artifacts / "substrate" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    output_path = artifacts / "substrate" / "shards" / f"shard-{section_num}.json"
    codemap_path = artifacts / "codemap.md"
    proposal_excerpt = artifacts / "sections" / f"section-{section_num}-proposal-excerpt.md"
    alignment_excerpt = artifacts / "sections" / f"section-{section_num}-alignment-excerpt.md"
    problem_frame = artifacts / "sections" / f"section-{section_num}-problem-frame.md"
    intent_problem = (
        artifacts / "intent" / "sections" / f"section-{section_num}" / "problem.md"
    )
    intent_rubric = (
        artifacts / "intent" / "sections" / f"section-{section_num}" / "problem-alignment.md"
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
    if intent_problem.exists():
        refs.append(f"- **Intent problem**: `{intent_problem}`")
    if intent_rubric.exists():
        refs.append(f"- **Intent rubric**: `{intent_rubric}`")
    refs.append(f"- **All section specs** (for cross-reference): `{artifacts / 'sections'}` directory")

    refs_block = "\n".join(refs)

    prompt_path = prompts_dir / f"shard-{section_num}.md"
    prompt_path.write_text(f"""# Shard Explorer: Section {section_num}

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
agent definition exactly (schema v1 with id/kind/summary fields).
""", encoding="utf-8")

    return prompt_path


def write_pruner_prompt(
    planspace: Path,
    codespace: Path,
    target_sections: list[str],
) -> Path:
    """Write the dispatch prompt for the pruner agent.

    Returns the path to the written prompt file.
    """
    artifacts = planspace / "artifacts"
    prompts_dir = artifacts / "substrate" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    shards_dir = artifacts / "substrate" / "shards"
    substrate_dir = artifacts / "substrate"
    codemap_path = artifacts / "codemap.md"
    proposal_path = artifacts / "proposal.md"
    alignment_path = artifacts / "alignment.md"
    philosophy_path = artifacts / "intent" / "global" / "philosophy.md"

    sections_list = ", ".join(target_sections)

    refs: list[str] = []
    refs.append(f"- **All shards**: `{shards_dir}/shard-*.json`")
    refs.append(f"- **Section specs**: `{artifacts / 'sections'}` directory")
    if proposal_path.exists():
        refs.append(f"- **Global proposal**: `{proposal_path}`")
    if alignment_path.exists():
        refs.append(f"- **Global alignment**: `{alignment_path}`")
    if codemap_path.exists():
        refs.append(f"- **Codemap**: `{codemap_path}`")
    if philosophy_path.exists():
        refs.append(f"- **Philosophy**: `{philosophy_path}`")

    refs_block = "\n".join(refs)

    prompt_path = prompts_dir / "pruner.md"
    prompt_path.write_text(f"""# Pruner: Strategic Merge

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
""", encoding="utf-8")

    return prompt_path


def write_seeder_prompt(
    planspace: Path,
    codespace: Path,
) -> Path:
    """Write the dispatch prompt for the seeder agent.

    Returns the path to the written prompt file.
    """
    artifacts = planspace / "artifacts"
    prompts_dir = artifacts / "substrate" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    substrate_dir = artifacts / "substrate"
    seed_plan_path = substrate_dir / "seed-plan.json"
    substrate_md_path = substrate_dir / "substrate.md"
    codemap_path = artifacts / "codemap.md"

    refs: list[str] = []
    refs.append(f"- **Seed plan**: `{seed_plan_path}`")
    refs.append(f"- **Substrate document**: `{substrate_md_path}`")
    if codemap_path.exists():
        refs.append(f"- **Codemap**: `{codemap_path}`")

    refs_block = "\n".join(refs)

    prompt_path = prompts_dir / "seeder.md"
    prompt_path.write_text(f"""# Seeder: Create Anchors and Wire References

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
2. **Related-files-update signals** at `{artifacts / "signals" / "related-files-update"}/section-<NN>.json`
3. **Substrate input refs** at `{artifacts / "inputs"}/section-<NN>/substrate.ref` (each containing the absolute path to `{substrate_md_path}`)
4. **Completion signal** at `{substrate_dir / "seed-signal.json"}`

Create parent directories as needed. Follow the schemas from your
agent definition exactly.
""", encoding="utf-8")

    return prompt_path
