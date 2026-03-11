"""Task routes for the risk system."""

from taskrouter import TaskRouter

router = TaskRouter("risk")

router.route(
    "assess",
    agent="risk-assessor.md",
    model="gpt-high",
    policy_key="risk_assessor",
)
router.route(
    "optimize",
    agent="execution-optimizer.md",
    model="gpt-high",
    policy_key="execution_optimizer",
)
router.route(
    "stack_eval",
    agent="stack-evaluator.md",
    model="gpt-high",
    policy_key="stack_evaluator",
)
