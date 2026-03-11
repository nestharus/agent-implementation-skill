"""Task routes for the research system."""

from taskrouter import TaskRouter

router = TaskRouter("research")

router.route(
    "plan",
    agent="research-planner.md",
    model="claude-opus",
    policy_key="research_plan",
)
router.route(
    "domain_ticket",
    agent="domain-researcher.md",
    model="gpt-high",
    policy_key="research_domain_ticket",
)
router.route(
    "synthesis",
    agent="research-synthesizer.md",
    model="gpt-high",
    policy_key="research_synthesis",
)
router.route(
    "verify",
    agent="research-verifier.md",
    model="glm",
    policy_key="research_verify",
)
