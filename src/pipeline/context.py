"""Pipeline context — shared state for pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section

if TYPE_CHECKING:
    from containers import ModelPolicyService


@dataclass(frozen=True)
class DispatchContext:
    """Immutable bundle of planspace/codespace.

    Threads through orchestrator, implementation, proposal,
    coordination, and alignment-checking layers.  Lazy-computes
    ``paths`` and ``policy`` on first access.
    """

    planspace: Path
    codespace: Path
    _policies: ModelPolicyService = field(
        repr=False, compare=False, hash=False,
    )

    def _get_policies(self) -> ModelPolicyService:
        return self._policies

    @cached_property
    def paths(self) -> PathRegistry:
        return PathRegistry(self.planspace)

    @cached_property
    def policy(self) -> dict:
        return self._get_policies().load(self.planspace)

    def resolve_model(self, key: str) -> str:
        return self._get_policies().resolve(self.policy, key)


@dataclass
class PipelineContext:
    """Carries shared parameters and mutable state through pipeline steps.

    Eliminates the need to thread ``planspace``, ``codespace``,
    ``policy``, and ``paths`` through every function call.
    Steps communicate intermediate results via ``state``.
    """

    section: Section
    planspace: Path
    codespace: Path
    policy: dict
    paths: PathRegistry
    state: dict[str, Any] = field(default_factory=dict)


class Context:
    """Dependency-injected pipeline context builder."""

    def __init__(self, policies: ModelPolicyService) -> None:
        self._policies = policies

    def build_context(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        policy: dict | None = None,
    ) -> PipelineContext:
        """Build a :class:`PipelineContext` from the standard parameter set.

        If *policy* is ``None``, loads it from *planspace*.
        """
        paths = PathRegistry(planspace)
        if policy is None:
            policy = self._policies.load(planspace)
        return PipelineContext(
            section=section,
            planspace=planspace,
            codespace=codespace,
            policy=policy,
            paths=paths,
        )
