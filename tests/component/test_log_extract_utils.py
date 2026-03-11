"""Component tests for shared log extraction helpers."""

from __future__ import annotations

from src.dispatch.helpers.log_extract import (
    infer_section,
    parse_timestamp,
    prompt_signature,
    summarize_text,
)


def test_parse_timestamp_accepts_iso_and_numeric_inputs() -> None:
    iso_ts, iso_ms = parse_timestamp("2026-01-15T10:30:00.123Z")
    num_ts, num_ms = parse_timestamp("1700000000")

    assert iso_ts == "2026-01-15T10:30:00.123Z"
    assert iso_ms == 1768473000123
    assert num_ts.endswith("Z")
    assert num_ms == 1700000000000


def test_prompt_signature_normalizes_whitespace() -> None:
    assert prompt_signature("hello   world") == prompt_signature(" hello world ")


def test_infer_section_returns_first_matching_candidate() -> None:
    assert infer_section("solver-03", "section-05") == "03"
    assert infer_section("no match") == ""


def test_summarize_text_collapses_whitespace_and_truncates() -> None:
    assert summarize_text("hello\n  world") == "hello world"
    assert summarize_text("x" * 200, limit=20) == ("x" * 17) + "..."
