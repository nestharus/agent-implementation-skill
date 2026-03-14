"""Auto-discover and register all system route modules.

Call ``discover()`` once at startup to import each system's routes.py,
which triggers route registration with the global registry.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from taskrouter import registry

_SRC_DIR = Path(__file__).resolve().parent.parent


def _find_route_modules() -> list[str]:
    """Scan sibling packages for ``routes.py`` files."""
    modules: list[str] = []
    for candidate in sorted(_SRC_DIR.iterdir()):
        if not candidate.is_dir():
            continue
        routes_file = candidate / "routes.py"
        if routes_file.exists():
            modules.append(f"{candidate.name}.routes")
    return modules


def discover() -> None:
    """Import all system route modules, registering their routes.

    Each module creates a TaskRouter on import and calls router.route()
    for each task type. We just need to add each router to the global
    registry.

    Idempotent — safe to call multiple times.
    """
    for module_name in _find_route_modules():
        mod = importlib.import_module(module_name)
        router = getattr(mod, "router", None)
        if router is not None and router.namespace not in registry.namespaces:
            registry.add_router(router)
