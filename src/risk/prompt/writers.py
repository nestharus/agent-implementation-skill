"""ROAL prompt construction for risk assessment and execution optimization.

Builds structured prompts listing artifact paths, evidence, and context
for the Risk Agent and Execution Optimizer.
"""

from __future__ import annotations

import json
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from risk.service.package_builder import read_text, scope_number
from risk.types import RiskAssessment, RiskPackage


def write_risk_assessment_prompt(
    package: RiskPackage,
    planspace: Path,
    scope: str,
) -> str:
    """Build the prompt for the Risk Agent."""
    paths = PathRegistry(planspace)
    section_number = scope_number(scope)
    lines = [
        "# ROAL Risk Assessment",
        "",
        f"- Scope: `{scope}`",
        f"- Layer: `{package.layer}`",
        f"- Package ID: `{package.package_id}`",
        f"- Risk package: `{paths.risk_package(scope)}`",
    ]

    artifact_specs = [
        ("Section spec", paths.section_spec(section_number), "text"),
        ("Proposal excerpt", paths.proposal_excerpt(section_number), "text"),
        ("Alignment excerpt", paths.alignment_excerpt(section_number), "text"),
        ("Problem frame", paths.problem_frame(section_number), "text"),
        ("Governance packet", paths.governance_packet(section_number), "json"),
        ("Microstrategy", paths.microstrategy(section_number), "text"),
        ("Proposal state", paths.proposal_state(section_number), "json"),
        ("Readiness", paths.execution_ready(section_number), "json"),
        ("Tool registry", paths.tool_registry(), "json"),
        ("Codemap", paths.codemap(), "text"),
    ]
    lines.extend(["## Artifact Paths", "", "Read these artifacts for context:", ""])
    for title, path, kind in artifact_specs:
        if kind == "json":
            lines.extend(_json_block(title, path, Services.artifact_io().read_json(path)))
        else:
            lines.extend(_artifact_block(title, path, kind))

    corrections_path = paths.corrections()
    if corrections_path.exists():
        lines.extend(
            _artifact_block(
                "Codemap corrections (authoritative overrides)",
                corrections_path,
                "json",
            )
        )

    lines.extend(_json_block("Risk history", paths.risk_history(), None))
    lines.extend(_artifact_block("Monitor signals directory", paths.signals_dir(), "dir"))

    consequence_paths = sorted(
        paths.notes_dir().glob(f"from-*-to-{section_number}.md")
    )
    outgoing_paths = sorted(
        paths.notes_dir().glob(f"from-{section_number}-to-*.md")
    )
    impact_paths = sorted(paths.coordination_dir().glob(f"*{scope}*"))
    lines.extend(_path_list_block("Incoming consequence notes", consequence_paths))
    lines.extend(_path_list_block("Outgoing consequence notes", outgoing_paths))
    lines.extend(_path_list_block("Impact artifacts", impact_paths))
    evidence = _collect_roal_evidence(paths, scope, section_number)
    if evidence:
        lines.extend(["", "## Reassessment Evidence", ""])
        for title, path in evidence:
            lines.append(f"- {title}: `{path}`")

    return "\n".join(lines).strip() + "\n"


def write_optimization_prompt(
    assessment: RiskAssessment,
    package: RiskPackage,
    parameters: dict,
    planspace: Path,
    scope: str,
    *,
    lightweight: bool = False,
) -> str:
    """Build the prompt for the Tool Agent (Execution Optimizer)."""
    del assessment, package, parameters
    paths = PathRegistry(planspace)
    lines = [
        "# ROAL Execution Optimization",
        "",
        f"- Risk assessment: `{paths.risk_assessment(scope)}`",
        f"- Risk package: `{paths.risk_package(scope)}`",
        "## Artifact Paths",
        "",
        "Read these artifacts for context:",
        "",
    ]
    lines.extend(_json_block("Risk parameters", paths.risk_parameters(), Services.artifact_io().read_json(paths.risk_parameters())))
    lines.extend(_json_block("Tool registry", paths.tool_registry(), Services.artifact_io().read_json(paths.tool_registry())))
    lines.extend(_json_block("Risk history", paths.risk_history(), None))
    if lightweight:
        lines.extend(
            [
                "",
                "## Lightweight Mode",
                "",
                "This is a single-pass lightweight risk check.",
                "No iteration, repeated reassessment, or horizon refinement is available.",
                "Return the standard structured risk plan JSON for the provided assessment.",
            ]
        )
    return "\n".join(lines).strip() + "\n"


# -- Formatting helpers ----------------------------------------------------


def _artifact_block(title: str, path: Path, kind: str) -> list[str]:
    content = read_text(path)
    lines = [
        f"- {title}: `{path}`",
    ]
    if kind == "dir":
        if not path.exists():
            lines[-1] += " (missing)"
        return lines
    if not path.exists():
        lines[-1] += " (missing)"
        return lines
    if kind == "text" and not content:
        lines[-1] += " (empty)"
    return lines


def _json_block(title: str, path: Path, payload: object) -> list[str]:
    del payload
    lines = [f"- {title}: `{path}`"]
    if not path.exists():
        lines[-1] += " (missing)"
        return lines
    try:
        if path.is_file() and path.stat().st_size == 0:
            lines[-1] += " (empty)"
    except OSError:
        lines[-1] += " (unreadable)"
    return lines


def _inline_json_block(title: str, payload: object) -> list[str]:
    return [
        f"## {title}",
        "",
        "```json",
        json.dumps(payload, indent=2),
        "```",
        "",
    ]


def _path_list_block(title: str, paths: list[Path]) -> list[str]:
    if not paths:
        return [f"- {title}: none"]
    if len(paths) == 1:
        return [f"- {title}: `{paths[0]}`"]
    return [f"- {title}: " + ", ".join(f"`{path}`" for path in paths)]


def _collect_roal_evidence(
    paths: PathRegistry,
    scope: str,
    section_number: str,
) -> list[tuple[str, Path]]:
    """Collect section-scoped evidence artifacts for ROAL prompts."""
    evidence: list[tuple[str, Path]] = []

    manifest_path = (
        paths.input_refs_dir(section_number)
        / f"section-{section_number}-modified-file-manifest.json"
    )
    if manifest_path.exists():
        evidence.append(("Modified-file manifest", manifest_path))

    align_result = paths.artifacts / f"impl-align-{section_number}-output.md"
    if align_result.exists():
        evidence.append(("Alignment check result", align_result))

    for recon in sorted(paths.reconciliation_dir().glob(f"*{scope}*")):
        evidence.append(("Reconciliation result", recon))

    for risk_artifact_name in (
        f"section-{section_number}-risk-accepted-steps.json",
        f"section-{section_number}-risk-deferred.json",
    ):
        risk_path = paths.input_refs_dir(section_number) / risk_artifact_name
        if risk_path.exists():
            evidence.append(("Previous risk artifact", risk_path))

    return evidence
