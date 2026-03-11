from __future__ import annotations

from src.implementation.scope_delta_parser import (
    normalize_section_id,
    parse_scope_delta_adjudication,
)


def test_parse_scope_delta_adjudication_from_code_fence() -> None:
    output = (
        "Review complete.\n"
        "```json\n"
        '{"decisions":[{"delta_id":"d1","action":"accept","reason":"needed",'
        '"new_sections":["09"]}]}\n'
        "```\n"
    )

    result = parse_scope_delta_adjudication(output)

    assert result is not None
    assert result["decisions"][0]["delta_id"] == "d1"
    assert result["decisions"][0]["action"] == "accept"


def test_parse_scope_delta_adjudication_rejects_invalid_schema() -> None:
    output = '{"decisions":[{"delta_id":"d1","action":"accept","reason":"needed"}]}'

    assert parse_scope_delta_adjudication(output) is None


def test_normalize_section_id_prefers_existing_exact_file(tmp_path) -> None:
    scope_deltas_dir = tmp_path / "scope-deltas"
    scope_deltas_dir.mkdir()
    (scope_deltas_dir / "section-3-scope-delta.json").write_text("{}", encoding="utf-8")

    assert normalize_section_id("3", scope_deltas_dir) == "3"


def test_normalize_section_id_uses_zero_padded_match(tmp_path) -> None:
    scope_deltas_dir = tmp_path / "scope-deltas"
    scope_deltas_dir.mkdir()
    (scope_deltas_dir / "section-03-scope-delta.json").write_text("{}", encoding="utf-8")

    assert normalize_section_id("3", scope_deltas_dir) == "03"
