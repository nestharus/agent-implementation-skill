"""Auto-discover and register all system route modules.

Call ``discover()`` once at startup to import each system's routes.py,
which triggers route registration with the global registry.
"""

from __future__ import annotations

import importlib

from taskrouter import registry

# All systems that expose task routes.
_SYSTEM_ROUTE_MODULES: list[str] = [
    "scan.routes",
    "staleness.routes",
    "research.routes",
    "proposal.routes",
    "implementation.routes",
    "coordination.routes",
    "reconciliation.routes",
    "dispatch.routes",
    "signals.routes",
]


def discover() -> None:
    """Import all system route modules, registering their routes.

    Each module creates a TaskRouter on import and calls router.route()
    for each task type. We just need to add each router to the global
    registry.

    Idempotent — safe to call multiple times.
    """
    for module_name in _SYSTEM_ROUTE_MODULES:
        mod = importlib.import_module(module_name)
        router = getattr(mod, "router", None)
        if router is not None and router.namespace not in registry.namespaces:
            registry.add_router(router)
