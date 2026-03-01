import json
from pathlib import Path
from typing import Any

from ..communication import _log_artifact, log
from ..dispatch import dispatch_agent, write_model_choice_signal
from ..task_ingestion import ingest_and_dispatch


def write_coordinator_fix_prompt(
    group: list[dict[str, Any]], planspace: Path, codespace: Path,
    group_id: int,
) -> Path:
    """Write a Codex prompt to fix a group of related problems.

    The prompt lists the grouped problems with section context, the
    affected files, and instructs the agent to fix ALL listed problems
    in a coordinated way.
    """
    artifacts = planspace / "artifacts" / "coordination"
    artifacts.mkdir(parents=True, exist_ok=True)
    prompt_path = artifacts / f"fix-{group_id}-prompt.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    problem_descriptions = []
    for i, p in enumerate(group):
        desc = (
            f"### Problem {i + 1} (Section {p['section']}, "
            f"type: {p['type']})\n"
            f"{p['description']}"
        )
        problem_descriptions.append(desc)
    problems_text = "\n\n".join(problem_descriptions)

    # Collect all unique files across the group
    all_files: list[str] = []
    seen: set[str] = set()
    for p in group:
        for f in p.get("files", []):
            if f not in seen:
                all_files.append(f)
                seen.add(f)

    file_list = "\n".join(f"- `{codespace / f}`" for f in all_files)

    # Collect section specs for context (include both actual spec and excerpts)
    section_nums = sorted({p["section"] for p in group})
    sec_dir = planspace / "artifacts" / "sections"
    section_specs = "\n".join(
        f"- Section {n} specification:"
        f" `{sec_dir / f'section-{n}.md'}`\n"
        f"  - Proposal excerpt:"
        f" `{sec_dir / f'section-{n}-proposal-excerpt.md'}`"
        for n in section_nums
    )
    alignment_specs = "\n".join(
        f"- Section {n} alignment excerpt:"
        f" `{sec_dir / f'section-{n}-alignment-excerpt.md'}`"
        for n in section_nums
    )

    codemap_path = planspace / "artifacts" / "codemap.md"
    corrections_path = (planspace / "artifacts" / "signals"
                        / "codemap-corrections.json")
    codemap_block = ""
    if codemap_path.exists():
        corrections_line = ""
        if corrections_path.exists():
            corrections_line = (
                f"- Codemap corrections (authoritative fixes): "
                f"`{corrections_path}`\n"
            )
        codemap_block = (
            f"\n## Project Understanding\n"
            f"- Codemap: `{codemap_path}`\n"
            f"{corrections_line}"
            f"\nIf codemap corrections exist, treat them as authoritative "
            f"over codemap.md.\n"
        )

    # Include cross-section tools — prefer digest if available
    tools_block = ""
    tool_digest_path = planspace / "artifacts" / "tool-digest.md"
    tool_registry_path = planspace / "artifacts" / "tool-registry.json"
    if tool_digest_path.exists():
        tools_block = (
            f"\n## Available Tools\n"
            f"See tool digest: `{tool_digest_path}`\n"
        )
    elif tool_registry_path.exists():
        try:
            reg = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            cross_tools = [
                t for t in (reg if isinstance(reg, list)
                            else reg.get("tools", []))
                if t.get("scope") == "cross-section"
            ]
            if cross_tools:
                tool_lines = "\n".join(
                    f"- `{t.get('path', '?')}` "
                    f"[{t.get('status', 'experimental')}]: "
                    f"{t.get('description', '')}"
                    for t in cross_tools
                )
                tools_block = (
                    f"\n## Available Cross-Section Tools\n{tool_lines}\n"
                )
        except (json.JSONDecodeError, ValueError) as exc:
            # V2/R58: Preserve corrupted tool-registry for diagnosis.
            malformed_path = tool_registry_path.with_suffix(
                ".malformed.json")
            try:
                import shutil
                shutil.copy2(tool_registry_path, malformed_path)
            except OSError:
                pass  # Best-effort preserve
            tools_block = (
                f"\n## Tool Registry Warning\n"
                f"Tool registry exists but is malformed ({exc}); "
                f"see `{tool_registry_path}`.\n"
                f"Malformed copy preserved at "
                f"`{malformed_path}`.\n"
                f"Consider dispatching tool-registrar repair before "
                f"relying on tool context.\n"
            )

    task_submission_path = artifacts / f"signals/task-requests-coord-{group_id}.json"

    prompt_path.write_text(f"""# Task: Coordinated Fix for Problem Group {group_id}

## Problems to Fix

{problems_text}

## Affected Files
{file_list}

## Section Context
{section_specs}
{alignment_specs}
{codemap_block}{tools_block}
## Instructions

Fix ALL the problems listed above in a COORDINATED way. These problems
are related — they share files and/or have a common root cause. Fixing
them together avoids the cascade where fixing one problem in isolation
creates or re-triggers another.

### Strategy

1. **Explore first.** Before making changes, understand the full picture.
   Read the codemap if available to understand how these files fit into
   the broader project structure. If you need deeper exploration, submit
   a task request to `{task_submission_path}`:
   ```json
   {{"task_type": "scan_explore", "concern_scope": "coord-group-{group_id}", "payload_path": "<path-to-exploration-prompt>", "priority": "normal"}}
   ```

2. **Plan holistically.** Consider how all the problems interact. A single
   coordinated change may fix multiple problems at once.

3. **Implement.** Make the changes. For targeted sub-tasks, submit a
   task request:
   ```json
   {{"task_type": "coordination_fix", "concern_scope": "coord-group-{group_id}", "payload_path": "<path-to-fix-prompt>", "priority": "normal"}}
   ```

4. **Verify.** After implementation, submit a scan task to verify
   the fixes address all listed problems without introducing new issues.

Available task types: scan_explore, coordination_fix

The dispatcher handles agent selection and model choice. You declare
WHAT work you need, not which agent or model runs it.

### Report Modified Files

After implementation, write a list of ALL files you modified to:
`{modified_report}`

One file path per line (relative to codespace root `{codespace}`).
Include files modified by sub-agents.
""", encoding="utf-8")
    _log_artifact(planspace, f"prompt:coordinator-fix-{group_id}")
    return prompt_path


