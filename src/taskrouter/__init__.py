"""Task router — decorator-based agent routing by system namespace.

Each system creates a TaskRouter with its namespace and registers
task routes. The global registry collects all routers and provides
unified lookup by qualified name (e.g. "scan.codemap_build").

Usage in a system module::

    # scan/routes.py
    from taskrouter import TaskRouter

    router = TaskRouter("scan")
    router.route("codemap_build", agent="scan-codemap-builder.md", model="claude-opus")
    router.route("explore", agent="scan-related-files-explorer.md", model="claude-opus")

Usage from dispatcher::

    from taskrouter import registry

    route = registry.resolve("scan.codemap_build")
    agent_file, model = route.agent, route.model
"""

from taskrouter.route_registry import TaskRegistry, TaskRoute, TaskRouter

registry: TaskRegistry = TaskRegistry()

_discovered = False


def ensure_discovered() -> None:
    """Populate the global registry by importing all system route modules.

    Idempotent — safe to call multiple times.  Called automatically the
    first time :func:`resolve` or :attr:`all_task_types` is accessed
    through the convenience wrappers below.
    """
    global _discovered
    if _discovered:
        return
    from taskrouter.discovery import discover
    discover()
    _discovered = True


def agent_for(task_type: str) -> str:
    """Resolve the agent file for a qualified task type.

    Ensures discovery has run, then returns the registered agent filename.
    """
    ensure_discovered()
    return registry.get_route(task_type).agent


__all__ = [
    "TaskRouter", "TaskRoute", "TaskRegistry",
    "registry", "ensure_discovered", "agent_for",
]
