"""Component tests for the task router system."""

from __future__ import annotations

import pytest

from taskrouter.route_registry import TaskRegistry, TaskRoute, TaskRouter


@pytest.fixture()
def fresh_registry() -> TaskRegistry:
    return TaskRegistry()


@pytest.fixture()
def scan_router(fresh_registry: TaskRegistry) -> TaskRouter:
    router = TaskRouter("scan")
    router.route("codemap_build", agent="scan-codemap-builder.md", model="claude-opus")
    router.route("explore", agent="scan-explorer.md", model="claude-opus")
    fresh_registry.add_router(router)
    return router


@pytest.fixture()
def research_router(fresh_registry: TaskRegistry) -> TaskRouter:
    router = TaskRouter("research")
    router.route("plan", agent="research-planner.md", model="claude-opus")
    router.route("verify", agent="research-verifier.md", model="glm")
    fresh_registry.add_router(router)
    return router


# ---------------------------------------------------------------------------
# TaskRoute
# ---------------------------------------------------------------------------


class TestTaskRoute:
    def test_qualified_name(self) -> None:
        route = TaskRoute(
            name="codemap_build",
            namespace="scan",
            agent="scan-codemap-builder.md",
            model="claude-opus",
        )
        assert route.qualified_name == "scan.codemap_build"

    def test_frozen(self) -> None:
        route = TaskRoute(
            name="explore",
            namespace="scan",
            agent="scan-explorer.md",
            model="claude-opus",
        )
        with pytest.raises(AttributeError):
            route.name = "other"


# ---------------------------------------------------------------------------
# TaskRouter
# ---------------------------------------------------------------------------


class TestTaskRouter:
    def test_route_registration(self) -> None:
        router = TaskRouter("scan")
        route = router.route(
            "codemap_build",
            agent="scan-codemap-builder.md",
            model="claude-opus",
        )
        assert route.name == "codemap_build"
        assert route.namespace == "scan"
        assert route.agent == "scan-codemap-builder.md"
        assert route.model == "claude-opus"

    def test_route_with_policy_key(self) -> None:
        router = TaskRouter("scan")
        route = router.route(
            "codemap_verify",
            agent="scan-codemap-verifier.md",
            model="glm",
            policy_key="scan.validation",
        )
        assert route.policy_key == "scan.validation"

    def test_duplicate_route_raises(self) -> None:
        router = TaskRouter("scan")
        router.route("explore", agent="a.md", model="glm")
        with pytest.raises(ValueError, match="Duplicate route"):
            router.route("explore", agent="b.md", model="glm")

    def test_get_known_route(self) -> None:
        router = TaskRouter("scan")
        router.route("explore", agent="scan-explorer.md", model="claude-opus")
        route = router.get("explore")
        assert route.agent == "scan-explorer.md"

    def test_get_unknown_route_raises(self) -> None:
        router = TaskRouter("scan")
        with pytest.raises(KeyError, match="Unknown route"):
            router.get("nonexistent")

    def test_task_names(self) -> None:
        router = TaskRouter("scan")
        router.route("a", agent="a.md", model="glm")
        router.route("b", agent="b.md", model="glm")
        assert router.task_names == frozenset({"a", "b"})

    def test_qualified_names(self) -> None:
        router = TaskRouter("scan")
        router.route("a", agent="a.md", model="glm")
        router.route("b", agent="b.md", model="glm")
        assert router.qualified_names == frozenset({"scan.a", "scan.b"})


# ---------------------------------------------------------------------------
# TaskRegistry
# ---------------------------------------------------------------------------


