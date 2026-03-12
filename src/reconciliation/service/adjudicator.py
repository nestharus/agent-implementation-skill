"""Agent-backed adjudication for ungrouped reconciliation candidates."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from containers import Services
from signals.repository.artifact_io import write_json
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.template import render_template
from taskrouter import agent_for

logger = logging.getLogger(__name__)


def adjudicate_ungrouped_candidates(
    ungrouped: list[dict],
    planspace: Path,
    candidate_type: str,
) -> list[dict]:
    """Dispatch an adjudicator agent to merge semantically similar candidates."""
    if len(ungrouped) < 2:
        return []

    recon_dir = PathRegistry(planspace).reconciliation_dir()
    candidates_path = recon_dir / f"ungrouped-{candidate_type}.json"
    write_json(candidates_path, ungrouped)

    dynamic_body = f"""# Reconciliation Adjudication: {candidate_type}

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

    prompt_path = recon_dir / f"adjudicate-{candidate_type}-prompt.md"
    output_path = recon_dir / f"adjudicate-{candidate_type}-output.md"

    violations = Services.prompt_guard().validate_dynamic(dynamic_body)
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

    policy = Services.policies().load(planspace)
    model = Services.policies().resolve(policy,"reconciliation_adjudicate")

    try:
        result = Services.dispatcher().dispatch(
            model,
            prompt_path,
            output_path,
            planspace=planspace,
            agent_file=agent_for("reconciliation.adjudicate"),
        )
    except Exception:
        logger.warning(
            "Reconciliation adjudication dispatch failed for %s "
            "— failing open with degraded advisory (PAT-0014: dispatch_error)",
            candidate_type,
            exc_info=True,
        )
        return []

    try:
        json_start = result.find("{")
        json_end = result.rfind("}")
        if json_start >= 0 and json_end > json_start:
            data = json.loads(result[json_start:json_end + 1])
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
