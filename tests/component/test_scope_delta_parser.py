from __future__ import annotations

from orchestrator.path_registry import PathRegistry
from src.implementation.service.scope_delta_parser import (
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
    planspace = tmp_path
    paths = PathRegistry(planspace)
    paths.scope_deltas_dir().mkdir(parents=True)
    paths.scope_delta_section("3").write_text("{}", encoding="utf-8")

    assert normalize_section_id("3", paths) == "3"


def test_normalize_section_id_uses_zero_padded_match(tmp_path) -> None:
    planspace = tmp_path
    paths = PathRegistry(planspace)
    paths.scope_deltas_dir().mkdir(parents=True)
    paths.scope_delta_section("03").write_text("{}", encoding="utf-8")

    assert normalize_section_id("3", paths) == "03"