class TestTaskRegistry:
    def test_resolve_qualified_name(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        agent, model = fresh_registry.resolve("scan.codemap_build")
        assert agent == "scan-codemap-builder.md"
        assert model == "claude-opus"

    def test_resolve_unknown_namespace_raises(
        self,
        fresh_registry: TaskRegistry,
    ) -> None:
        with pytest.raises(ValueError, match="Unknown namespace"):
            fresh_registry.resolve("nonexistent.task")

    def test_resolve_unknown_task_in_known_namespace_raises(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        with pytest.raises(KeyError, match="Unknown route"):
            fresh_registry.resolve("scan.nonexistent")

    def test_resolve_unqualified_name_raises(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        with pytest.raises(ValueError, match="must be qualified"):
            fresh_registry.resolve("codemap_build")

    def test_duplicate_namespace_raises(
        self,
        fresh_registry: TaskRegistry,
    ) -> None:
        r1 = TaskRouter("scan")
        r2 = TaskRouter("scan")
        fresh_registry.add_router(r1)
        with pytest.raises(ValueError, match="Duplicate namespace"):
            fresh_registry.add_router(r2)

    def test_model_policy_override_flat_key(
        self,
        fresh_registry: TaskRegistry,
        research_router: TaskRouter,
    ) -> None:
        """Policy key matches flat string in policy dict."""
        research_router.route(
            "synthesis",
            agent="research-synthesizer.md",
            model="gpt-high",
            policy_key="research.synthesis",
        )
        agent, model = fresh_registry.resolve(
            "research.synthesis",
            model_policy={"research.synthesis": "gpt-xhigh"},
        )
        assert agent == "research-synthesizer.md"
        assert model == "gpt-xhigh"

    def test_model_policy_override_nested_key(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        """Dotted policy key resolves through nested dict."""
        # scan_router already has codemap_build, but no policy_key.
        # Add one with a dotted policy key.
        router = TaskRouter("test_scan")
        router.route(
            "codemap_build",
            agent="scan-codemap-builder.md",
            model="claude-opus",
            policy_key="scan.codemap_build",
        )
        fresh_registry.add_router(router)

        agent, model = fresh_registry.resolve(
            "test_scan.codemap_build",
            model_policy={"scan": {"codemap_build": "glm"}},
        )
        assert model == "glm"

    def test_model_policy_no_match_uses_default(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        agent, model = fresh_registry.resolve(
            "scan.explore",
            model_policy={"unrelated_key": "gpt-high"},
        )
        assert model == "claude-opus"

    def test_all_task_types(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
        research_router: TaskRouter,
    ) -> None:
        expected = frozenset({
            "scan.codemap_build",
            "scan.explore",
            "research.plan",
            "research.verify",
        })
        assert fresh_registry.all_task_types == expected

    def test_namespaces(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
        research_router: TaskRouter,
    ) -> None:
        assert fresh_registry.namespaces == frozenset({"scan", "research"})

    def test_all_routes(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        routes = fresh_registry.all_routes
        assert len(routes) == 2
        names = {r.qualified_name for r in routes}
        assert names == {"scan.codemap_build", "scan.explore"}

    def test_get_router(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        assert fresh_registry.get_router("scan") is scan_router

    def test_get_router_unknown_raises(
        self,
        fresh_registry: TaskRegistry,
    ) -> None:
        with pytest.raises(KeyError, match="Unknown namespace"):
            fresh_registry.get_router("nonexistent")

    def test_allowed_tasks_for(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
        research_router: TaskRouter,
    ) -> None:
        allowed = fresh_registry.allowed_tasks_for(
            frozenset({"scan.explore", "research.plan"}),
        )
        assert allowed == ["research.plan", "scan.explore"]

    def test_allowed_tasks_for_unknown_raises(
        self,
        fresh_registry: TaskRegistry,
        scan_router: TaskRouter,
    ) -> None:
        with pytest.raises(ValueError, match="must be qualified"):
            fresh_registry.allowed_tasks_for(frozenset({"nonexistent"}))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_all_system_routes_discoverable(self) -> None:
        """Importing all system route modules registers every declared route."""
        import importlib
        from taskrouter.discovery import _SYSTEM_ROUTE_MODULES

        test_registry = TaskRegistry()

        for module_name in _SYSTEM_ROUTE_MODULES:
            mod = importlib.import_module(module_name)
            if mod.router.namespace not in test_registry.namespaces:
                test_registry.add_router(mod.router)

        # Every declared route module is registered
        expected_namespaces = frozenset(
            m.split(".")[0] for m in _SYSTEM_ROUTE_MODULES
        )
        assert test_registry.namespaces == expected_namespaces
        # At least one route per namespace
        assert len(test_registry.all_task_types) >= len(expected_namespaces)

    def test_every_route_has_agent_file(self) -> None:
        """Every registered route has a non-empty agent file."""
        test_registry = TaskRegistry()

        import scan.routes
        import staleness.routes
        import research.routes
        import proposal.routes
        import implementation.routes
        import coordination.routes
        import reconciliation.routes
        import dispatch.routes
        import signals.routes

        for mod in [
            scan.routes, staleness.routes, research.routes,
            proposal.routes, implementation.routes, coordination.routes,
            reconciliation.routes, dispatch.routes, signals.routes,
        ]:
            if mod.router.namespace not in test_registry.namespaces:
                test_registry.add_router(mod.router)

        for route in test_registry.all_routes:
            assert route.agent, f"{route.qualified_name} has empty agent"
            assert route.agent.endswith(".md"), (
                f"{route.qualified_name} agent should be .md file"
            )
