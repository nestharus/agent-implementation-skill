"""Component tests for research-first model policy surfaces."""

from __future__ import annotations

import json

from dispatch.service.model_policy import ModelPolicy, load_model_policy
from taskrouter import ensure_discovered, registry


def test_model_policy_defaults_include_research_keys() -> None:
    policy = ModelPolicy()

    assert policy.research_plan == "claude-opus"
    assert policy.research_domain_ticket == "gpt-high"
    assert policy.research_synthesis == "gpt-high"
    assert policy.research_verify == "glm"


def test_load_model_policy_overrides_research_keys(tmp_path) -> None:
    policy_path = tmp_path / "artifacts" / "model-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({
        "research_plan": "policy-plan",
        "research_domain_ticket": "policy-domain",
        "research_synthesis": "policy-synthesis",
        "research_verify": "policy-verify",
    }), encoding="utf-8")

    policy = load_model_policy(tmp_path)

    assert policy.research_plan == "policy-plan"
    assert policy.research_domain_ticket == "policy-domain"
    assert policy.research_synthesis == "policy-synthesis"
    assert policy.research_verify == "policy-verify"


def test_registry_resolve_uses_research_policy_keys() -> None:
    ensure_discovered()

    assert registry.resolve(
        "research.plan",
        {"research_plan": "policy-plan"},
    ) == ("research-planner.md", "policy-plan")
    assert registry.resolve(
        "research.domain_ticket",
        {"research_domain_ticket": "policy-domain"},
    ) == ("domain-researcher.md", "policy-domain")
    assert registry.resolve(
        "research.synthesis",
        {"research_synthesis": "policy-synthesis"},
    ) == ("research-synthesizer.md", "policy-synthesis")
    assert registry.resolve(
        "research.verify",
        {"research_verify": "policy-verify"},
    ) == ("research-verifier.md", "policy-verify")
