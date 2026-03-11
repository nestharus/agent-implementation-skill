"""Task routes for the signals system."""

from taskrouter import TaskRouter

router = TaskRouter("signals")

router.route(
    "impact_analysis",
    agent="impact-analyzer.md",
    model="glm",
)
