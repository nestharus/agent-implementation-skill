"""Model policy loading and lookup."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping, cast

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService


# ---------------------------------------------------------------------------
# Field defaults — single source of truth for known policy keys.
# ---------------------------------------------------------------------------

_FIELD_DEFAULTS: dict[str, Any] = {
    "setup": "claude-opus",
    "proposal": "gpt-high",
    "alignment": "claude-opus",
    "implementation": "gpt-high",
    "coordination_plan": "claude-opus",
    "coordination_fix": "gpt-high",
    "coordination_bridge": "gpt-xhigh",
    "exploration": "glm",
    "adjudicator": "glm",
    "impact_analysis": "glm",
    "impact_normalizer": "glm",
    "triage": "glm",
    "microstrategy_decider": "glm",
    "tool_registrar": "glm",
    "bridge_tools": "gpt-high",
    "risk_assessor": "gpt-high",
    "execution_optimizer": "gpt-high",
    "qa_interceptor": "claude-opus",
    "escalation_model": "gpt-xhigh",
    "intent_triage": "glm",
    "intent_philosophy": "claude-opus",
    "intent_pack": "gpt-high",
    "intent_judge": "claude-opus",
    "intent_problem_expander": "claude-opus",
    "intent_philosophy_expander": "claude-opus",
    "intent_triage_escalation": "claude-opus",
    "intent_recurrence_adjudicator": "glm",
    "intent_philosophy_selector": "gpt-high",
    "intent_philosophy_selector_escalation": "claude-opus",
    "intent_philosophy_verifier": "claude-opus",
    "intent_philosophy_bootstrap_prompter": "glm",
    "substrate_shard": "gpt-high",
    "substrate_pruner": "gpt-xhigh",
    "substrate_seeder": "gpt-high",
    "reconciliation_adjudicate": "claude-opus",
    # Research-first intent layer
    "research_plan": "claude-opus",
    "research_domain_ticket": "gpt-high",
    "research_synthesis": "gpt-high",
    "research_verify": "glm",
    # Intake / trust / verification layer
    "intake_triage": "gpt-high",
    "claim_extractor": "gpt-high",
    "hypothesis_builder": "claude-opus",
    "verification_builder": "gpt-high",
    "codebase_governance_assessor": "claude-opus",
    "value_scale_enumerator": "gpt-high",
    "stack_evaluator": "gpt-high",
    "escalation_triggers": {
        "stall_count": 2,
        "max_attempts_before_escalation": 3,
    },
    "scan": {},
    "substrate_trigger_min_vacuum_sections": 2,
}

_FIELD_NAMES = tuple(_FIELD_DEFAULTS.keys())


class ModelPolicy(Mapping[str, Any]):
    """Model policy with attribute and mapping-style access.

    Not a dataclass — implements the full :class:`~collections.abc.Mapping`
    protocol with conditional fallback into an ``extras`` dict for
    forward-compatible keys.
    """

    def __init__(self, **kwargs: Any) -> None:
        extras = kwargs.pop("extras", {})
        for name in _FIELD_NAMES:
            default = _FIELD_DEFAULTS[name]
            # Deep-copy mutable defaults (dicts) to avoid sharing state.
            if isinstance(default, dict):
                default = default.copy()
            setattr(self, name, kwargs.pop(name, default))
        self.extras: dict[str, Any] = extras

    # -- Mapping protocol ---------------------------------------------------

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


class ModelPolicyLoader:
    """Loads and manages model policy with injected dependencies."""

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def load_model_policy(self, planspace: Path) -> ModelPolicy:
        """Read ``artifacts/model-policy.json`` with current defaults."""
        policy_path = PathRegistry(planspace).model_policy()
        defaults = ModelPolicy()

        if not policy_path.exists():
            return defaults

        data = self._artifact_io.read_json(policy_path)
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
            self._artifact_io.rename_malformed(policy_path)
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
