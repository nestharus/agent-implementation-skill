"""Component test: scan validation fallback uses central policy defaults."""

from __future__ import annotations

from src.scan.service.scan_dispatch_config import DEFAULT_SCAN_MODELS


def test_scan_validation_default_is_glm() -> None:
    """PAT-0005: scan validation default must be glm, not claude-opus."""
    assert DEFAULT_SCAN_MODELS["validation"] == "glm"
