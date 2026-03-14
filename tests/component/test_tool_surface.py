from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from dependency_injector import providers

from containers import Services
from src.orchestrator.path_registry import PathRegistry
from tests.conftest import WritingGuard, make_dispatcher, StubPolicies

from src.dispatch.service.tool_surface_writer import surface_tool_registry, write_tool_surface
from src.dispatch.service.tool_validator import validate_tool_registry_after_implementation
from src.dispatch.service.tool_bridge import handle_tool_friction


def test_write_tool_surface_filters_cross_section_and_local_tools(tmp_path) -> None:
    surface_path = tmp_path / "tools.md"
    count = write_tool_surface(
        [
            {"path": "tools/a.py", "scope": "cross-section", "created_by": "section-02"},
            {"path": "tools/b.py", "scope": "section-local", "created_by": "section-03"},
            {"path": "tools/c.py", "scope": "section-local", "created_by": "section-04"},
        ],
        "03",
        surface_path,
    )

    assert count == 2
    content = surface_path.read_text(encoding="utf-8")
    assert "tools/a.py" in content
    assert "tools/b.py" in content
    assert "tools/c.py" not in content


def test_surface_tool_registry_repairs_malformed_registry(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    tool_registry_path = artifacts / "tool-registry.json"
    tool_registry_path.write_text("{bad json", encoding="utf-8")
    tools_available_path = artifacts / "sections" / "section-03-tools-available.md"
    tools_available_path.write_text("stale", encoding="utf-8")

    dispatch_calls: list[tuple] = []

    def fake_dispatch(*args, **kwargs):
        dispatch_calls.append((args, kwargs))
        tool_registry_path.write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "path": "tools/b.py",
                            "scope": "section-local",
                            "created_by": "section-03",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    Services.dispatcher.override(providers.Object(make_dispatcher(fake_dispatch)))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.policies.override(providers.Object(StubPolicies()))
    Services.logger.override(providers.Object(MagicMock()))
    try:
        total = surface_tool_registry(
            section_number="03",
            planspace=planspace,
            parent="parent",
            codespace=tmp_path / "codespace",
        )

        assert total == 1
        assert dispatch_calls
        assert tool_registry_path.with_suffix(".malformed.json").exists()
        assert "tools/b.py" in tools_available_path.read_text(encoding="utf-8")
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()
        Services.logger.reset_override()


def test_validate_tool_registry_after_implementation_dispatches_validator(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    tool_registry_path = artifacts / "tool-registry.json"
    tool_registry_path.write_text(
        json.dumps(
            {
                "tools": [
                    {"path": "a.py"},
                    {"path": "b.py"},
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple] = []

    def fake_dispatch(*args, **kwargs):
        calls.append((args, kwargs))

    Services.dispatcher.override(providers.Object(make_dispatcher(fake_dispatch)))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.policies.override(providers.Object(StubPolicies()))
    Services.logger.override(providers.Object(MagicMock()))
    try:
        friction_path = validate_tool_registry_after_implementation(
            section_number="03",
            pre_tool_total=1,
            planspace=planspace,
            parent="parent",
            codespace=tmp_path / "codespace",
        )

        assert calls
        assert friction_path == artifacts / "signals" / "section-03-tool-friction.json"
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()
        Services.logger.reset_override()


def test_handle_tool_friction_bridges_and_acknowledges_signal(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    tool_registry_path = artifacts / "tool-registry.json"
    tool_registry_path.write_text(json.dumps({"tools": [{"path": "a.py"}]}), encoding="utf-8")
    friction_signal_path = artifacts / "signals" / "section-03-tool-friction.json"
    friction_signal_path.write_text(json.dumps({"friction": True}), encoding="utf-8")

    def fake_dispatch(model, prompt_path, output_path, *args, **kwargs):
        if "bridge-tools" in str(prompt_path):
            proposal_path = artifacts / "proposals" / "section-03-tool-bridge.md"
            proposal_path.write_text("# proposal\n", encoding="utf-8")
            signal_path = artifacts / "signals" / "section-03-tool-bridge.json"
            signal_path.write_text(
                json.dumps(
                    {
                        "status": "bridged",
                        "proposal_path": str(proposal_path),
                        "targets": ["04"],
                        "broadcast": False,
                        "note_markdown": "Use the bridge.",
                    }
                ),
                encoding="utf-8",
            )

    mock_cross_section = MagicMock()
    notes: list[tuple] = []
    mock_cross_section.write_consequence_note = lambda *args: notes.append(args)

    Services.dispatcher.override(providers.Object(make_dispatcher(fake_dispatch)))
    Services.prompt_guard.override(providers.Object(WritingGuard()))
    Services.policies.override(providers.Object(StubPolicies()))
    Services.logger.override(providers.Object(MagicMock()))
    Services.cross_section.override(providers.Object(mock_cross_section))
    try:
        handle_tool_friction(
            section_number="03",
            section_path="spec.md",
            all_sections=None,
            planspace=planspace,
            parent="parent",
            codespace=tmp_path / "codespace",
        )

        assert notes
        assert (artifacts / "inputs" / "section-03" / "tool-bridge.ref").exists()
        friction_state = json.loads(friction_signal_path.read_text(encoding="utf-8"))
        assert friction_state["friction"] is False
        assert friction_state["status"] == "handled"
    finally:
        Services.dispatcher.reset_override()
        Services.prompt_guard.reset_override()
        Services.policies.reset_override()
        Services.logger.reset_override()
        Services.cross_section.reset_override()
