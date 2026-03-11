"""Component tests for prompt context building."""

from __future__ import annotations

from pathlib import Path

from dispatch.prompts_context import build_prompt_context
from orchestrator.types import Section


def _make_section(planspace: Path, number: str = "01") -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(
        f"# Section {number}\n\nPrompt context test section.\n",
        encoding="utf-8",
    )
    return Section(number=number, path=section_path, related_files=[])


def test_context_builder_separates_risk_refs_from_coordination_refs(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _make_section(planspace)
    inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    risk_payload = inputs_dir / "section-01-risk-accepted-steps.json"
    risk_payload.write_text('{"accepted_steps": ["edit-02"]}\n', encoding="utf-8")
    (inputs_dir / "section-01-roal-input-index.json").write_text(
        (
            "[\n"
            f'  {{"kind": "accepted_frontier", "path": "{risk_payload}"}}\n'
            "]\n"
        ),
        encoding="utf-8",
    )

    coordination_payload = inputs_dir / "section-01-bridge-note.md"
    coordination_payload.write_text("# Bridge\n\nShared seam details.\n", encoding="utf-8")
    (inputs_dir / "bridge-note.ref").write_text(
        str(coordination_payload),
        encoding="utf-8",
    )

    ctx = build_prompt_context(section, planspace, codespace)

    assert "Risk Inputs (from ROAL)" in ctx["risk_inputs_block"]
    assert str(risk_payload) in ctx["risk_inputs_block"]
    assert "accepted_frontier" in ctx["risk_inputs_block"]
    assert "accepted frontier is your current local execution authority" in ctx[
        "risk_inputs_block"
    ]
    assert "Additional Inputs (from coordination)" in ctx["additional_inputs_block"]
    assert str(coordination_payload) in ctx["additional_inputs_block"]
    assert str(coordination_payload) not in ctx["risk_inputs_block"]
    assert str(risk_payload) not in ctx["additional_inputs_block"]


def test_context_builder_omits_risk_inputs_block_when_no_risk_refs(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _make_section(planspace)
    inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    coordination_payload = inputs_dir / "section-01-coordination-note.md"
    coordination_payload.write_text("note\n", encoding="utf-8")
    (inputs_dir / "coordination-note.ref").write_text(
        str(coordination_payload),
        encoding="utf-8",
    )

    ctx = build_prompt_context(section, planspace, codespace)

    assert ctx["risk_inputs_block"] == ""
    assert "Additional Inputs (from coordination)" in ctx["additional_inputs_block"]
    assert str(coordination_payload) in ctx["additional_inputs_block"]


def test_context_builder_uses_roal_index_without_ref_prefix_inference(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _make_section(planspace)
    inputs_dir = planspace / "artifacts" / "inputs" / "section-01"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    risk_payload = inputs_dir / "section-01-risk-deferred.json"
    risk_payload.write_text('{"deferred_steps": ["verify-03"]}\n', encoding="utf-8")
    (inputs_dir / "section-01-roal-input-index.json").write_text(
        (
            "[\n"
            f'  {{"kind": "deferred", "path": "{risk_payload}"}}\n'
            "]\n"
        ),
        encoding="utf-8",
    )
    stale_roal_ref = inputs_dir / "stale-risk.ref"
    stale_roal_ref.write_text(str(risk_payload), encoding="utf-8")

    ctx = build_prompt_context(section, planspace, codespace)

    assert str(risk_payload) in ctx["risk_inputs_block"]
    assert str(risk_payload) not in ctx["additional_inputs_block"]
    assert "stale-risk" not in ctx["additional_inputs_block"]


def test_context_builder_includes_governance_packet_reference(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _make_section(planspace)
    gov_packet = (
        planspace
        / "artifacts"
        / "governance"
        / "section-01-governance-packet.json"
    )
    gov_packet.parent.mkdir(parents=True, exist_ok=True)
    gov_packet.write_text('{"section": "01"}\n', encoding="utf-8")

    ctx = build_prompt_context(section, planspace, codespace)

    assert "Governance packet" in ctx["governance_ref"]
    assert str(gov_packet) in ctx["governance_ref"]
