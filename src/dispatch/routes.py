"""Task routes for the dispatch system."""

from taskrouter import TaskRouter

router = TaskRouter("dispatch")

router.route(
    "tool_registry_repair",
    agent="tool-registrar.md",
    model="glm",
    policy_key="tool_registrar",
)
router.route(
    "bridge_tools",
    agent="bridge-tools.md",
    model="gpt-high",
    policy_key="bridge_tools",
)
router.route(
    "qa_intercept",
    agent="qa-interceptor.md",
    model="claude-opus",
    policy_key="qa_interceptor",
)
