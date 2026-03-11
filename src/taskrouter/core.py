"""Core routing primitives: TaskRoute, TaskRouter, TaskRegistry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskRoute:
    """A registered task route within a system."""

    name: str
    namespace: str
    agent: str
    model: str
    policy_key: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"


class TaskRouter:
    """Per-system task router.

    Each system creates one router with its namespace and registers
    task routes on it. Routes auto-register with the global registry.

    Example::

        router = TaskRouter("scan")
        router.route("codemap_build", agent="scan-codemap-builder.md", model="claude-opus")
    """

    def __init__(self, namespace: str, *, _registry: TaskRegistry | None = None):
        self.namespace = namespace
        self._routes: dict[str, TaskRoute] = {}
        self._registry = _registry

    def route(
        self,
        name: str,
        *,
        agent: str,
        model: str,
        policy_key: str | None = None,
    ) -> TaskRoute:
        """Register a task route in this system's namespace."""
        if name in self._routes:
            raise ValueError(
                f"Duplicate route: {self.namespace}.{name}"
            )
        task_route = TaskRoute(
            name=name,
            namespace=self.namespace,
            agent=agent,
            model=model,
            policy_key=policy_key,
        )
        self._routes[name] = task_route
        return task_route

    def get(self, name: str) -> TaskRoute:
        """Resolve a local task name to its route."""
        if name not in self._routes:
            raise KeyError(
                f"Unknown route: {self.namespace}.{name}. "
                f"Known: {sorted(self._routes)}"
            )
        return self._routes[name]

    @property
    def routes(self) -> dict[str, TaskRoute]:
        return dict(self._routes)

    @property
    def task_names(self) -> frozenset[str]:
        return frozenset(self._routes)

    @property
    def qualified_names(self) -> frozenset[str]:
        return frozenset(r.qualified_name for r in self._routes.values())


class TaskRegistry:
    """Global registry that collects system routers and resolves task types.

    Acts as the root router — receives a qualified task name like
    "scan.codemap_build", finds the system router for "scan", and
    delegates resolution to it.
    """

    def __init__(self) -> None:
        self._routers: dict[str, TaskRouter] = {}

    def add_router(self, router: TaskRouter) -> None:
        """Register a system router."""
        if router.namespace in self._routers:
            raise ValueError(
                f"Duplicate namespace: {router.namespace!r}. "
                f"Already registered."
            )
        router._registry = self
        self._routers[router.namespace] = router

    def resolve(
        self,
        task_type: str,
        model_policy: dict | None = None,
    ) -> tuple[str, str]:
        """Resolve a qualified task type to (agent_file, model).

        Model policy overrides default model when present. The policy
        maps task types or policy keys to model names.
        """
        route = self.get_route(task_type)
        model = route.model

        if model_policy:
            lookup_key = route.policy_key or route.qualified_name
            # Support nested policy dicts: {"scan": {"codemap_build": "glm"}}
            if "." in lookup_key:
                ns, local = lookup_key.split(".", 1)
                ns_policy = model_policy.get(ns)
                if isinstance(ns_policy, dict) and local in ns_policy:
                    model = ns_policy[local]
            if model == route.model and lookup_key in model_policy:
                model = model_policy[lookup_key]

        return route.agent, model

    def get_route(self, task_type: str) -> TaskRoute:
        """Look up a route by qualified name.

        Raises ValueError for unknown task types.
        """
        if "." not in task_type:
            raise ValueError(
                f"Task type must be qualified (namespace.name): {task_type!r}. "
                f"Known namespaces: {sorted(self._routers)}"
            )

        namespace, name = task_type.split(".", 1)
        if namespace not in self._routers:
            raise ValueError(
                f"Unknown namespace: {namespace!r}. "
                f"Known: {sorted(self._routers)}"
            )

        return self._routers[namespace].get(name)

    def get_router(self, namespace: str) -> TaskRouter:
        """Get a system router by namespace."""
        if namespace not in self._routers:
            raise KeyError(
                f"Unknown namespace: {namespace!r}. "
                f"Known: {sorted(self._routers)}"
            )
        return self._routers[namespace]

    @property
    def all_task_types(self) -> frozenset[str]:
        """All qualified task type names across all systems."""
        result: set[str] = set()
        for router in self._routers.values():
            result.update(router.qualified_names)
        return frozenset(result)

    @property
    def all_routes(self) -> list[TaskRoute]:
        """All registered routes across all systems."""
        result: list[TaskRoute] = []
        for router in self._routers.values():
            result.extend(router.routes.values())
        return result

    @property
    def namespaces(self) -> frozenset[str]:
        return frozenset(self._routers)

    def allowed_tasks_for(self, task_types: frozenset[str]) -> list[str]:
        """Return sorted qualified names for a given set of task types.

        Used to build the allowed_tasks toolbelt for agents.
        Validates that all requested types exist.
        """
        result: list[str] = []
        for task_type in sorted(task_types):
            self.get_route(task_type)  # validates existence
            result.append(task_type)
        return result
