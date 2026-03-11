"""Shared dynamic-prompt safety validator.

Provides fail-closed validation of rendered prompt content before
dispatch.  Used by both ``section_loop`` and ``scan`` prompt builders
to enforce the same prohibited-pattern rules across all dynamic
prompt surfaces.

R83/P2: Extracted from ``section_loop.agent_templates`` so scan
builders can reuse the same mechanical guard without coupling to
section-loop internals.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prohibited patterns in dynamic content
# ---------------------------------------------------------------------------

_PROHIBITED_PATTERNS: list[tuple[str, str]] = [
    (
        r"\buv\s+run\s+agents?\b",
        "Dynamic content must not instruct agent spawning (uv run agents)",
    ),
    (
        r"\bagents\s+--model\b",
        "Dynamic content must not instruct agent spawning (agents binary)",
    ),
    (
        r"\b(?:spawn|launch|create|invoke)\s+(?:an?\s+)?(?:sub-?)?agent",
        "Dynamic content must not instruct sub-agent spawning",
    ),
    (
        r"\b(?:import|install|pip\s+install)\s+(?:new\s+)?(?:tool|package)",
        "Dynamic content must not instruct importing new tools/packages",
    ),
    (
        r"\boverride\s+(?:system\s+)?constraints?\b",
        "Dynamic content must not instruct overriding system constraints",
    ),
    (
        r"\bignore\s+(?:the\s+)?(?:system\s+)?constraints?\b",
        "Dynamic content must not instruct ignoring system constraints",
    ),
    (
        r"\bdisregard\s+(?:the\s+)?(?:above|system|immutable)\b",
        "Dynamic content must not instruct disregarding system constraints",
    ),
]


def validate_dynamic_content(content: str) -> list[str]:
    """Check dynamic content for prohibited patterns.

    Returns a list of violation descriptions. An empty list means the
    content is valid. Violations block dispatch -- callers must not
    proceed when this returns a non-empty list.
    """
    violations: list[str] = []
    content_lower = content.lower()
    for pattern, description in _PROHIBITED_PATTERNS:
        if re.search(pattern, content_lower):
            violations.append(description)
    return violations


def write_validated_prompt(content: str, path: Path) -> bool:
    """Validate dynamic content and write to *path*.

    Always writes the content (for forensic inspection on failure).
    Returns ``True`` if validation passed.  Returns ``False`` on
    violation — caller must not dispatch.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    violations = validate_dynamic_content(content)
    if violations:
        _logger.warning(
            "Prompt safety violation at %s: %s — dispatch blocked",
            path, violations,
        )
        return False
    return True
