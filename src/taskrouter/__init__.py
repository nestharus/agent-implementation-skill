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

from taskrouter.core import TaskRegistry, TaskRoute, TaskRouter

registry: TaskRegistry = TaskRegistry()

__all__ = ["TaskRouter", "TaskRoute", "TaskRegistry", "registry"]
