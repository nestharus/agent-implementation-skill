"""Agent-backed adjudication for ungrouped reconciliation candidates."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from pipeline.template import render_template

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        ModelPolicyService,
        PromptGuard,
        TaskRouterService,
    )

logger = logging.getLogger(__name__)


def _compose_adjudication_text(candidate_type: str, candidates_path: Path) -> str:
    """Build the dynamic prompt body for reconciliation adjudication."""
    return f"""# Reconciliation Adjudication: {candidate_type}

## Candidate Type
{candidate_type.replace("_", " ").title()} candidates

## Ungrouped Candidates

Read the ungrouped candidates from: `{candidates_path}`

The candidates were NOT matched by exact title comparison.
Decide which ones describe the same underlying concern and should be
merged, and which should remain separate.

## Instructions

Return a JSON verdict with merged groups and separate candidates.
Every candidate title must appear exactly once — either in a merged
group's `members` array or in the `separate` array.

```json
{{
  "merged_groups": [
    {{"canonical_title": "...", "members": ["title-a", "title-b"], "rationale": "..."}}
  ],
  "separate": ["title-c"]
}}
```
"""


class Adjudicator:
    """Dispatches agent-backed adjudication for ungrouped reconciliation candidates."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        prompt_guard: PromptGuard,
        policies: ModelPolicyService,
        dispatcher: AgentDispatcher,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._prompt_guard = prompt_guard
        self._policies = policies
        self._dispatcher = dispatcher
        self._task_router = task_router

    def adjudicate_ungrouped_candidates(
        self,
        ungrouped: list[dict],
        planspace: Path,
        candidate_type: str,
    ) -> list[dict]:
        """Dispatch an adjudicator agent to merge semantically similar candidates."""
        if len(ungrouped) < 2:
            return []

        recon_dir = PathRegistry(planspace).reconciliation_dir()
        candidates_path = recon_dir / f"ungrouped-{candidate_type}.json"
        self._artifact_io.write_json(candidates_path, ungrouped)

        dynamic_body = _compose_adjudication_text(candidate_type, candidates_path)

        prompt_path = recon_dir / f"adjudicate-{candidate_type}-prompt.md"
        output_path = recon_dir / f"adjudicate-{candidate_type}-output.md"

        violations = self._prompt_guard.validate_dynamic(dynamic_body)
        if violations:
            logger.warning(
                "Reconciliation adjudicate prompt safety violation: %s "
                "— failing open with degraded advisory (PAT-0014: safety_blocked)",
                violations,
            )
            return []

        prompt_path.write_text(
            render_template("reconciliation-adjudicate", dynamic_body),
            encoding="utf-8",
        )

        policy = self._policies.load(planspace)
        model = self._policies.resolve(policy, "reconciliation_adjudicate")

        try:
            result = self._dispatcher.dispatch(
                model,
                prompt_path,
                output_path,
                planspace=planspace,
                agent_file=self._task_router.agent_for("reconciliation.adjudicate"),
            )
        except Exception:  # noqa: BLE001 — fail-open: adjudication is best-effort
            logger.warning(
                "Reconciliation adjudication dispatch failed for %s "
                "— failing open with degraded advisory (PAT-0014: dispatch_error)",
                candidate_type,
                exc_info=True,
            )
            return []

        try:
            json_start = result.output.find("{")
            json_end = result.output.rfind("}")
            if json_start >= 0 and json_end > json_start:
                data = json.loads(result.output[json_start:json_end + 1])
                merged = data.get("merged_groups", [])
                if isinstance(merged, list):
                    return merged
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning(
                "Reconciliation adjudication returned malformed JSON for "
                "%s — failing open with degraded advisory (PAT-0014: unparseable)",
                candidate_type,
            )
        return []


# ---------------------------------------------------------------------------
# Backward-compat wrappers
# ---------------------------------------------------------------------------

def _get_adjudicator() -> Adjudicator:
    from containers import Services
    return Adjudicator(
        artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        policies=Services.policies(),
        dispatcher=Services.dispatcher(),
        task_router=Services.task_router(),
    )


def adjudicate_ungrouped_candidates(
    ungrouped: list[dict],
    planspace: Path,
    candidate_type: str,
) -> list[dict]:
    """Dispatch an adjudicator agent to merge semantically similar candidates."""
    return _get_adjudicator().adjudicate_ungrouped_candidates(
        ungrouped, planspace, candidate_type,
    )
