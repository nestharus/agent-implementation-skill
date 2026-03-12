"""Component tests for impact_analyzer."""

from __future__ import annotations

from dependency_injector import providers

from containers import AgentDispatcher, PromptGuard, Services
from src.implementation.service import impact_analyzer
from src.orchestrator.types import Section


def _write_section(path, summary: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nsummary: {summary}\n---\n", encoding="utf-8")


def test_collect_impact_candidates_uses_shared_refs_and_contracts(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    artifacts = planspace / "artifacts"
    (artifacts / "inputs" / "section-01").mkdir(parents=True, exist_ok=True)
    (artifacts / "inputs" / "section-02").mkdir(parents=True, exist_ok=True)
    (artifacts / "contracts").mkdir(parents=True, exist_ok=True)
    (artifacts / "inputs" / "section-01" / "shared.ref").write_text("", encoding="utf-8")
    (artifacts / "inputs" / "section-02" / "shared.ref").write_text("", encoding="utf-8")
    (artifacts / "contracts" / "contract-01-04.md").write_text("contract", encoding="utf-8")

    section_paths = [tmp_path / f"section-{num}.md" for num in ("01", "02", "03", "04")]
    for idx, path in enumerate(section_paths, start=1):
        _write_section(path, f"Section {idx}")

    sections = [
        Section(number="01", path=section_paths[0], related_files=["pkg/a.py"]),
        Section(number="02", path=section_paths[1], related_files=[]),
        Section(number="03", path=section_paths[2], related_files=[]),
        Section(number="04", path=section_paths[3], related_files=[]),
    ]

    candidates = impact_analyzer.collect_impact_candidates(
        planspace,
        "01",
        ["pkg/other.py"],
        sections,
    )

    assert [section.number for section in candidates] == ["02", "04"]


def test_analyze_impacts_parses_material_impacts_from_primary_output(
    tmp_path, monkeypatch,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    (planspace / "artifacts").mkdir(parents=True, exist_ok=True)
    codespace.mkdir(parents=True, exist_ok=True)

    source_path = tmp_path / "section-01.md"
    target_path = tmp_path / "section-02.md"
    _write_section(source_path, "Source summary")
    _write_section(target_path, "Target summary")
    sections = [
        Section(number="01", path=source_path, related_files=["pkg/api.py"]),
        Section(number="02", path=target_path, related_files=["pkg/api.py"]),
    ]

    monkeypatch.setattr(impact_analyzer, "materialize_context_sidecar", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(impact_analyzer, "_log_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(impact_analyzer, "log", lambda _msg: None)
    monkeypatch.setattr(impact_analyzer.subprocess, "run", lambda *args, **kwargs: None)

    class _MockGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            return (
                '{"impacts": [{"to": "2", "impact": "MATERIAL", '
                '"reason": "Changed API", "contract_risk": true, '
                '"note_markdown": "Update consumer"}]}'
            )

    Services.prompt_guard.override(providers.Object(_MockGuard()))
    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    try:
        impacts = impact_analyzer.analyze_impacts(
            planspace,
            "01",
            "Source summary",
            ["pkg/api.py"],
            sections,
            codespace,
            "parent-task",
            summary_reader=lambda path: path.read_text(encoding="utf-8"),
            impact_model="glm",
            normalizer_model="glm",
        )

        assert impacts == [("02", "Changed API", True, "Update consumer")]
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()


def test_analyze_impacts_falls_back_to_normalizer_when_primary_is_invalid(
    tmp_path, monkeypatch,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    (planspace / "artifacts").mkdir(parents=True, exist_ok=True)
    codespace.mkdir(parents=True, exist_ok=True)

    source_path = tmp_path / "section-01.md"
    target_path = tmp_path / "section-03.md"
    _write_section(source_path, "Source summary")
    _write_section(target_path, "Target summary")
    sections = [
        Section(number="01", path=source_path, related_files=["pkg/service.py"]),
        Section(number="03", path=target_path, related_files=["pkg/service.py"]),
    ]

    monkeypatch.setattr(impact_analyzer, "materialize_context_sidecar", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(impact_analyzer, "_log_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(impact_analyzer, "log", lambda _msg: None)
    monkeypatch.setattr(impact_analyzer.subprocess, "run", lambda *args, **kwargs: None)

    class _MockGuard(PromptGuard):
        def validate_dynamic(self, content):
            return []
        def write_validated(self, content, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

    class _MockDispatcher(AgentDispatcher):
        def dispatch(self, *args, **kwargs):
            if kwargs.get("agent_file") == "impact-analyzer.md":
                return "not valid json"
            return (
                '{"impacts": [{"to": "03", "impact": "MATERIAL", '
                '"reason": "Normalized reason", "note_markdown": "Normalized note"}]}'
            )

    Services.prompt_guard.override(providers.Object(_MockGuard()))
    Services.dispatcher.override(providers.Object(_MockDispatcher()))
    try:
        impacts = impact_analyzer.analyze_impacts(
            planspace,
            "01",
            "Source summary",
            ["pkg/service.py"],
            sections,
            codespace,
            "parent-task",
            summary_reader=lambda path: path.read_text(encoding="utf-8"),
            impact_model="glm",
            normalizer_model="glm",
        )

        assert impacts == [("03", "Normalized reason", False, "Normalized note")]
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
