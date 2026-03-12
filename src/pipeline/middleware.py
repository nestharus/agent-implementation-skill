"""Built-in middleware for the pipeline engine."""

from __future__ import annotations

from typing import Any, Callable

from pipeline.context import PipelineContext
from pipeline.engine import HALT


class AlignmentGuard:
    """Check for concurrent alignment changes around pipeline steps.

    Wraps the ubiquitous ``alignment_changed_pending(planspace)`` guard
    that appears 10+ times across engine/ god functions.  When the
    guard fires, the pipeline halts — the caller returns ``None`` so
    the section-loop can re-queue the section.

    Parameters
    ----------
    check_fn:
        Callable that takes *planspace* and returns ``True`` when an
        alignment change has been detected.
    after_steps:
        If provided, the guard checks AFTER these named steps complete
        (matching the original pattern where alignment is checked after
        expensive LLM calls, not before every step).  If ``None``, the
        guard checks BEFORE every step.
    """

    def __init__(
        self,
        check_fn: Callable[..., bool],
        after_steps: set[str] | list[str] | None = None,
    ) -> None:
        self.check_fn = check_fn
        self.after_steps: set[str] | None = (
            set(after_steps) if after_steps else None
        )

    def before(self, ctx: PipelineContext, step_name: str) -> Any | None:
        if self.after_steps is None and self.check_fn(ctx.planspace):
            return HALT
        return None

    def after(
        self, ctx: PipelineContext, step_name: str, result: Any,
    ) -> Any | None:
        if self.after_steps and step_name in self.after_steps:
            if self.check_fn(ctx.planspace):
                return HALT
        return None


class StepLogger:
    """Log step entry and exit.

    Replaces the ``log(f"Section {n}: ...")`` calls interleaved with
    business logic in god functions.
    """

    def __init__(self, log_fn: Callable[[str], None]) -> None:
        self.log_fn = log_fn

    def before(self, ctx: PipelineContext, step_name: str) -> Any | None:
        self.log_fn(
            f"Section {ctx.section.number}: {step_name} — starting",
        )
        return None

    def after(
        self, ctx: PipelineContext, step_name: str, result: Any,
    ) -> Any | None:
        self.log_fn(
            f"Section {ctx.section.number}: {step_name} — done",
        )
        return None
