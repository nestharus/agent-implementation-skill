"""Pipeline context — shared state for pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section


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

    @classmethod
    def for_section(
        cls,
        section: Section,
        planspace: Path,
        codespace: Path,
        parent: str,
        policy: dict | None = None,
    ) -> PipelineContext:
        """Build context from the standard parameter set.

        If *policy* is ``None``, loads it from *planspace*.
        """
        from dispatch.service.model_policy import load_model_policy

        paths = PathRegistry(planspace)
        if policy is None:
            policy = load_model_policy(planspace)
        return cls(
            section=section,
            planspace=planspace,
            codespace=codespace,
            parent=parent,
            policy=policy,
            paths=paths,
        )
