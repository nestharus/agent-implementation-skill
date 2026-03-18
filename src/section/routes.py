"""Task routes for the per-section fractal pipeline.

Each section independently progresses through:
    section.propose -> section.readiness_check
      -> if ready: section.implement -> section.verify
      -> if blocked: emit signals (research, coordination, etc.)

These routes map section-pipeline task types to their agent + model
pairs.  The agents are the same ones used by the batch phases
(integration-proposer for proposals, implementation-strategist for
implementation) -- only the orchestration changes.
"""

from taskrouter import TaskRouter

router = TaskRouter("section")

router.route(
    "propose",
    agent="integration-proposer.md",
    model="gpt-high",
    policy_key="proposal",
)
router.route(
    "readiness_check",
    agent="integration-proposer.md",
    model="gpt-high",
    policy_key="proposal",
)
router.route(
    "implement",
    agent="implementation-strategist.md",
    model="gpt-high",
    policy_key="implementation",
)
router.route(
    "verify",
    agent="structural-verifier.md",
    model="gpt-high",
    policy_key="verification_structural",
)
