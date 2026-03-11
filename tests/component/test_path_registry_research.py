"""Component tests for research artifact paths in PathRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.path_registry import PathRegistry


@pytest.fixture()
def reg(tmp_path: Path) -> PathRegistry:
    return PathRegistry(tmp_path)


def test_research_dir(reg: PathRegistry, tmp_path: Path) -> None:
    assert reg.research_dir() == tmp_path / "artifacts" / "research"


def test_research_sections_dir(reg: PathRegistry, tmp_path: Path) -> None:
    assert reg.research_sections_dir() == (
        tmp_path / "artifacts" / "research" / "sections"
    )


def test_research_global_dir(reg: PathRegistry, tmp_path: Path) -> None:
    assert reg.research_global_dir() == (
        tmp_path / "artifacts" / "research" / "global"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_section_dir(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_section_dir(num) == (
        tmp_path / "artifacts" / "research" / "sections" / f"section-{num}"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_plan(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_plan(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "research-plan.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_status(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_status(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "research-status.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_trigger(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_trigger(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "research-trigger.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_dossier(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_dossier(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "dossier.md"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_claims(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_claims(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "dossier-claims.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_derived_surfaces(
    reg: PathRegistry, tmp_path: Path, num: str
) -> None:
    assert reg.research_derived_surfaces(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "research-derived-surfaces.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_addendum(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_addendum(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "proposal-addendum.md"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_verify_report(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_verify_report(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "research-verify.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_tickets_dir(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_tickets_dir(num) == (
        tmp_path
        / "artifacts"
        / "research"
        / "sections"
        / f"section-{num}"
        / "tickets"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_research_prompt_paths(reg: PathRegistry, tmp_path: Path, num: str) -> None:
    assert reg.research_plan_prompt(num) == (
        tmp_path / "artifacts" / f"research-plan-{num}-prompt.md"
    )
    assert reg.research_synthesis_prompt(num) == (
        tmp_path / "artifacts" / f"research-synthesis-{num}-prompt.md"
    )
    assert reg.research_verify_prompt(num) == (
        tmp_path / "artifacts" / f"research-verify-{num}-prompt.md"
    )


def test_research_ticket_artifact_paths(reg: PathRegistry, tmp_path: Path) -> None:
    base = tmp_path / "artifacts" / "research" / "sections" / "section-03" / "tickets"
    assert reg.research_ticket_spec("03", 1) == base / "ticket-01-spec.json"
    assert reg.research_ticket_prompt("03", 1) == base / "ticket-01-prompt.md"
    assert reg.research_ticket_result("03", 1) == base / "ticket-01-result.json"
    assert reg.research_scan_prompt("03", 1) == base / "ticket-01-scan-prompt.md"
    assert reg.research_ticket_spec("03", 1, "web") == (
        base / "ticket-01-web-spec.json"
    )
    assert reg.research_ticket_prompt("03", 1, "web") == (
        base / "ticket-01-web-prompt.md"
    )
    assert reg.research_ticket_result("03", 1, "web") == (
        base / "ticket-01-web-result.json"
    )


@pytest.mark.parametrize("num", ["01", "12"])
def test_proposal_state_and_intent_surfaces_paths(
    reg: PathRegistry,
    tmp_path: Path,
    num: str,
) -> None:
    assert reg.proposal_state(num) == (
        tmp_path
        / "artifacts"
        / "proposals"
        / f"section-{num}-proposal-state.json"
    )
    assert reg.intent_surfaces_signal(num) == (
        tmp_path
        / "artifacts"
        / "signals"
        / f"intent-surfaces-{num}.json"
    )
