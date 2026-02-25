"""Template loading and rendering for prompt generation."""

from collections import defaultdict
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"


def load_template(name: str) -> str:
    """Load a .md template from the templates directory."""
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def render(template_text: str, context: dict) -> str:
    """Render a template with context, defaulting missing keys to empty string."""
    return template_text.format_map(defaultdict(str, context))
