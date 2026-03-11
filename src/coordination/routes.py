"""Task routes for the coordination system."""

from taskrouter import TaskRouter

router = TaskRouter("coordination")

router.route(
    "fix",
    agent="coordination-fixer.md",
    model="gpt-high",
)
router.route(
    "consequence_triage",
    agent="consequence-note-triager.md",
    model="glm",
    policy_key="triage",
)
router.route(
    "recurrence_adjudication",
    agent="recurrence-adjudicator.md",
    model="glm",
)
