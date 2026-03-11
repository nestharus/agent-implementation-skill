"""Model policy loading and lookup."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator, Mapping, cast

from signals.repository.artifact_io import read_json, rename_malformed
from orchestrator.path_registry import PathRegistry


@dataclass
class ModelPolicy(Mapping[str, Any]):
    """Model policy with attribute and mapping-style access."""

    setup: str = "claude-opus"
    proposal: str = "gpt-high"
    alignment: str = "claude-opus"
    implementation: str = "gpt-high"
    coordination_plan: str = "claude-opus"
    coordination_fix: str = "gpt-high"
    coordination_bridge: str = "gpt-xhigh"
    exploration: str = "glm"
    adjudicator: str = "glm"
    impact_analysis: str = "glm"
    impact_normalizer: str = "glm"
    triage: str = "glm"
    microstrategy_decider: str = "glm"
    tool_registrar: str = "glm"
    bridge_tools: str = "gpt-high"
    risk_assessor: str = "gpt-high"
    execution_optimizer: str = "gpt-high"
    qa_interceptor: str = "claude-opus"
    escalation_model: str = "gpt-xhigh"
    intent_triage: str = "glm"
    intent_philosophy: str = "claude-opus"
    intent_pack: str = "gpt-high"
    intent_judge: str = "claude-opus"
    intent_problem_expander: str = "claude-opus"
    intent_philosophy_expander: str = "claude-opus"
    intent_triage_escalation: str = "claude-opus"
    intent_recurrence_adjudicator: str = "glm"
    intent_philosophy_selector: str = "gpt-high"
    intent_philosophy_selector_escalation: str = "claude-opus"
    intent_philosophy_verifier: str = "claude-opus"
    intent_philosophy_bootstrap_prompter: str = "glm"
    substrate_shard: str = "gpt-high"
    substrate_pruner: str = "gpt-xhigh"
    substrate_seeder: str = "gpt-high"
    reconciliation_adjudicate: str = "claude-opus"
    # Research-first intent layer
    research_plan: str = "claude-opus"
    research_domain_ticket: str = "gpt-high"
    research_synthesis: str = "gpt-high"
    research_verify: str = "glm"
    # Intake / trust / verification layer
    intake_triage: str = "gpt-high"
    claim_extractor: str = "gpt-high"
    hypothesis_builder: str = "claude-opus"
    verification_builder: str = "gpt-high"
    codebase_governance_assessor: str = "claude-opus"
    value_scale_enumerator: str = "gpt-high"
    stack_evaluator: str = "gpt-high"
    escalation_triggers: dict[str, int] = field(default_factory=lambda: {
        "stall_count": 2,
        "max_attempts_before_escalation": 3,
    })
    scan: dict[str, str] = field(default_factory=dict)
    substrate_trigger_min_vacuum_sections: int = 2
    extras: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if key == "extras":
            raise KeyError(key)
        if key in _FIELD_NAMES:
            return getattr(self, key)
        if key in self.extras:
            return self.extras[key]
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield from _FIELD_NAMES
        yield from self.extras

    def __len__(self) -> int:
        return len(_FIELD_NAMES) + len(self.extras)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


_FIELD_NAMES = tuple(
    info.name for info in fields(ModelPolicy) if info.name != "extras"
)


def load_model_policy(planspace: Path) -> ModelPolicy:
    """Read ``artifacts/model-policy.json`` with current defaults."""
    policy_path = PathRegistry(planspace).model_policy()
    defaults = ModelPolicy()

    if not policy_path.exists():
        return defaults

    data = read_json(policy_path)
    if isinstance(data, dict):
        merged = defaults.__dict__.copy()
        extras = {}

        for key, value in data.items():
            if key == "escalation_triggers":
                merged[key] = {
                    **defaults.escalation_triggers,
                    **value,
                }
            elif key in _FIELD_NAMES:
                merged[key] = value
            else:
                extras[key] = value

        merged["extras"] = extras
        return ModelPolicy(**merged)

    print(
        "  WARNING: model-policy.json exists but is invalid — "
        "renaming to .malformed.json",
        flush=True,
    )
    if data is not None:
        rename_malformed(policy_path)
    return defaults


_DEFAULTS = ModelPolicy()


def resolve(policy: Mapping[str, Any], key: str) -> str:
    """Resolve a string-valued policy key with authoritative defaults.

    Works with both ``ModelPolicy`` instances and plain dicts.  When a key
    is missing from *policy*, the authoritative default from ``ModelPolicy``
    is returned.  This keeps default knowledge centralized (PAT-0005).
    """
    head, sep, tail = key.partition(".")
    if sep:
        nested = policy.get(head) if not isinstance(policy, ModelPolicy) else policy[head]
        if isinstance(nested, Mapping) and tail in nested:
            return cast(str, nested[tail])
        raise KeyError(key)
    val = policy.get(key)
    if val is not None:
        return cast(str, val)
    return cast(str, getattr(_DEFAULTS, key))
