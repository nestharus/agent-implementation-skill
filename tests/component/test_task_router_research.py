"""Component tests for research task routing via taskrouter registry."""

from __future__ import annotations

import pytest

from taskrouter import ensure_discovered, registry


@pytest.fixture(autouse=True)
def _discover() -> None:
    ensure_discovered()


@pytest.mark.parametrize(
    ("task_type", "expected_agent", "expected_model"),
    [
        ("research.plan", "research-planner.md", "claude-opus"),
        ("research.domain_ticket", "domain-researcher.md", "gpt-high"),
        ("research.synthesis", "research-synthesizer.md", "gpt-high"),
        ("research.verify", "research-verifier.md", "glm"),
    ],
)
def test_resolve_research_task_uses_default_model(
    task_type: str,
    expected_agent: str,
    expected_model: str,
) -> None:
    assert registry.resolve(task_type) == (expected_agent, expected_model)


@pytest.mark.parametrize(
    ("task_type", "expected_agent"),
    [
        ("research.plan", "research-planner.md"),
        ("research.domain_ticket", "domain-researcher.md"),
        ("research.synthesis", "research-synthesizer.md"),
        ("research.verify", "research-verifier.md"),
    ],
)
def test_resolve_research_task_honors_model_policy_override(
    task_type: str,
    expected_agent: str,
) -> None:
    policy = {
        "research_plan": "policy-plan",
        "research_domain_ticket": "policy-domain",
        "research_synthesis": "policy-synthesis",
        "research_verify": "policy-verify",
    }

    route = registry.get_route(task_type)
    expected_model = policy[route.policy_key]

    assert registry.resolve(task_type, policy) == (
        expected_agent,
        expected_model,
    )
