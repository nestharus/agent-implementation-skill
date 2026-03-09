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
    "alignment_check": ("alignment-judge.md", "claude-opus", "alignment"),
    "alignment_adjudicate": ("alignment-output-adjudicator.md", "glm", "adjudicator"),
    "impact_analysis": ("impact-analyzer.md", "glm", None),
    "coordination_fix": ("coordination-fixer.md", "gpt-high", None),
    "consequence_triage": ("consequence-note-triager.md", "glm", "triage"),
    "microstrategy_decision": ("microstrategy-decider.md", "glm", "microstrategy_decider"),
    "recurrence_adjudication": ("recurrence-adjudicator.md", "glm", None),
    "tool_registry_repair": ("tool-registrar.md", "glm", "tool_registrar"),
    "integration_proposal": ("integration-proposer.md", "gpt-high", "proposal"),
    "strategic_implementation": ("implementation-strategist.md", "gpt-high", "implementation"),
    "section_setup": ("setup-excerpter.md", "claude-opus", "setup"),
    # Scan tasks: "scan." prefix resolves through scan policy namespace
    "scan_codemap_build": ("scan-codemap-builder.md", "claude-opus", "scan.codemap_build"),
    "scan_codemap_freshness": ("scan-codemap-freshness-judge.md", "glm", "scan.codemap_freshness"),
    "scan_codemap_verify": ("scan-codemap-verifier.md", "glm", "scan.validation"),
    "scan_explore": ("scan-related-files-explorer.md", "claude-opus", "scan.exploration"),
    "scan_adjudicate": ("scan-related-files-adjudicator.md", "glm", "scan.validation"),
    "scan_tier_rank": ("scan-tier-ranker.md", "glm", "scan.tier_ranking"),
    "scan_deep_analyze": ("scan-file-analyzer.md", "glm", "scan.deep_analysis"),
    "state_adjudicate": ("state-adjudicator.md", "glm", None),
    "substrate_shard": ("substrate-shard-explorer.md", "gpt-high", None),
    "substrate_prune": ("substrate-pruner.md", "gpt-xhigh", "substrate_pruner"),
    "substrate_seed": ("substrate-seeder.md", "gpt-high", "substrate_seeder"),
    "reconciliation_adjudicate": ("reconciliation-adjudicator.md", "claude-opus", None),
    # Research tasks: research-first intent layer.
    "research_plan": ("research-planner.md", "claude-opus", "research_plan"),
    "research_domain_ticket": ("domain-researcher.md", "gpt-high", "research_domain_ticket"),
    "research_synthesis": ("research-synthesizer.md", "gpt-high", "research_synthesis"),
    "research_verify": ("research-verifier.md", "glm", "research_verify"),
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
    # R82/P1: "scan." prefix resolves through model_policy["scan"][suffix].
    lookup_key = policy_key or task_type
    if model_policy:
        if lookup_key.startswith("scan."):
            scan_key = lookup_key[5:]  # strip "scan." prefix
            scan_policy = model_policy.get("scan", {})
            if isinstance(scan_policy, dict) and scan_key in scan_policy:
                model = scan_policy[scan_key]
        elif lookup_key in model_policy:
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
    instance_id: str | None = None,
    flow_id: str | None = None,
    chain_id: str | None = None,
    declared_by_task_id: int | None = None,
    trigger_gate_id: str | None = None,
    flow_context_path: str | None = None,
    continuation_path: str | None = None,
    result_manifest_path: str | None = None,
    freshness_token: str | None = None,
) -> int:
    """Submit a task to the queue. Returns the task ID.

    This is a Python-native alternative to shelling out to
    ``db.sh submit-task``. Uses the same SQLite schema.

    ``freshness_token`` (P4): lightweight hash of alignment artifacts
    at submission time.  The dispatcher compares this against the
    current hash before dispatch and rejects stale tasks.
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO tasks(submitted_by, task_type, problem_id, concern_scope,
           payload_path, priority, depends_on,
           instance_id, flow_id, chain_id, declared_by_task_id,
           trigger_gate_id, flow_context_path, continuation_path,
           result_manifest_path, freshness_token)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            submitted_by,
            task_type,
            problem_id,
            concern_scope,
            payload_path,
            priority,
            str(depends_on) if depends_on is not None else None,
            instance_id,
            flow_id,
            chain_id,
            declared_by_task_id,
            trigger_gate_id,
            flow_context_path,
            continuation_path,
            result_manifest_path,
            freshness_token,
        ),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id
