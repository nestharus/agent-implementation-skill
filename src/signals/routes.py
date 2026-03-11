"""Task routes for the signals system."""

from taskrouter import TaskRouter

router = TaskRouter("signals")

router.route(
    "impact_analysis",
    agent="impact-analyzer.md",
    model="glm",
)
router.route(
    "impact_normalize",
    agent="impact-output-normalizer.md",
    model="glm",
    policy_key="impact_normalizer",
)
