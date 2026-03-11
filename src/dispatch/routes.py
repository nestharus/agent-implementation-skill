"""Task routes for the dispatch system."""

from taskrouter import TaskRouter

router = TaskRouter("dispatch")

router.route(
    "tool_registry_repair",
    agent="tool-registrar.md",
    model="glm",
    policy_key="tool_registrar",
)
