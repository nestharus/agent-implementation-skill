"""Tier ranking helpers for deep scan."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scan.service.template_loader import load_scan_template
from scan.codemap.cache import strip_scan_summaries
from scan.scan_dispatcher import dispatch_agent

if TYPE_CHECKING:
    from containers import ArtifactIOService, HasherService, PromptGuard, TaskRouterService


class TierRanker:
    """Tier ranking dispatch and validation.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        hasher: HasherService,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._hasher = hasher
        self._prompt_guard = prompt_guard
        self._task_router = task_router

    def validate_tier_file(self, tier_file: Path) -> bool:
        """Validate tier file structure: valid JSON with required fields."""
        data = self._artifact_io.read_json(tier_file)
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

    def _check_tier_freshness(
        self,
        tier_file: Path, tier_inputs_sidecar: Path,
        tier_inputs_hash: str, section_name: str,
    ) -> bool | None:
        """Check if tier file is fresh.

        Returns True if fresh (skip), False if stale/invalid (regenerate),
        None if tier file doesn't exist.
        """
        if not tier_file.is_file():
            return None
        if not self.validate_tier_file(tier_file):
            print(
                f"[TIER] {section_name}: existing tier file invalid "
                "(missing scan_now or bad schema) — preserving as "
                ".malformed.json and regenerating",
            )
            if self._artifact_io.rename_malformed(tier_file) is None:
                tier_file.unlink()
            return False
        if (
            tier_inputs_sidecar.is_file()
            and tier_inputs_sidecar.read_text(encoding="utf-8").strip() == tier_inputs_hash
        ):
            return True
        print(
            f"[TIER] {section_name}: inputs changed since last "
            "tier ranking — regenerating",
        )
        tier_file.unlink()
        return False

    def _dispatch_tier_with_escalation(
        self,
        section_name: str, codespace: Path, tier_prompt: Path,
        tier_output: Path, artifacts_dir: Path,
        related_files: list[str], model_policy: dict[str, str],
    ) -> None:
        """Dispatch tier ranking with model escalation on failure."""
        tier_model = model_policy["tier_ranking"]
        escalation_model = model_policy["exploration"]
        result = dispatch_agent(
            model=tier_model,
            project=codespace,
            prompt_file=tier_prompt,
            agent_file=self._task_router.agent_for("scan.tier_rank"),
            stdout_file=tier_output,
        )

        if result.returncode == 0:
            print(f"[TIER] {section_name}: file tiers ranked")
            return

        print(
            f"[TIER] {section_name}: tier ranking failed with {tier_model} "
            f"— escalating to {escalation_model}",
        )
        result = dispatch_agent(
            model=escalation_model,
            project=codespace,
            prompt_file=tier_prompt,
            agent_file=self._task_router.agent_for("scan.tier_rank"),
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
            fail_path = (artifacts_dir / "signals"
                         / f"{section_name}-tier-ranking-failed.json")
            self._artifact_io.write_json(
                fail_path,
                {
                    "section": section_name,
                    "related_files_count": len(related_files),
                    "error_output": str(tier_output),
                    "suggested_action": "manual_review_or_parent_escalation",
                },
            )

    def _validate_generated_tier(
        self,
        tier_file: Path, section_name: str, artifacts_dir: Path,
    ) -> None:
        """Validate a newly generated tier file, removing it if invalid."""
        if not tier_file.is_file() or self.validate_tier_file(tier_file):
            return
        print(f"[TIER] {section_name}: generated tier file invalid — fail-closed")
        fail_path = (artifacts_dir / "signals"
                     / f"{section_name}-tier-ranking-invalid.json")
        self._artifact_io.write_json(
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

    def run_tier_ranking(
        self,
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
        tier_inputs_hash = self._hasher.content_hash(tier_inputs)

        freshness = self._check_tier_freshness(
            tier_file, tier_inputs_sidecar, tier_inputs_hash, section_name,
        )
        if freshness is True:
            return tier_file

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
        violations = self._prompt_guard.validate_dynamic(prompt)
        if violations:
            print(
                f"[TIER] {section_name}: prompt blocked — "
                f"safety violations: {violations}",
            )
            return None
        tier_prompt.write_text(prompt, encoding="utf-8")

        self._dispatch_tier_with_escalation(
            section_name, codespace, tier_prompt, tier_output,
            artifacts_dir, related_files, model_policy,
        )
        self._validate_generated_tier(tier_file, section_name, artifacts_dir)

        if tier_file.is_file():
            tier_inputs_sidecar.write_text(tier_inputs_hash, encoding="utf-8")

        return tier_file if tier_file.is_file() else None


# ------------------------------------------------------------------
# Backward-compat free function wrappers
# ------------------------------------------------------------------


def _default_ranker() -> TierRanker:
    from containers import Services
    return TierRanker(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )


def validate_tier_file(tier_file: Path) -> bool:
    """Validate tier file structure: valid JSON with required fields."""
    return _default_ranker().validate_tier_file(tier_file)


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
    return _default_ranker().run_tier_ranking(
        section_file, section_name, related_files,
        codespace, artifacts_dir, scan_log_dir, model_policy,
    )
