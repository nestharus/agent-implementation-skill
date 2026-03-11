"""Task routes for the reconciliation system."""

from taskrouter import TaskRouter

router = TaskRouter("reconciliation")

router.route(
    "adjudicate",
    agent="reconciliation-adjudicator.md",
    model="claude-opus",
)
