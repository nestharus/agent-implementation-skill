"""Pipeline context — shared state for pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section


@dataclass(frozen=True)
class DispatchContext:
    """Immutable bundle of the planspace/codespace/parent triple.

    Threads through orchestrator, implementation, proposal,
    coordination, and alignment-checking layers.  Lazy-computes
    ``paths`` and ``policy`` on first access.
    """

    planspace: Path
    codespace: Path
    parent: str

    @cached_property
    def paths(self) -> PathRegistry:
        return PathRegistry(self.planspace)

    @cached_property
    def policy(self) -> dict:
        from containers import Services
        return Services.policies().load(self.planspace)

    def resolve_model(self, key: str) -> str:
        from containers import Services
        return Services.policies().resolve(self.policy, key)


@dataclass
class PipelineContext:
    """Carries shared parameters and mutable state through pipeline steps.

    Eliminates the need to thread ``planspace``, ``codespace``,
    ``parent``, ``policy``, and ``paths`` through every function call.
    Steps communicate intermediate results via ``state``.
    """

    section: Section
    planspace: Path
    codespace: Path
    parent: str
    policy: dict
    paths: PathRegistry
    state: dict[str, Any] = field(default_factory=dict)


def build_context(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict | None = None,
) -> PipelineContext:
    """Build a :class:`PipelineContext` from the standard parameter set.

    If *policy* is ``None``, loads it from *planspace*.
    """
    from containers import Services

    paths = PathRegistry(planspace)
    if policy is None:
        policy = Services.policies().load(planspace)
    return PipelineContext(
        section=section,
        planspace=planspace,
        codespace=codespace,
        parent=parent,
        policy=policy,
        paths=paths,
    )
