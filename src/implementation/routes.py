"""Task routes for the implementation system."""

from taskrouter import TaskRouter

router = TaskRouter("implementation")

router.route(
    "strategic",
    agent="implementation-strategist.md",
    model="gpt-high",
    policy_key="implementation",
)
router.route(
    "post_assessment",
    agent="post-implementation-assessor.md",
    model="glm",
)
router.route(
    "microstrategy_decision",
    agent="microstrategy-decider.md",
    model="glm",
    policy_key="microstrategy_decider",
)
router.route(
    "microstrategy",
    agent="microstrategy-writer.md",
    model="gpt-high",
    policy_key="implementation",
)
router.route(
    "reexplore",
    agent="section-re-explorer.md",
    model="claude-opus",
    policy_key="setup",
)
