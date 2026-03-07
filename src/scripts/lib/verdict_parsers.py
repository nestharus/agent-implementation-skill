"""VerdictParsers: parse structured JSON verdicts from LLM judge output.

Pure parsing logic (Tier 1). No I/O, no domain knowledge beyond the
expected JSON shape. Takes a string, returns structured data or None.
"""

from __future__ import annotations

import json as _json


def parse_alignment_verdict(output: str) -> dict | None:
    """Parse structured verdict from alignment judge output.

    Looks for a JSON block containing ``frame_ok``.  Returns the full
    dict (which may also contain ``aligned`` and ``problems``), or
    ``None`` if no JSON verdict is found.

    Scanning order:

    1. Single-line JSON — each line is checked for a ``{`` prefix and
       ``frame_ok`` substring, then parsed.
    2. Code-fenced JSON — content between triple-backtick fences is
       collected and parsed if it contains ``frame_ok``.

    The first valid match wins.
    """

    def _try_parse(text: str) -> dict | None:
        try:
            data = _json.loads(text)
            if isinstance(data, dict) and "frame_ok" in data:
                return data
        except _json.JSONDecodeError:
            pass
        return None

    # Single-line JSON
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("{") and "frame_ok" in stripped:
            parsed = _try_parse(stripped)
            if parsed:
                return parsed

    # Code-fenced JSON
    in_fence = False
    fence_lines: list[str] = []
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```") and not in_fence:
            in_fence = True
            fence_lines = []
            continue
        if stripped.startswith("```") and in_fence:
            candidate = "\n".join(fence_lines)
            if "frame_ok" in candidate:
                parsed = _try_parse(candidate)
                if parsed:
                    return parsed
            in_fence = False
            continue
        if in_fence:
            fence_lines.append(line)
    return None
