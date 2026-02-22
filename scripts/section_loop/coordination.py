import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .alignment import _extract_problems, _run_alignment_check_with_retries
from .communication import (
    AGENT_NAME,
    DB_SH,
    _log_artifact,
    log,
    mailbox_send,
)
from .cross_section import read_incoming_notes
from .dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_agent_signal,
    write_model_choice_signal,
)
from .pipeline_control import poll_control_messages
from .types import Section, SectionResult

# Coordination round limits: hard cap to prevent runaway, but rounds
# continue adaptively while problem count decreases.
MAX_COORDINATION_ROUNDS = 10  # hard safety cap
MIN_COORDINATION_ROUNDS = 2   # always try at least this many


def build_file_to_sections(sections: list[Section]) -> dict[str, list[str]]:
    """Map each file path to the section numbers that reference it."""
    mapping: dict[str, list[str]] = {}
    for sec in sections:
        for f in sec.related_files:
            mapping.setdefault(f, []).append(sec.number)
    return mapping


def _collect_outstanding_problems(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
) -> list[dict[str, Any]]:
    """Collect all outstanding problems across sections.

    Includes both misaligned sections AND unaddressed consequence notes
    from the cross-section communication system.

    Returns a list of problem dicts, each with:
      - section: section number
      - type: "misaligned" | "unaddressed_note"
      - description: the problem text
      - files: list of files related to this section
    """
    problems = []
    for sec_num, result in section_results.items():
        if result.aligned:
            continue
        section = sections_by_num.get(sec_num)
        files = list(section.related_files) if section else []

        if result.problems:
            problems.append({
                "section": sec_num,
                "type": "misaligned",
                "description": result.problems,
                "files": files,
            })

    # Scan for unaddressed consequence notes using note IDs and
    # acknowledgment state (not section number ordering heuristics).
    # Each note has an ID (hash of filename). Target sections acknowledge
    # notes via signals/note-ack-<target>.json.
    notes_dir = planspace / "artifacts" / "notes"
    if notes_dir.exists():
        for note_path in sorted(notes_dir.glob("from-*-to-*.md")):
            name_match = re.match(
                r'from-(\d+)-to-(\d+)\.md', note_path.name,
            )
            if not name_match:
                continue
            target_num = name_match.group(2)
            source_num = name_match.group(1)
            target_result = section_results.get(target_num)
            if not target_result or not target_result.aligned:
                continue  # target isn't aligned yet — will see note

            # Compute note ID (stable hash of filename + content hash)
            note_content = note_path.read_text(encoding="utf-8")
            note_id = hashlib.sha256(
                f"{note_path.name}:{hashlib.sha256(note_content.encode()).hexdigest()}"
                .encode()
            ).hexdigest()[:12]

            # Check acknowledgment via structured signal
            ack_path = (planspace / "artifacts" / "signals"
                        / f"note-ack-{target_num}.json")
            ack_signal = read_agent_signal(ack_path)
            if ack_signal:
                acks = ack_signal.get("acknowledged", [])
                if any(a.get("note_id") == note_id for a in acks):
                    continue  # note was acknowledged

            # Note is unaddressed — add as problem
            section = sections_by_num.get(target_num)
            files = list(section.related_files) if section else []
            problems.append({
                "section": target_num,
                "type": "unaddressed_note",
                "note_id": note_id,
                "description": (
                    f"Consequence note {note_id} from section "
                    f"{source_num} has not been acknowledged by "
                    f"section {target_num}. "
                    f"Note content:\n{note_content[:500]}"
                ),
                "files": files,
            })
    return problems


