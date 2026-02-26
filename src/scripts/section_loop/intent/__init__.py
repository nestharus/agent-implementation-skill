"""Intent layer: bidirectional alignment + expansion for section loop.

Provides surface discovery, problem/philosophy expansion, and convergence
detection on top of the existing proposal → align → implement pipeline.
"""

from .bootstrap import ensure_global_philosophy, generate_intent_pack
from .expansion import run_expansion_cycle
from .surfaces import (
    find_discarded_recurrences,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
)
from .triage import run_intent_triage

__all__ = [
    "ensure_global_philosophy",
    "find_discarded_recurrences",
    "generate_intent_pack",
    "load_surface_registry",
    "merge_surfaces_into_registry",
    "normalize_surface_ids",
    "run_expansion_cycle",
    "run_intent_triage",
]