def _dispatch_fix_group(
    group: list[dict[str, Any]], group_id: int,
    planspace: Path, codespace: Path, parent: str,
    default_fix_model: str = "",
) -> tuple[int, list[str] | None]:
    """Dispatch a Codex agent to fix a single problem group.

    Returns (group_id, list_of_modified_files) on success.
    Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.

    The ``default_fix_model`` should come from ``policy["coordination_fix"]``
    so that model selection is strictly policy-driven.
    """
    from ..dispatch import read_model_policy

    artifacts = planspace / "artifacts" / "coordination"
    policy = read_model_policy(planspace)
    fix_prompt = write_coordinator_fix_prompt(
        group, planspace, codespace, group_id,
    )
    fix_output = artifacts / f"fix-{group_id}-output.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    # Check for model escalation (triggered by coordination churn)
    if not default_fix_model:
        default_fix_model = policy["coordination_fix"]
    fix_model = default_fix_model
    coord_escalated_from = None
    escalation_file = artifacts / "model-escalation.txt"
    if escalation_file.exists():
        coord_escalated_from = fix_model
        fix_model = escalation_file.read_text(encoding="utf-8").strip()
        log(f"  coordinator: using escalated model {fix_model}")

    write_model_choice_signal(
        planspace, f"coord-{group_id}", "coordination-fix",
        fix_model,
        "escalated due to coordination churn" if coord_escalated_from
        else "default model",
        coord_escalated_from,
    )

    log(f"  coordinator: dispatching fix for group {group_id} "
        f"({len(group)} problems)")
    result = dispatch_agent(
        fix_model, fix_prompt, fix_output,
        planspace, parent, codespace=codespace,
        agent_file="coordination-fixer.md",
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return group_id, None  # Sentinel — caller must check

    # V5: Ingest any task requests the coordination fixer submitted
    ingest_and_dispatch(
        planspace,
        artifacts / f"signals/task-requests-coord-{group_id}.json",
        str(group_id), parent, codespace,
    )

    # Collect modified files from the report (validated to be safe
    # relative paths under codespace — same logic as collect_modified_files)
    codespace_resolved = codespace.resolve()
    modified: list[str] = []
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    log(f"  coordinator: WARNING — fix path outside "
                        f"codespace, skipping: {line}")
                    continue
            else:
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    log(f"  coordinator: WARNING — fix path escapes "
                        f"codespace, skipping: {line}")
                    continue
            modified.append(str(rel))
    return group_id, modified
