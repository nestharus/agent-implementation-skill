"""Task routes for the verification system."""

from taskrouter import TaskRouter

router = TaskRouter("verification")

router.route(
    "structural",
    agent="structural-verifier.md",
    model="gpt-high",
    policy_key="verification_structural",
)
router.route(
    "integration",
    agent="integration-verifier.md",
    model="gpt-high",
    policy_key="verification_integration",
)