def _parse_coordination_plan(
    agent_output: str, problems: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Parse JSON coordination plan from agent output.

    Returns the parsed plan dict, or None if parsing fails or the plan
    is structurally invalid (missing indices, duplicate indices, etc.).
    """
    # Extract JSON block from agent output (may be in a code fence)
    json_text = None
    in_fence = False
    fence_lines: list[str] = []
    for line in agent_output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") and not in_fence:
            in_fence = True
            fence_lines = []
            continue
        if stripped.startswith("```") and in_fence:
            in_fence = False
            candidate = "\n".join(fence_lines)
            if '"groups"' in candidate:
                json_text = candidate
                break
            continue
        if in_fence:
            fence_lines.append(line)

    if json_text is None:
        # Try raw JSON (no code fence)
        start = agent_output.find("{")
        end = agent_output.rfind("}")
        if start >= 0 and end > start:
            json_text = agent_output[start:end + 1]

    if json_text is None:
        log("  coordinator: no JSON found in coordination plan output")
        return None

    try:
        plan = json.loads(json_text)
    except json.JSONDecodeError as exc:
        log(f"  coordinator: JSON parse error in coordination plan: {exc}")
        return None

    # Validate structure
    if "groups" not in plan or not isinstance(plan["groups"], list):
        log("  coordinator: coordination plan missing 'groups' array")
        return None

    # Validate all problem indices are covered exactly once
    seen_indices: set[int] = set()
    n = len(problems)
    for g in plan["groups"]:
        if "problems" not in g or not isinstance(g["problems"], list):
            log("  coordinator: group missing 'problems' array")
            return None
        for idx in g["problems"]:
            if not isinstance(idx, int) or idx < 0 or idx >= n:
                log(f"  coordinator: invalid problem index {idx}")
                return None
            if idx in seen_indices:
                log(f"  coordinator: duplicate problem index {idx}")
                return None
            seen_indices.add(idx)

    if len(seen_indices) != n:
        missing = set(range(n)) - seen_indices
        log(f"  coordinator: coordination plan missing indices: {missing}")
        return None

    return plan


def write_coordination_plan_prompt(
    problems: list[dict[str, Any]], planspace: Path,
) -> Path:
    """Write an Opus prompt to plan coordination strategy for problems.

    The coordination-planner agent receives the full problem list and
    produces a JSON plan with groups, strategies, and execution order.
    The script then executes the plan mechanically.
    """
    artifacts = planspace / "artifacts" / "coordination"
    artifacts.mkdir(parents=True, exist_ok=True)
    prompt_path = artifacts / "coordination-plan-prompt.md"

    # Write problems as JSON for the agent
    problems_json = json.dumps(problems, indent=2)

    # Include codemap reference so the planner sees project skeleton
    codemap_path = planspace / "artifacts" / "codemap.md"
    codemap_ref = ""
    if codemap_path.exists():
        codemap_ref = (
            f"\n## Project Skeleton\n\n"
            f"Read the codemap for project structure context: "
            f"`{codemap_path}`\n"
        )

    prompt_path.write_text(f"""# Task: Plan Coordination Strategy

## Outstanding Problems

```json
{problems_json}
```
{codemap_ref}
## Instructions

You are the coordination planner. Read the problems above (and the
codemap if provided) and produce a JSON coordination plan. Think
strategically about problem relationships — don't just match files.
Understand whether problems share root causes, whether fixing one
affects another, and what order minimizes rework.

Reply with a JSON block:

```json
{{
  "groups": [
    {{
      "problems": [0, 1],
      "reason": "Both problems stem from incomplete event model in config.py",
      "strategy": "sequential"
    }},
    {{
      "problems": [2],
      "reason": "Independent API endpoint issue",
      "strategy": "parallel"
    }}
  ],
  "execution_order": "Groups can run in parallel if files don't overlap.",
  "notes": "Optional observations about cross-group dependencies."
}}
```

Each group's `problems` array contains indices into the problems list above.
Every problem index (0 through {len(problems) - 1}) must appear in exactly
one group.

Strategy values:
- `sequential`: problems within this group must be fixed in order
- `parallel`: problems within this group can be fixed concurrently

The `execution_order` field describes how GROUPS relate to each other —
which groups can run in parallel and which must wait.
""", encoding="utf-8")
    _log_artifact(planspace, "prompt:coordination-plan")
    return prompt_path


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
    codemap_block = ""
    if codemap_path.exists():
        codemap_block = (
            f"\n## Project Understanding\n"
            f"- Codemap: `{codemap_path}`\n"
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
        except (json.JSONDecodeError, ValueError):
            pass

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
   the broader project structure. Then dispatch GLM sub-agents to read
   files and understand context:
   ```bash
   uv run --frozen agents --model glm --project "{codespace}" "<instructions>"
   ```

2. **Plan holistically.** Consider how all the problems interact. A single
   coordinated change may fix multiple problems at once.

3. **Implement.** Make the changes. For targeted sub-tasks:
   ```bash
   uv run --frozen agents --model gpt-5.3-codex-high \\
     --project "{codespace}" "<instructions>"
   ```

4. **Verify.** After implementation, dispatch GLM to verify the fixes
   address all listed problems without introducing new issues.

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
) -> tuple[int, list[str] | None]:
    """Dispatch a Codex agent to fix a single problem group.

    Returns (group_id, list_of_modified_files) on success.
    Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.
    """
    artifacts = planspace / "artifacts" / "coordination"
    fix_prompt = write_coordinator_fix_prompt(
        group, planspace, codespace, group_id,
    )
    fix_output = artifacts / f"fix-{group_id}-output.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    # Check for model escalation (triggered by coordination churn)
    fix_model = "gpt-5.3-codex-high"
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
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return group_id, None  # Sentinel — caller must check

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


def run_global_coordination(
    sections: list[Section],
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> bool:
    """Run the global problem coordinator.

    Collects outstanding problems across all sections, groups related
    problems, dispatches coordinated fixes, and re-runs alignment on
    affected sections.

    Returns True if all sections are ALIGNED (or no problems remain).
    """
    coord_dir = planspace / "artifacts" / "coordination"
    coord_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------
    # Step 1: Collect all outstanding problems
    # -----------------------------------------------------------------
    problems = _collect_outstanding_problems(
        section_results, sections_by_num, planspace,
    )

    if not problems:
        log("  coordinator: no outstanding problems — all ALIGNED")
        return True

    log(f"  coordinator: {len(problems)} outstanding problems across "
        f"{len({p['section'] for p in problems})} sections")

    # Save coordination state for debugging / inspection
    state_path = coord_dir / "problems.json"
    state_path.write_text(json.dumps(problems, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:problems")

    # -----------------------------------------------------------------
    # Step 2: Dispatch coordination-planner agent to group problems
    # -----------------------------------------------------------------
    ctrl = poll_control_messages(planspace, parent)
    if ctrl == "alignment_changed":
        return False

    plan_prompt = write_coordination_plan_prompt(problems, planspace)
    plan_output = coord_dir / "coordination-plan-output.md"
    log("  coordinator: dispatching coordination-planner agent")
    plan_result = dispatch_agent(
        "claude-opus", plan_prompt, plan_output,
        planspace, parent, agent_file="coordination-planner.md",
    )
    if plan_result == "ALIGNMENT_CHANGED_PENDING":
        return False

    # Parse the JSON coordination plan from agent output
    coord_plan = _parse_coordination_plan(plan_result, problems)
    if coord_plan is None:
        # Fallback: treat each problem as its own group, sequential
        log("  coordinator: WARNING — could not parse coordination plan, "
            "falling back to one-problem-per-group")
        coord_plan = {
            "groups": [
                {"problems": [i], "reason": "fallback", "strategy": "parallel"}
                for i in range(len(problems))
            ],
            "execution_order": "all sequential (fallback)",
        }

    # Build confirmed groups from the plan
    confirmed_groups: list[list[dict[str, Any]]] = []
    group_strategies: list[str] = []
    for g in coord_plan["groups"]:
        group_problems = [problems[i] for i in g["problems"]]
        confirmed_groups.append(group_problems)
        group_strategies.append(g.get("strategy", "sequential"))
        log(f"  coordinator: group {len(confirmed_groups) - 1} — "
            f"{len(group_problems)} problems, "
            f"strategy={group_strategies[-1]}, "
            f"reason={g.get('reason', '(none)')}")

    log(f"  coordinator: {len(confirmed_groups)} problem groups from "
        f"coordination plan")

    # Save plan and groups for debugging
    plan_path = coord_dir / "coordination-plan.json"
    plan_path.write_text(json.dumps(coord_plan, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:plan")

    groups_path = coord_dir / "groups.json"
    groups_data = []
    for i, g in enumerate(confirmed_groups):
        groups_data.append({
            "group_id": i,
            "problem_count": len(g),
            "strategy": group_strategies[i],
            "sections": sorted({p["section"] for p in g}),
            "files": sorted({f for p in g for f in p.get("files", [])}),
        })
    groups_path.write_text(json.dumps(groups_data, indent=2), encoding="utf-8")
    _log_artifact(planspace, "coordination:groups")

    # -----------------------------------------------------------------
    # Step 3: Execute the coordination plan
    # -----------------------------------------------------------------
    # Identify which groups can run in parallel (disjoint file sets)
    # and which must be sequential (overlapping files). The agent's
    # execution_order notes inform us, but we enforce file safety.
    group_file_sets = [
        set(f for p in g for f in p.get("files", []))
        for g in confirmed_groups
    ]

    # Build safe parallel batches: groups with disjoint files
    batches: list[list[int]] = []
    for gidx, files in enumerate(group_file_sets):
        if not files:
            # Unknown scope — isolate
            batches.append([gidx])
            continue
        placed = False
        for batch in batches:
            batch_files = set()
            for bidx in batch:
                batch_files |= group_file_sets[bidx]
            if not batch_files:
                continue
            if not (files & batch_files):
                batch.append(gidx)
                placed = True
                break
        if not placed:
            batches.append([gidx])

    log(f"  coordinator: {len(batches)} execution batches")

    all_modified: list[str] = []
    for batch_num, batch in enumerate(batches):
        ctrl = poll_control_messages(planspace, parent)
        if ctrl == "alignment_changed":
            return False

        # Bridge agent: dispatch for groups with multi-section friction
        # (multiple sections contending over shared files)
        for gidx in batch:
            group = confirmed_groups[gidx]
            group_sections = sorted({p["section"] for p in group})
            group_files = sorted({
                f for p in group for f in p.get("files", [])})
            if len(group_sections) >= 2 and len(group_files) >= 1:
                bridge_prompt = (
                    coord_dir / f"bridge-{gidx}-prompt.md")
                bridge_output = (
                    coord_dir / f"bridge-{gidx}-output.md")
                contract_path = (
                    coord_dir / f"contract-patch-{gidx}.md")
                sec_dir = planspace / "artifacts" / "sections"
                sec_refs = "\n".join(
                    f"- Section {s}: `{sec_dir / f'section-{s}-proposal-excerpt.md'}`"
                    for s in group_sections
                )
                proposals_dir = planspace / "artifacts" / "proposals"
                prop_refs = "\n".join(
                    f"- `{proposals_dir / f'section-{s}-integration-proposal.md'}`"
                    for s in group_sections
                )
                bridge_prompt.write_text(
                    f"# Bridge: Resolve Cross-Section Friction "
                    f"(Group {gidx})\n\n"
                    f"## Sections in Conflict\n{sec_refs}\n\n"
                    f"## Integration Proposals\n{prop_refs}\n\n"
                    f"## Shared Files\n"
                    + "\n".join(f"- `{f}`" for f in group_files)
                    + f"\n\n## Output\n"
                    f"Write your contract patch to: `{contract_path}`\n"
                    f"Write per-section consequence notes to:\n"
                    + "\n".join(
                        f"- `{planspace / 'artifacts' / 'notes' / f'bridge-{gidx}-to-{s}.md'}`"
                        for s in group_sections
                    ) + "\n",
                    encoding="utf-8",
                )
                log(f"  coordinator: dispatching bridge agent for group "
                    f"{gidx} ({group_sections})")
                dispatch_agent(
                    "gpt-5.3-codex-xhigh", bridge_prompt,
                    bridge_output, planspace, parent,
                    codespace=codespace,
                    agent_file="bridge-agent.md",
                )

        if len(batch) == 1:
            gidx = batch[0]
            _, modified = _dispatch_fix_group(
                confirmed_groups[gidx], gidx,
                planspace, codespace, parent,
            )
            if modified is None:
                return False
            all_modified.extend(modified)
        else:
            log(f"  coordinator: batch {batch_num} — "
                f"{len(batch)} groups in parallel")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(
                        _dispatch_fix_group,
                        confirmed_groups[gidx], gidx,
                        planspace, codespace, parent,
                    ): gidx
                    for gidx in batch
                }
                sentinel_hit = False
                for future in as_completed(futures):
                    gidx = futures[future]
                    try:
                        _, modified = future.result()
                        if modified is None:
                            sentinel_hit = True
                            continue
                        all_modified.extend(modified)
                        log(f"  coordinator: group {gidx} fix "
                            f"complete ({len(modified)} files "
                            f"modified)")
                    except Exception as exc:
                        log(f"  coordinator: group {gidx} fix "
                            f"FAILED: {exc}")
            if sentinel_hit:
                return False

    log(f"  coordinator: fixes complete, "
        f"{len(all_modified)} total files modified")

    # -----------------------------------------------------------------
    # Step 4: Re-run per-section alignment on affected sections
    # -----------------------------------------------------------------
    # Determine which sections need re-checking:
    # sections that had problems + sections whose files were modified
    affected_sections: set[str] = set()

    # Sections that had problems
    for p in problems:
        affected_sections.add(p["section"])

    # Sections whose files were modified by the coordinator
    file_to_sections = build_file_to_sections(sections)
    for mod_file in all_modified:
        for sec_num in file_to_sections.get(mod_file, []):
            affected_sections.add(sec_num)

    log(f"  coordinator: re-checking alignment for sections "
        f"{sorted(affected_sections)}")

    # Incremental alignment: track per-section input hashes to skip
    # unchanged sections
    inputs_hash_dir = coord_dir / "inputs-hashes"
    inputs_hash_dir.mkdir(parents=True, exist_ok=True)

    for sec_num in sorted(affected_sections):
        section = sections_by_num.get(sec_num)
        if not section:
            continue

        # Compute inputs hash for this section
        sec_artifacts = planspace / "artifacts"
        hash_sources = [
            sec_artifacts / "sections"
            / f"section-{sec_num}-alignment-excerpt.md",
            sec_artifacts / "proposals"
            / f"section-{sec_num}-integration-proposal.md",
        ]
        hasher = hashlib.sha256()
        for hp in hash_sources:
            if hp.exists():
                hasher.update(hp.read_bytes())
        # Include incoming notes hash
        notes_dir = planspace / "artifacts" / "notes"
        if notes_dir.exists():
            for note_path in sorted(notes_dir.glob(f"from-*-to-{sec_num}.md")):
                hasher.update(note_path.read_bytes())
        # Include modified files hash (coordinator may have changed files)
        for mod_f in sorted(all_modified):
            mod_path = codespace / mod_f
            if mod_path.exists():
                hasher.update(mod_path.read_bytes())
        current_hash = hasher.hexdigest()

        prev_hash_file = inputs_hash_dir / f"section-{sec_num}.hash"
        if prev_hash_file.exists():
            prev_hash = prev_hash_file.read_text(encoding="utf-8").strip()
            if prev_hash == current_hash:
                log(f"  coordinator: section {sec_num} inputs unchanged "
                    f"— skipping alignment recheck")
                continue
        prev_hash_file.write_text(current_hash, encoding="utf-8")

        # Poll for control messages before each re-check
        ctrl = poll_control_messages(planspace, parent, sec_num)
        if ctrl == "alignment_changed":
            log("  coordinator: alignment changed — aborting re-checks")
            return False

        # Read any incoming notes for this section (cross-section context)
        notes = read_incoming_notes(section, planspace, codespace)
        if notes:
            log(f"  coordinator: section {sec_num} has incoming notes "
                f"from other sections")

        # Re-run implementation alignment check with TIMEOUT retry
        align_result = _run_alignment_check_with_retries(
            section, planspace, codespace, parent, sec_num,
            output_prefix="coord-align",
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return False  # Let outer loop restart Phase 1
        if align_result is None:
            # All retries timed out
            log(f"  coordinator: section {sec_num} alignment check "
                f"timed out after retries")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems="alignment check timed out after retries",
            )
            continue

        align_problems = _extract_problems(align_result)
        coord_signal_dir = coord_dir / "signals"
        coord_signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=(coord_signal_dir
                         / f"coord-align-{sec_num}-signal.json"),
            output_path=coord_dir / f"coord-align-{sec_num}-output.md",
            planspace=planspace, parent=parent, codespace=codespace,
        )

        if align_problems is None and signal is None:
            log(f"  coordinator: section {sec_num} now ALIGNED")
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
            )
        else:
            log(f"  coordinator: section {sec_num} still has problems")
            # Fold signal info into problems string (SectionResult has
            # no signal fields — only problems)
            combined_problems = align_problems or ""
            if signal:
                combined_problems += (
                    f"\n[signal:{signal}] {detail}" if combined_problems
                    else f"[signal:{signal}] {detail}"
                )
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=False,
                problems=combined_problems or None,
            )

    # Check if everything is now aligned
    remaining = [r for r in section_results.values() if not r.aligned]
    if not remaining:
        log("  coordinator: all sections now ALIGNED")
        return True

    log(f"  coordinator: {len(remaining)} sections still not aligned")
    return False
