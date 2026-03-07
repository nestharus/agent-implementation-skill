"""Compatibility wrapper for dynamic prompt template helpers."""

from __future__ import annotations

from lib.prompt_template import (
    SYSTEM_CONSTRAINTS,
    TASK_SUBMISSION_SEMANTICS,
    render_template,
)
from prompt_safety import validate_dynamic_content  # noqa: F401 — re-export
