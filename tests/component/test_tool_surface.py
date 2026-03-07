from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.tools.tool_surface import (
    handle_tool_friction,
    surface_tool_registry,
    validate_tool_registry_after_implementation,
    write_tool_surface,
)


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
    artifacts = tmp_path / "artifacts"
    (artifacts / "signals").mkdir(parents=True)
    tool_registry_path = artifacts / "tool-registry.json"
    tool_registry_path.write_text("{bad json", encoding="utf-8")
    tools_available_path = artifacts / "sections" / "section-03-tools-available.md"
    tools_available_path.parent.mkdir(parents=True)
    tools_available_path.write_text("stale", encoding="utf-8")

    dispatch_calls: list[tuple] = []
    blocker_calls: list[Path] = []

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

    total = surface_tool_registry(
        section_number="03",
        tool_registry_path=tool_registry_path,
        tools_available_path=tools_available_path,
        artifacts=artifacts,
        planspace=tmp_path / "planspace",
        parent="parent",
        codespace=tmp_path / "codespace",
        policy={},
        dispatch_agent=fake_dispatch,
        log=lambda _: None,
        update_blocker_rollup=lambda path: blocker_calls.append(path),
    )

    assert total == 1
    assert dispatch_calls
    assert not blocker_calls
    assert tool_registry_path.with_suffix(".malformed.json").exists()
    assert "tools/b.py" in tools_available_path.read_text(encoding="utf-8")


def test_validate_tool_registry_after_implementation_dispatches_validator(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
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

    friction_path = validate_tool_registry_after_implementation(
        section_number="03",
        pre_tool_total=1,
        tool_registry_path=tool_registry_path,
        artifacts=artifacts,
        planspace=tmp_path / "planspace",
        parent="parent",
        codespace=tmp_path / "codespace",
        policy={},
        dispatch_agent=lambda *args, **kwargs: calls.append((args, kwargs)),
        log=lambda _: None,
        update_blocker_rollup=lambda _: None,
    )

    assert calls
    assert friction_path == artifacts / "signals" / "section-03-tool-friction.json"


def test_handle_tool_friction_bridges_and_acknowledges_signal(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    (artifacts / "signals").mkdir(parents=True)
    (artifacts / "proposals").mkdir(parents=True)
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

    notes: list[tuple[Path, str, str, str]] = []

    handle_tool_friction(
        section_number="03",
        section_path="spec.md",
        all_sections=None,
        artifacts=artifacts,
        tool_registry_path=tool_registry_path,
        friction_signal_path=friction_signal_path,
        planspace=tmp_path / "planspace",
        parent="parent",
        codespace=tmp_path / "codespace",
        policy={"escalation_model": "escalation"},
        dispatch_agent=fake_dispatch,
        log=lambda _: None,
        write_consequence_note=lambda *args: notes.append(args),
        update_blocker_rollup=lambda _: None,
    )

    assert notes
    assert (artifacts / "inputs" / "section-03" / "tool-bridge.ref").exists()
    friction_state = json.loads(friction_signal_path.read_text(encoding="utf-8"))
    assert friction_state["friction"] is False
    assert friction_state["status"] == "handled"
