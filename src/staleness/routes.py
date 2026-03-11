"""Task routes for the staleness system."""

from taskrouter import TaskRouter

router = TaskRouter("staleness")

router.route(
    "alignment_check",
    agent="alignment-judge.md",
    model="claude-opus",
    policy_key="alignment",
)
router.route(
    "alignment_adjudicate",
    agent="alignment-output-adjudicator.md",
    model="glm",
    policy_key="adjudicator",
)
router.route(
    "state_adjudicate",
    agent="state-adjudicator.md",
    model="glm",
)
