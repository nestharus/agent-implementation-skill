"""Component tests for research task routing."""

from __future__ import annotations

import pytest

from src.flow.task_router import resolve_task


@pytest.mark.parametrize(
    ("task_type", "expected_agent", "expected_model"),
    [
        ("research_plan", "research-planner.md", "claude-opus"),
        ("research_domain_ticket", "domain-researcher.md", "gpt-high"),
        ("research_synthesis", "research-synthesizer.md", "gpt-high"),
        ("research_verify", "research-verifier.md", "glm"),
    ],
)
def test_resolve_research_task_uses_default_model(
    task_type: str,
    expected_agent: str,
    expected_model: str,
) -> None:
    assert resolve_task(task_type) == (expected_agent, expected_model)


@pytest.mark.parametrize(
    ("task_type", "expected_agent"),
    [
        ("research_plan", "research-planner.md"),
        ("research_domain_ticket", "domain-researcher.md"),
        ("research_synthesis", "research-synthesizer.md"),
        ("research_verify", "research-verifier.md"),
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

    assert resolve_task(task_type, policy) == (
        expected_agent,
        policy[task_type],
    )
