"""Testing data types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestResult:
    """Result of a single behavioral contract test.

    ``status`` is one of ``pass``, ``fail``.
    """

    test_name: str
    status: str
    failure_summary: str | None
    contract_description: str
    seam: str
