"""Shared coordination planning helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coordination.problem_types import Problem
from orchestrator.path_registry import PathRegistry
from dispatch.helpers.signal_checker import extract_fenced_block

if TYPE_CHECKING:
    from containers import ArtifactIOService, Communicator, LogService, PromptGuard


class Planner:
    """Coordination planning: parse plans, write prompts."""

    def __init__(
        self,
        *,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        logger: LogService,
        prompt_guard: PromptGuard,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._logger = logger
        self._prompt_guard = prompt_guard

    def _validate_problem_indices(
        self, plan: dict[str, Any], n: int,
    ) -> bool:
        """Validate that all problem indices in groups are valid and complete."""
        seen_indices: set[int] = set()
        for group in plan["groups"]:
            if "problems" not in group or not isinstance(group["problems"], list):
                self._logger.log("  coordinator: group missing 'problems' array")
                return False
            for idx in group["problems"]:
                if not isinstance(idx, int) or idx < 0 or idx >= n:
                    self._logger.log(f"  coordinator: invalid problem index {idx}")
                    return False
                if idx in seen_indices:
                    self._logger.log(f"  coordinator: duplicate problem index {idx}")
                    return False
                seen_indices.add(idx)

        if len(seen_indices) != n:
            missing = set(range(n)) - seen_indices
            self._logger.log(f"  coordinator: coordination plan missing indices: {missing}")
            return False
        return True

    def _validate_batches(self, plan: dict[str, Any]) -> None:
        """Validate batch ordering in-place. Removes invalid batches."""
        if "batches" not in plan:
            return
        batches = plan["batches"]
        if not isinstance(batches, list):
            self._logger.log("  coordinator: 'batches' is not an array — ignoring")
            del plan["batches"]
            return

        n_groups = len(plan["groups"])
        seen_gidx: set[int] = set()
        batches_valid = True
        for batch in batches:
            if not isinstance(batch, list):
                batches_valid = False
                break
            for gidx in batch:
                if not isinstance(gidx, int) or gidx < 0 or gidx >= n_groups:
                    self._logger.log(f"  coordinator: invalid group index {gidx} in batches")
                    batches_valid = False
                    break
                if gidx in seen_gidx:
                    self._logger.log(f"  coordinator: duplicate group index {gidx} in batches")
                    batches_valid = False
                    break
                seen_gidx.add(gidx)
            if not batches_valid:
                break
        if batches_valid and len(seen_gidx) != n_groups:
            self._logger.log(
                "  coordinator: batches missing group indices: "
                f"{set(range(n_groups)) - seen_gidx}",
            )
            batches_valid = False
        if not batches_valid:
            self._logger.log("  coordinator: invalid batches — will use file-safety batching")
            del plan["batches"]

    def _normalize_bridge_directives(self, plan: dict[str, Any]) -> None:
        """Normalize bridge directives on each group to dict form."""
        for group in plan["groups"]:
            bridge = group.get("bridge")
            if bridge is None:
                group["bridge"] = {"needed": False}
            elif isinstance(bridge, bool):
                group["bridge"] = {"needed": bridge}
            elif not isinstance(bridge, dict):
                self._logger.log(
                    "  coordinator: bridge directive has unexpected type "
                    f"{type(bridge).__name__} — defaulting to disabled",
                )
                group["bridge"] = {"needed": False}

    def _parse_coordination_plan(
        self, agent_output: str, problems: list[Problem],
    ) -> dict[str, Any] | None:
        """Parse JSON coordination plan from agent output."""
        json_text = _extract_json_from_output(agent_output)
        if json_text is None:
            self._logger.log("  coordinator: no JSON found in coordination plan output")
            return None

        try:
            plan = json.loads(json_text)
        except json.JSONDecodeError as exc:
            self._logger.log(f"  coordinator: JSON parse error in coordination plan: {exc}")
            return None

        if "groups" not in plan or not isinstance(plan["groups"], list):
            self._logger.log("  coordinator: coordination plan missing 'groups' array")
            return None

        if not self._validate_problem_indices(plan, len(problems)):
            return None

        self._validate_batches(plan)
        self._normalize_bridge_directives(plan)
        return plan

    def write_coordination_plan_prompt(
        self, problems: list[Problem], planspace: Path,
    ) -> Path:
        """Write an Opus prompt to plan coordination strategy for problems."""
        paths = PathRegistry(planspace)
        coord_dir = paths.coordination_dir()
        prompt_path = coord_dir / "coordination-plan-prompt.md"

        problems_path = coord_dir / "problems.json"
        self._artifact_io.write_json(problems_path, problems)

        codemap_path = paths.codemap()
        corrections_path = paths.corrections()
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

        recurrence_ref = ""
        recurrence_path = paths.coordination_recurrence()
        if recurrence_path.exists():
            recurrence_ref = (
                f"\n## Recurrence Data\n\n"
                f"Some sections have recurring problems (failed to converge in "
                f"per-section loop). Read: `{recurrence_path}`\n\n"
                f"Recurring sections should be grouped together when possible "
                f"and flagged for escalated model usage.\n"
            )

        plan_prompt_text = _compose_coordination_plan_text(
            problems_path=problems_path,
            codemap_ref=codemap_ref,
            recurrence_ref=recurrence_ref,
            max_problem_index=len(problems) - 1,
        )
        if not self._prompt_guard.write_validated(plan_prompt_text, prompt_path):
            return None
        self._communicator.log_artifact(planspace, "prompt:coordination-plan")
        return prompt_path


# ---------------------------------------------------------------------------
# Pure helpers (no Services usage)
# ---------------------------------------------------------------------------

def _extract_json_from_output(agent_output: str) -> str | None:
    """Extract JSON text containing 'groups' from agent output."""
    result = extract_fenced_block(agent_output, '"groups"')
    if result is not None:
        return result
    start = agent_output.find("{")
    end = agent_output.rfind("}")
    if start >= 0 and end > start:
        return agent_output[start:end + 1]
    return None


def _compose_coordination_plan_text(
    problems_path: Path,
    codemap_ref: str,
    recurrence_ref: str,
    max_problem_index: int,
) -> str:
    """Return the coordination plan prompt text."""
    return f"""# Task: Plan Coordination Strategy

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
Every problem index (0 through {max_problem_index}) must appear in exactly
one group.

Strategy values:
- `sequential`: problems within this group must be fixed in order
- `parallel`: problems within this group can be fixed concurrently

The `batches` array defines execution ordering of GROUPS. Each batch is a
list of group indices to run concurrently (subject to file-safety checks).
Batches execute sequentially — batch 0 completes before batch 1 starts.
Example: `[[0, 2], [1]]` means run groups 0 and 2 in parallel first,
then run group 1.
"""
