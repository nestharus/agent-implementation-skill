"""Task routes for the proposal system."""

from taskrouter import TaskRouter

router = TaskRouter("proposal")

router.route(
    "integration",
    agent="integration-proposer.md",
    model="gpt-high",
    policy_key="proposal",
)
router.route(
    "section_setup",
    agent="setup-excerpter.md",
    model="claude-opus",
    policy_key="setup",
)
router.route(
    "section",
    agent="integration-proposer.md",
    model="gpt-high",
    policy_key="proposal",
)
router.route(
    "gate_synthesis",
    agent="integration-proposer.md",
    model="gpt-high",
    policy_key="proposal",
)
