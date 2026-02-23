"""FileCardCache: content-hash based file-card cache.

Reuses deep-scan analysis when the (section + source file) content
pair has not changed.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


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
    def content_hash(section_file: Path, source_file: Path) -> str:
        """Compute sha256 over concatenated file contents."""
        h = hashlib.sha256()
        for p in (section_file, source_file):
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
        """Copy response (and optionally feedback) into the cache."""
        dst = self.cards_dir / f"{key}.md"
        shutil.copy2(response_file, dst)
        if feedback_file is not None and feedback_file.is_file():
            fb_dst = self.cards_dir / f"{key}-feedback.json"
            shutil.copy2(feedback_file, fb_dst)
