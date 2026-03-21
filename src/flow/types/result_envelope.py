from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskResultEnvelope:
    task_id: int
    task_type: str
    status: str
    output_path: str | None
    unresolved_problems: list[str] = field(default_factory=list)
    new_value_axes: list[str] = field(default_factory=list)
    partial_solutions: list[dict] = field(default_factory=list)
    scope_expansions: list[str] = field(default_factory=list)
    error: str | None = None
