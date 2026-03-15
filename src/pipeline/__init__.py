"""Lightweight pipeline engine for composing multi-step workflows.

Provides step sequencing with middleware hooks (alignment guards,
logging, traceability) so that engine/ orchestration functions can
be expressed as a declared list of steps rather than inlining
cross-cutting concerns alongside business logic.
"""

from pipeline.context import PipelineContext
from pipeline.engine import HALT, Pipeline, Step
from pipeline.middleware import AlignmentGuard, StepLogger

__all__ = [
    "AlignmentGuard",
    "HALT",
    "Pipeline",
    "PipelineContext",
    "Step",
    "StepLogger",
]
