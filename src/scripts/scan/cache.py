"""FileCardCache: content-hash based file-card cache.

Reuses deep-scan analysis when the (section + source file) content
pair has not changed.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

# Regex to strip scan-generated summary blocks from section text.
# These blocks are wrapped in HTML comment markers by update_match().
_SCAN_SUMMARY_RE = re.compile(
    r'<!-- scan-summary:begin -->.*?<!-- scan-summary:end -->\n?',
    re.DOTALL,
)


def strip_scan_summaries(text: str) -> str:
    """Remove scan-generated summary blocks from section text.

    Scan summaries are derived annotations — they must not poison
    cache keys or tier-ranking inputs.
    """
    return _SCAN_SUMMARY_RE.sub('', text)


class FileCardCache:
    """Directory of cached file cards keyed by content hash.

    The cache key is ``sha256(section_content || source_content)``.
    Two files are stored per entry:

    - ``<hash>.md``  — the analysis response
    - ``<hash>-feedback.json`` — the structured feedback (optional)
    """

    def __init__(self, cards_dir: Path) -> None:
        self.cards_dir = cards_dir
        self.cards_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Key computation
    # ------------------------------------------------------------------

    @staticmethod
    def content_hash(
        section_file: Path,
        source_file: Path,
        *extra_files: Path,
    ) -> str:
        """Compute sha256 over concatenated file contents.

        The base key includes ``section_file`` and ``source_file``.
        Additional files (e.g. codemap corrections) can be passed as
        positional args to incorporate their content into the hash.
        When an extra file doesn't exist, it contributes nothing
        (graceful degradation).

        The section file (first arg) is normalized to exclude
        scan-generated summary blocks, preventing scan output from
        invalidating its own cache.
        """
        h = hashlib.sha256()
        # Normalize section file: strip scan summaries so derived
        # annotations don't poison the cache key.
        try:
            section_text = section_file.read_text()
            h.update(strip_scan_summaries(section_text).encode())
        except OSError:
            pass
        for p in (source_file, *extra_files):
            try:
                h.update(p.read_bytes())
            except OSError:
                pass
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, key: str) -> Path | None:
        """Return cached response path if it exists, else ``None``."""
        card = self.cards_dir / f"{key}.md"
        return card if card.is_file() else None

    def get_feedback(self, key: str) -> Path | None:
        """Return cached feedback path if it exists, else ``None``."""
        fb = self.cards_dir / f"{key}-feedback.json"
        return fb if fb.is_file() else None

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        response_file: Path,
        feedback_file: Path | None = None,
    ) -> None:
        """Copy response (and optionally feedback) into the cache.

        Only stores feedback if it passes schema validation. Invalid
        feedback is not cached to avoid permanently locking in bad data.
        """
        dst = self.cards_dir / f"{key}.md"
        shutil.copy2(response_file, dst)
        if feedback_file is not None and feedback_file.is_file():
            if is_valid_cached_feedback(feedback_file):
                fb_dst = self.cards_dir / f"{key}-feedback.json"
                shutil.copy2(feedback_file, fb_dst)


def is_valid_cached_feedback(feedback_path: Path) -> bool:
    """Check whether a cached feedback file is schema-valid.

    Required fields: ``relevant`` (bool), ``source_file`` (str).
    Returns ``True`` if valid, ``False`` if missing, malformed, or
    missing required fields.
    """
    import json

    if not feedback_path.is_file():
        return False
    try:
        data = json.loads(feedback_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[CACHE][WARN] Malformed cached feedback: "
            f"{feedback_path} ({exc})",
        )
        return False
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("relevant"), bool):
        return False
    if not isinstance(data.get("source_file"), str):
        return False
    return True
