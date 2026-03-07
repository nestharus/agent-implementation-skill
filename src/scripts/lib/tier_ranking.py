"""Tier ranking helpers for deep scan."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .artifact_io import read_json, rename_malformed, write_json
from .hash_service import content_hash
from .scan_template_loader import load_scan_template
from prompt_safety import validate_dynamic_content
from scan.cache import strip_scan_summaries
from scan.dispatch import dispatch_agent


def validate_tier_file(tier_file: Path) -> bool:
    """Validate tier file structure: valid JSON with required fields."""
    data = read_json(tier_file)
    if data is None:
        return False

    tiers = data.get("tiers")
    if not isinstance(tiers, dict):
        return False

    scan_now = data.get("scan_now")
    if not isinstance(scan_now, list) or not scan_now:
        return False

    for tier_name in scan_now:
        if tier_name not in tiers:
            return False

    return True


def run_tier_ranking(
    section_file: Path,
    section_name: str,
    related_files: list[str],
    codespace: Path,
    artifacts_dir: Path,
    scan_log_dir: Path,
    model_policy: dict[str, str],
) -> Path | None:
    """Dispatch tier ranking and return the tier file path on success."""
    tier_file = artifacts_dir / "sections" / f"{section_name}-file-tiers.json"
    tier_inputs_sidecar = (
        artifacts_dir / "sections" / f"{section_name}-file-tiers.inputs.sha256"
    )

    raw_section = section_file.read_text(encoding="utf-8")
    tier_inputs = strip_scan_summaries(raw_section) + "\n" + "\n".join(
        sorted(related_files),
    )
    tier_inputs_hash = content_hash(tier_inputs)

    if tier_file.is_file():
        if not validate_tier_file(tier_file):
            print(
                f"[TIER] {section_name}: existing tier file invalid "
                "(missing scan_now or bad schema) — preserving as "
                ".malformed.json and regenerating",
            )
            if rename_malformed(tier_file) is None:
                tier_file.unlink()
        elif (
            tier_inputs_sidecar.is_file()
            and tier_inputs_sidecar.read_text(encoding="utf-8").strip() == tier_inputs_hash
        ):
            return tier_file
        else:
            print(
                f"[TIER] {section_name}: inputs changed since last "
                "tier ranking — regenerating",
            )
            tier_file.unlink()

    section_log = scan_log_dir / section_name
    section_log.mkdir(parents=True, exist_ok=True)
    tier_prompt = section_log / "tier-prompt.md"
    tier_output = section_log / "tier-output.md"

    file_list_text = "\n".join(
        f"- {related_file}" for related_file in related_files if related_file.strip()
    )
    prompt = load_scan_template("tier_ranking.md").format(
        section_file=section_file,
        file_list_text=file_list_text,
        tier_file=tier_file,
    )
    violations = validate_dynamic_content(prompt)
    if violations:
        print(
            f"[TIER] {section_name}: prompt blocked — "
            f"safety violations: {violations}",
        )
        return None
    tier_prompt.write_text(prompt, encoding="utf-8")

    tier_model = model_policy.get("tier_ranking", "glm")
    escalation_model = model_policy.get("exploration", "claude-opus")
    result = dispatch_agent(
        model=tier_model,
        project=codespace,
        prompt_file=tier_prompt,
        agent_file="scan-tier-ranker.md",
        stdout_file=tier_output,
    )

    if result.returncode == 0:
        print(f"[TIER] {section_name}: file tiers ranked")
    else:
        print(
            f"[TIER] {section_name}: tier ranking failed with {tier_model} "
            f"— escalating to {escalation_model}",
        )
        result = dispatch_agent(
            model=escalation_model,
            project=codespace,
            prompt_file=tier_prompt,
            agent_file="scan-tier-ranker.md",
            stdout_file=tier_output,
        )
        if result.returncode == 0:
            print(
                f"[TIER] {section_name}: file tiers ranked "
                "(via Opus escalation)",
            )
        else:
            print(
                f"[TIER] {section_name}: tier ranking failed after "
                "escalation — fail-closed",
            )
            signals_dir = artifacts_dir / "signals"
            signals_dir.mkdir(parents=True, exist_ok=True)
            fail_path = signals_dir / f"{section_name}-tier-ranking-failed.json"
            write_json(
                fail_path,
                {
                    "section": section_name,
                    "related_files_count": len(related_files),
                    "error_output": str(tier_output),
                    "suggested_action": "manual_review_or_parent_escalation",
                },
            )

    if tier_file.is_file() and not validate_tier_file(tier_file):
        print(f"[TIER] {section_name}: generated tier file invalid — fail-closed")
        signals_dir = artifacts_dir / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)
        fail_path = signals_dir / f"{section_name}-tier-ranking-invalid.json"
        write_json(
            fail_path,
            {
                "section": section_name,
                "error": "invalid_tier_file_schema",
                "detail": "Tier file missing scan_now or has invalid structure",
                "tier_file_path": str(tier_file),
                "suggested_action": "manual_review_or_parent_escalation",
            },
        )
        tier_file.unlink()

    if tier_file.is_file():
        tier_inputs_sidecar.write_text(tier_inputs_hash, encoding="utf-8")

    return tier_file if tier_file.is_file() else None
