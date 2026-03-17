"""Task routes for the testing system."""

from taskrouter import TaskRouter

router = TaskRouter("testing")

router.route(
    "behavioral",
    agent="behavioral-tester.md",
    model="gpt-high",
    policy_key="testing_behavioral",
)
router.route(
    "rca",
    agent="test-rca.md",
    model="gpt-high",
    policy_key="testing_rca",
)
