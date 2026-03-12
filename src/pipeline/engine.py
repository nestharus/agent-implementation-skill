"""Pipeline engine — step sequencing with middleware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from pipeline.context import PipelineContext


class _HaltSentinel:
    """Sentinel returned by a step or middleware to stop the pipeline."""

    _instance: _HaltSentinel | None = None

    def __new__(cls) -> _HaltSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "HALT"

    def __bool__(self) -> bool:
        return False


HALT = _HaltSentinel()


class Middleware(Protocol):
    """Protocol for pipeline middleware.

    ``before`` runs before each step.  Return ``HALT`` to stop the
    pipeline.  Return ``None`` to continue.

    ``after`` runs after each step completes successfully.  Return
    ``HALT`` to stop the pipeline, ``None`` to continue.
    """

    def before(self, ctx: PipelineContext, step_name: str) -> Any | None: ...

    def after(
        self, ctx: PipelineContext, step_name: str, result: Any,
    ) -> Any | None: ...


@dataclass
class Step:
    """A named pipeline step.

    Parameters
    ----------
    name:
        Human-readable label for logging and debugging.
    fn:
        Callable that receives a ``PipelineContext`` and returns a result.
        Return ``HALT`` to stop the pipeline (step completed but pipeline
        should not continue).  Return ``None`` to halt the pipeline
        (step indicates the caller should return ``None``).
    guard:
        Optional predicate.  If it returns ``False``, the step is skipped.
    """

    name: str
    fn: Callable[[PipelineContext], Any]
    guard: Callable[[PipelineContext], bool] | None = None


class Pipeline:
    """Sequences steps and applies middleware.

    Steps run in order.  If any step returns ``None`` or ``HALT``, the
    pipeline stops and ``run()`` returns ``None``.  Middleware hooks
    run before/after each step — a ``before`` hook returning ``HALT``
    also stops the pipeline.

    The final pipeline result is ``ctx.state["result"]`` if set by the
    last step, otherwise ``ctx.state`` itself.
    """

    def __init__(
        self,
        name: str,
        steps: list[Step],
        middleware: list[Middleware] | None = None,
    ) -> None:
        self.name = name
        self.steps = steps
        self.middleware = middleware or []

    def run(self, ctx: PipelineContext) -> Any | None:
        """Execute all steps in sequence, applying middleware."""
        for step in self.steps:
            if step.guard is not None and not step.guard(ctx):
                continue

            # Before hooks
            for mw in self.middleware:
                check = mw.before(ctx, step.name)
                if check is HALT:
                    return None

            result = step.fn(ctx)
            if result is None or result is HALT:
                return None

            # After hooks
            for mw in self.middleware:
                check = mw.after(ctx, step.name, result)
                if check is HALT:
                    return None

        return ctx.state.get("result", ctx.state)
