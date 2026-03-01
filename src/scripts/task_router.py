"""Task type router — maps task types to agent files and models.

Agents submit task types + payloads. This module resolves those types
to concrete agent files and models so the dispatcher can launch them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Task type -> (agent_file, default_model, policy_key | None)
# policy_key overrides task_type for model-policy.json lookup when
# the two names differ (e.g. substrate_prune → substrate_pruner).
# When policy_key is None, task_type is used directly.
TASK_ROUTES: dict[str, tuple[str, str, str | None]] = {
    "alignment_check": ("alignment-judge.md", "claude-opus", None),
    "alignment_adjudicate": ("alignment-output-adjudicator.md", "glm", None),
    "impact_analysis": ("impact-analyzer.md", "glm", None),
    "coordination_fix": ("coordination-fixer.md", "gpt-codex-high", None),
    "consequence_triage": ("consequence-note-triager.md", "glm", None),
    "microstrategy_decision": ("microstrategy-decider.md", "glm", None),
    "recurrence_adjudication": ("recurrence-adjudicator.md", "glm", None),
    "tool_registry_repair": ("tool-registrar.md", "glm", None),
    "integration_proposal": ("integration-proposer.md", "gpt-codex-high", None),
    "strategic_implementation": ("implementation-strategist.md", "gpt-codex-high", None),
    "section_setup": ("setup-excerpter.md", "claude-opus", None),
    "scan_codemap_build": ("scan-codemap-builder.md", "claude-opus", None),
    "scan_codemap_freshness": ("scan-codemap-freshness-judge.md", "glm", None),
    "scan_codemap_verify": ("scan-codemap-verifier.md", "glm", None),
    "scan_explore": ("scan-related-files-explorer.md", "claude-opus", None),
    "scan_adjudicate": ("scan-related-files-adjudicator.md", "glm", None),
    "scan_tier_rank": ("scan-tier-ranker.md", "glm", None),
    "scan_deep_analyze": ("scan-file-analyzer.md", "glm", None),
    "state_adjudicate": ("state-adjudicator.md", "glm", None),
    "exception_handling": ("exception-handler.md", "claude-opus", None),
    "substrate_shard": ("substrate-shard-explorer.md", "gpt-codex-high", None),
    "substrate_prune": ("substrate-pruner.md", "gpt-codex-xhigh", "substrate_pruner"),
    "substrate_seed": ("substrate-seeder.md", "gpt-codex-high", "substrate_seeder"),
}


def resolve_task(
    task_type: str, model_policy: dict[str, str] | None = None
) -> tuple[str, str]:
    """Resolve task type to (agent_file, model).

    Model policy overrides default model if present. The policy maps
    task types to model names, e.g. {"alignment_check": "glm"}.

    Raises ValueError for unknown task types.
    """
    if task_type not in TASK_ROUTES:
        raise ValueError(
            f"Unknown task type: {task_type!r}. "
            f"Known types: {sorted(TASK_ROUTES)}"
        )

    agent_file, default_model, policy_key = TASK_ROUTES[task_type]
    model = default_model
    # V3/R75: Use policy_key (when set) for model-policy lookup so
    # task types and policy keys that differ still resolve correctly.
    lookup_key = policy_key or task_type
    if model_policy and lookup_key in model_policy:
        model = model_policy[lookup_key]

    return agent_file, model


def submit_task(
    db_path: Path,
    submitted_by: str,
    task_type: str,
    *,
    problem_id: str | None = None,
    concern_scope: str | None = None,
    payload_path: str | None = None,
    priority: str = "normal",
    depends_on: int | None = None,
) -> int:
    """Submit a task to the queue. Returns the task ID.

    This is a Python-native alternative to shelling out to
    ``db.sh submit-task``. Uses the same SQLite schema.
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO tasks(submitted_by, task_type, problem_id, concern_scope,
           payload_path, priority, depends_on)
           VALUES(?, ?, ?, ?, ?, ?, ?)""",
        (
            submitted_by,
            task_type,
            problem_id,
            concern_scope,
            payload_path,
            priority,
            str(depends_on) if depends_on is not None else None,
        ),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id
