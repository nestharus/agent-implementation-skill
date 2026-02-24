import json
from pathlib import Path
from typing import Any

from ..communication import _log_artifact, log


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

    # Validate batches if present: each batch is a list of valid group
    # indices, every group index must appear exactly once across all batches.
    if "batches" in plan:
        batches = plan["batches"]
        if not isinstance(batches, list):
            log("  coordinator: 'batches' is not an array — ignoring")
            del plan["batches"]
        else:
            n_groups = len(plan["groups"])
            seen_gidx: set[int] = set()
            batches_valid = True
            for batch in batches:
                if not isinstance(batch, list):
                    batches_valid = False
                    break
                for gidx in batch:
                    if not isinstance(gidx, int) or gidx < 0 or gidx >= n_groups:
                        log(f"  coordinator: invalid group index {gidx} in batches")
                        batches_valid = False
                        break
                    if gidx in seen_gidx:
                        log(f"  coordinator: duplicate group index {gidx} in batches")
                        batches_valid = False
                        break
                    seen_gidx.add(gidx)
                if not batches_valid:
                    break
            if batches_valid and len(seen_gidx) != n_groups:
                log(f"  coordinator: batches missing group indices: "
                    f"{set(range(n_groups)) - seen_gidx}")
                batches_valid = False
            if not batches_valid:
                log("  coordinator: invalid batches — will use file-safety batching")
                del plan["batches"]

    # Extract bridge directives if present (planner decides, not script)
    # Validate type: must be a dict. Coerce other types safely.
    for g in plan["groups"]:
        bridge = g.get("bridge")
        if bridge is None:
            g["bridge"] = {"needed": False}
        elif isinstance(bridge, bool):
            g["bridge"] = {"needed": bridge}
        elif not isinstance(bridge, dict):
            log(f"  coordinator: bridge directive has unexpected type "
                f"{type(bridge).__name__} — defaulting to disabled")
            g["bridge"] = {"needed": False}

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

    # Write problems to artifact file (avoid inline embedding)
    problems_path = artifacts / "problems.json"
    problems_path.write_text(json.dumps(problems, indent=2), encoding="utf-8")

    # Include codemap reference so the planner sees project skeleton
    codemap_path = planspace / "artifacts" / "codemap.md"
    corrections_path = (planspace / "artifacts" / "signals"
                        / "codemap-corrections.json")
    codemap_ref = ""
    if codemap_path.exists():
        corrections_line = ""
        if corrections_path.exists():
            corrections_line = (
                f"\n- Codemap corrections (authoritative fixes): "
                f"`{corrections_path}`"
            )
        codemap_ref = (
            f"\n## Project Skeleton\n\n"
            f"Read the codemap for project structure context: "
            f"`{codemap_path}`{corrections_line}\n"
            f"\nIf codemap corrections exist, treat them as authoritative "
            f"over codemap.md.\n"
        )

    # Include recurrence data if available
    recurrence_ref = ""
    recurrence_path = planspace / "artifacts" / "coordination" / "recurrence.json"
    if recurrence_path.exists():
        recurrence_ref = (
            f"\n## Recurrence Data\n\n"
            f"Some sections have recurring problems (failed to converge in "
            f"per-section loop). Read: `{recurrence_path}`\n\n"
            f"Recurring sections should be grouped together when possible "
            f"and flagged for escalated model usage.\n"
        )

    prompt_path.write_text(f"""# Task: Plan Coordination Strategy

## Outstanding Problems

Read the problems list from: `{problems_path}`
{codemap_ref}
{recurrence_ref}
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
  "batches": [[0, 2], [1]],
  "notes": "Optional observations about cross-group dependencies."
}}
```

Each group's `problems` array contains indices into the problems list above.
Every problem index (0 through {len(problems) - 1}) must appear in exactly
one group.

Strategy values:
- `sequential`: problems within this group must be fixed in order
- `parallel`: problems within this group can be fixed concurrently

The `batches` array defines execution ordering of GROUPS. Each batch is a
list of group indices to run concurrently (subject to file-safety checks).
Batches execute sequentially — batch 0 completes before batch 1 starts.
Example: `[[0, 2], [1]]` means run groups 0 and 2 in parallel first,
then run group 1.
""", encoding="utf-8")
    _log_artifact(planspace, "prompt:coordination-plan")
    return prompt_path
