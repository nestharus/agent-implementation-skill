"""Task routes for the QA system."""

from taskrouter import TaskRouter

router = TaskRouter("qa")

router.route(
    "qa_intercept",
    agent="qa-interceptor.md",
    model="claude-opus",
    policy_key="qa_interceptor",
)
