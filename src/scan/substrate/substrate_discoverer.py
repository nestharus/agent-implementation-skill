"""Stage 3.5 Shared Integration Substrate (SIS) runner.

Orchestrates the three-phase substrate discovery pipeline:

  Phase A: Shard exploration (per target section)
  Phase B: Pruning (strategic merge of all shards)
  Phase C: Seeding (anchor creation + related-files wiring)

Entry point: ``run_substrate_discovery(planspace, codespace)``.
CLI entry point: ``main()``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from scan.related.related_file_resolver import list_section_files as _list_section_files
from scan.substrate.substrate_state_reader import (
    SubstrateStateReader,
    count_existing_related as _count_existing_related,
    section_number as _section_number,
)
from scan.substrate.policy import Policy
from scan.substrate.prompt_builder import PromptBuilder
from scan.substrate.related_files import RelatedFiles
from scan.substrate.schemas import Schemas
from scan.substrate.substrate_dispatcher import SubstrateDispatcher
from signals.types import BLOCKING_NEEDS_PARENT

if TYPE_CHECKING:
    from containers import ArtifactIOService, PromptGuard, TaskRouterService


@dataclass(frozen=True)
class PrerequisiteResult:
    """Result of prerequisite checks (project mode + section files)."""

    project_mode: str
    section_files: list[Path] = field(default_factory=list)
    total_sections: int = 0


@dataclass(frozen=True)
class TargetingResult:
    """Result of target section selection (vacuum + signal triggers)."""

    target_sections: list[str] = field(default_factory=list)
    target_paths: dict[str, Path] = field(default_factory=dict)
    trigger_reason: str = ""
    vacuum_sections: list[str] = field(default_factory=list)
    trigger_threshold: int = 2



class SubstrateDiscoverer:
    """Stage 3.5 Shared Integration Substrate (SIS) runner.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        task_router: TaskRouterService,
        prompt_guard: PromptGuard,
    ) -> None:
        self._artifact_io = artifact_io
        self._task_router = task_router
        self._state_reader = SubstrateStateReader(artifact_io=artifact_io)
        self._policy = Policy(artifact_io=artifact_io)
        self._schemas = Schemas(artifact_io=artifact_io)
        self._related_files = RelatedFiles(artifact_io=artifact_io)
        self._dispatcher = SubstrateDispatcher(task_router=task_router)
        self._prompt_builder = PromptBuilder(prompt_guard=prompt_guard)

    def _check_prerequisites(
        self,
        artifacts_dir: Path,
        sections_dir: Path,
    ) -> PrerequisiteResult | None:
        """Steps 1-2: read project mode and load section specs."""
        project_mode = self._state_reader.read_project_mode(artifacts_dir)
        if project_mode is None:
            print("[SUBSTRATE] No project-mode signal found -- writing NEEDS_PARENT")
            self._state_reader.write_status(
                artifacts_dir,
                state=BLOCKING_NEEDS_PARENT,
                project_mode="unknown",
                total_sections=0,
                vacuum_sections=[],
                notes="No project-mode signal from scan stage",
            )
            return None

        if not sections_dir.is_dir():
            print(f"[SUBSTRATE] Sections directory not found: {sections_dir}")
            self._state_reader.write_status(
                artifacts_dir,
                state=BLOCKING_NEEDS_PARENT,
                project_mode=project_mode,
                total_sections=0,
                vacuum_sections=[],
                notes=f"Sections directory not found: {sections_dir}",
            )
            return None

        section_files = _list_section_files(sections_dir)
        total_sections = len(section_files)
        if total_sections == 0:
            print("[SUBSTRATE] No section files found")
            self._state_reader.write_status(
                artifacts_dir,
                state=BLOCKING_NEEDS_PARENT,
                project_mode=project_mode,
                total_sections=0,
                vacuum_sections=[],
                notes="No section files found",
            )
            return None

        return PrerequisiteResult(
            project_mode=project_mode,
            section_files=section_files,
            total_sections=total_sections,
        )

    def _find_target_sections(
        self,
        section_files: list[Path],
        artifacts_dir: Path,
        codespace: Path,
        project_mode: str,
        total_sections: int,
    ) -> TargetingResult | None:
        """Steps 3-4: determine vacuum sections, read trigger signals, evaluate trigger rule."""
        vacuum_sections: list[str] = []
        for sf in section_files:
            num = _section_number(sf)
            existing = _count_existing_related(sf, codespace)
            if existing == 0:
                vacuum_sections.append(num)

        signal_triggered: list[str] = self._policy.read_trigger_signals(artifacts_dir)
        trigger_threshold = self._policy.read_trigger_threshold(artifacts_dir)

        combined = list(dict.fromkeys(vacuum_sections + signal_triggered))
        if len(vacuum_sections) >= trigger_threshold or signal_triggered:
            target_sections = combined
            target_paths = {
                _section_number(sf): sf
                for sf in section_files
                if _section_number(sf) in combined
            }
            parts = []
            if vacuum_sections:
                parts.append(f"{len(vacuum_sections)} vacuum section(s)")
            if signal_triggered:
                parts.append(
                    f"{len(signal_triggered)} signal-triggered section(s)"
                )
            trigger_reason = (
                f"{' + '.join(parts)} "
                f"(threshold={trigger_threshold}) -- "
                f"running for {len(target_sections)} sections"
            )
            return TargetingResult(
                target_sections=target_sections,
                target_paths=target_paths,
                trigger_reason=trigger_reason,
                vacuum_sections=vacuum_sections,
                trigger_threshold=trigger_threshold,
            )

        print(
            f"[SUBSTRATE] SKIPPED: {project_mode} project with "
            f"{len(vacuum_sections)} vacuum section(s) "
            f"(threshold={trigger_threshold})"
        )
        self._state_reader.write_status(
            artifacts_dir,
            state="SKIPPED",
            project_mode=project_mode,
            total_sections=total_sections,
            vacuum_sections=vacuum_sections,
            notes=(
                f"{project_mode} project with {len(vacuum_sections)} "
                f"vacuum section(s) -- below threshold of "
                f"{trigger_threshold}"
            ),
            threshold=trigger_threshold,
        )
        return None

    def _run_shard_exploration(
        self,
        target_sections: list[str],
        target_paths: dict[str, Path],
        registry: PathRegistry,
        codespace: Path,
        model_policy: dict,
    ) -> list[str]:
        """Phase A: run shard explorer for each target section.

        Returns the list of section numbers whose shards were valid.
        """
        print(f"[SUBSTRATE] Phase A: Shard exploration ({len(target_sections)} sections)")
        shards_dir = registry.substrate_dir() / "shards"
        shards_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = registry.substrate_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        shard_failures: list[str] = []
        for section_num in target_sections:
            section_path = target_paths[section_num]
            print(f"[SUBSTRATE]   Shard explorer: section-{section_num}")

            prompt_path = self._prompt_builder.write_shard_prompt(
                section_num, section_path, registry.planspace, codespace,
            )
            output_path = logs_dir / f"shard-{section_num}-output.txt"

            ok = self._dispatcher.dispatch_substrate_agent(
                model=model_policy["substrate_shard"],
                prompt_path=prompt_path,
                output_path=output_path,
                codespace=codespace,
                agent_file=self._task_router.agent_for("scan.substrate_shard"),
            )

            # Validate the shard was produced and is well-formed
            shard_path = shards_dir / f"shard-{section_num}.json"
            shard = self._schemas.read_shard_failclosed(shard_path)
            if not ok or shard is None:
                shard_failures.append(section_num)
                print(
                    f"[SUBSTRATE][WARN] Shard explorer failed for "
                    f"section-{section_num}"
                )

        if shard_failures:
            print(
                f"[SUBSTRATE][WARN] {len(shard_failures)} shard(s) failed: "
                f"{', '.join(shard_failures)}"
            )

        # Return valid shard section numbers
        return [s for s in target_sections if s not in shard_failures]

    def _validate_pruner_outputs(
        self,
        registry: PathRegistry,
        artifacts_dir: Path,
        project_mode: str,
        total_sections: int,
        vacuum_sections: list[str],
        trigger_threshold: int,
        pruner_ok: bool,
    ) -> tuple[dict, Path] | None:
        """Validate pruner outputs: seed plan, substrate.md, prune signal.

        Returns ``(seed_plan, substrate_md_path)`` on success, or ``None``
        on failure (writes status on failure).
        """
        substrate_dir = registry.substrate_dir()
        substrate_md_path = substrate_dir / "substrate.md"
        seed_plan_path = substrate_dir / "seed-plan.json"
        prune_signal_path = substrate_dir / "prune-signal.json"

        status_kwargs = dict(
            project_mode=project_mode,
            total_sections=total_sections,
            vacuum_sections=vacuum_sections,
            threshold=trigger_threshold,
        )

        seed_plan = self._schemas.read_seed_plan_failclosed(seed_plan_path)
        if not pruner_ok or seed_plan is None:
            print("[SUBSTRATE] Pruner failed -- aborting")
            self._state_reader.write_status(artifacts_dir, state="RAN",
                          notes="Pruner failed -- no seed plan produced",
                          **status_kwargs)
            return None

        if not substrate_md_path.is_file():
            print("[SUBSTRATE] Pruner did not write substrate.md -- aborting")
            self._state_reader.write_status(artifacts_dir, state="RAN",
                          notes="Pruner completed but substrate.md missing",
                          **status_kwargs)
            return None

        if prune_signal_path.is_file():
            prune_signal = self._artifact_io.read_json(prune_signal_path)
            if isinstance(prune_signal, dict):
                status_val = prune_signal.get("state", "").upper()
                if status_val == BLOCKING_NEEDS_PARENT:
                    reason = prune_signal.get("reason", "no reason given")
                    print(f"[SUBSTRATE] Pruner signalled NEEDS_PARENT: {reason}")
                    self._state_reader.write_status(artifacts_dir, state=BLOCKING_NEEDS_PARENT,
                                  notes=f"Pruner deferred: {reason}",
                                  **status_kwargs)
                    return None
            else:
                print(
                    "[SUBSTRATE][WARN] prune-signal.json malformed "
                    "-- renaming to .malformed.json"
                )

        return seed_plan, substrate_md_path

    def _run_pruning(
        self,
        codespace: Path,
        planspace: Path,
        valid_shards: list[str],
        model_policy: dict,
        project_mode: str,
        total_sections: int,
        vacuum_sections: list[str],
        trigger_threshold: int,
    ) -> tuple[dict, Path] | None:
        """Phase B: run pruner to strategically merge shards.

        Returns ``(seed_plan, substrate_md_path)`` on success, or ``None``
        on failure (writes status on failure).
        """
        registry = PathRegistry(planspace)
        artifacts_dir = registry.artifacts
        print("[SUBSTRATE] Phase B: Pruner (strategic merge)")
        logs_dir = registry.substrate_dir() / "logs"
        pruner_prompt = self._prompt_builder.write_pruner_prompt(
            planspace, codespace, valid_shards,
        )
        pruner_output = logs_dir / "pruner-output.txt"

        pruner_ok = self._dispatcher.dispatch_substrate_agent(
            model=model_policy["substrate_pruner"],
            prompt_path=pruner_prompt,
            output_path=pruner_output,
            codespace=codespace,
            agent_file=self._task_router.agent_for("scan.substrate_prune"),
        )

        return self._validate_pruner_outputs(
            registry, artifacts_dir, project_mode, total_sections,
            vacuum_sections, trigger_threshold, pruner_ok,
        )

    def _run_seeding_and_apply(
        self,
        registry: PathRegistry,
        planspace: Path,
        codespace: Path,
        target_sections: list[str],
        model_policy: dict,
        substrate_md_path: Path,
    ) -> int:
        """Phase C: run seeder, write substrate.ref, apply related-files updates.

        Returns the number of updated section specs.
        """
        # ---- Phase C: Seeding ----
        print("[SUBSTRATE] Phase C: Seeder (anchor creation + wiring)")
        logs_dir = registry.substrate_dir() / "logs"
        seeder_prompt = self._prompt_builder.write_seeder_prompt(planspace, codespace)
        seeder_output = logs_dir / "seeder-output.txt"

        seeder_ok = self._dispatcher.dispatch_substrate_agent(
            model=model_policy["substrate_seeder"],
            prompt_path=seeder_prompt,
            output_path=seeder_output,
            codespace=codespace,
            agent_file=self._task_router.agent_for("scan.substrate_seed"),
        )

        if not seeder_ok:
            print("[SUBSTRATE][WARN] Seeder agent returned non-zero -- "
                  "attempting to apply any signals that were written")

        # Verify seed-signal.json completion marker
        substrate_dir = registry.substrate_dir()
        seed_signal_path = substrate_dir / "seed-signal.json"
        if seed_signal_path.is_file():
            seed_signal = self._artifact_io.read_json(seed_signal_path)
            if isinstance(seed_signal, dict):
                print(
                    f"[SUBSTRATE] Seed signal: "
                    f"{seed_signal.get('state', 'unknown')}"
                )
            else:
                print(
                    "[SUBSTRATE][WARN] seed-signal.json malformed"
                    f" -- renaming to .malformed.json"
                )

        # Write substrate.ref for each target section (input-ref mechanism)
        for section_num in target_sections:
            ref_dir = registry.input_refs_dir(section_num)
            ref_dir.mkdir(parents=True, exist_ok=True)
            ref_path = ref_dir / "substrate.ref"
            ref_path.write_text(
                str(substrate_md_path.resolve()) + "\n", encoding="utf-8",
            )
        print(
            f"[SUBSTRATE] Wrote substrate.ref for "
            f"{len(target_sections)} section(s)"
        )

        # ---- Apply related-files updates ----
        print("[SUBSTRATE] Applying related-files updates")
        updated_count = self._related_files.apply_related_files_updates(planspace)
        print(f"[SUBSTRATE] Updated {updated_count} section spec(s)")

        return updated_count

    def run_substrate_discovery(self, planspace: Path, codespace: Path) -> bool:
        """Run the Stage 3.5 Shared Integration Substrate discovery.

        Pipeline:
          1. Read project mode and section specs.
          2. Determine vacuum sections (related files count == 0).
          3. Apply trigger rule to decide whether to run.
          4. Phase A: Shard explorer per target section.
          5. Phase B: Pruner reads all shards, writes seed plan.
          6. Phase C: Seeder creates anchors, writes related-files signals.
          7. Apply related-files updates to section specs.

        Parameters
        ----------
        planspace:
            Root of the planspace directory containing ``artifacts/``.
        codespace:
            Root of the project source code.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on failure.
        """
        registry = PathRegistry(planspace)
        artifacts_dir = registry.artifacts
        sections_dir = registry.sections_dir()

        # Steps 1-2: Read project mode and load section specs
        prereqs = self._check_prerequisites(artifacts_dir, sections_dir)
        if prereqs is None:
            return False

        # Steps 3-4: Determine targets and evaluate trigger rule
        targeting = self._find_target_sections(
            prereqs.section_files, artifacts_dir, codespace,
            prereqs.project_mode, prereqs.total_sections,
        )
        if targeting is None:
            return True  # Skip is a success -- not an error

        print(f"[SUBSTRATE] Triggered: {targeting.trigger_reason}")

        # Read model policy
        model_policy = self._policy.read_substrate_model_policy(artifacts_dir)

        # Phase A: Shard exploration
        valid_shards = self._run_shard_exploration(
            targeting.target_sections, targeting.target_paths, registry, codespace,
            model_policy,
        )
        if not valid_shards:
            print("[SUBSTRATE] All shards failed -- aborting")
            self._state_reader.write_status(
                artifacts_dir,
                state="RAN",
                project_mode=prereqs.project_mode,
                total_sections=prereqs.total_sections,
                vacuum_sections=targeting.vacuum_sections,
                notes="All shard explorers failed -- no seed plan produced",
                threshold=targeting.trigger_threshold,
            )
            return False

        # Phase B: Pruning
        pruning_result = self._run_pruning(
            codespace, planspace, valid_shards, model_policy,
            prereqs.project_mode, prereqs.total_sections,
            targeting.vacuum_sections, targeting.trigger_threshold,
        )
        if pruning_result is None:
            return False
        seed_plan, substrate_md_path = pruning_result

        # Phase C: Seeding + apply related-files updates
        updated_count = self._run_seeding_and_apply(
            registry, planspace, codespace, targeting.target_sections,
            model_policy, substrate_md_path,
        )

        # Write final status
        self._state_reader.write_status(
            artifacts_dir,
            state="RAN",
            project_mode=prereqs.project_mode,
            total_sections=prereqs.total_sections,
            vacuum_sections=targeting.vacuum_sections,
            notes=(
                f"Completed: {len(valid_shards)} shards, "
                f"{len(seed_plan.get('anchors', []))} anchors, "
                f"{updated_count} sections wired"
            ),
            threshold=targeting.trigger_threshold,
        )

        print("[SUBSTRATE] Done")
        return True


# ---- CLI ----


def _default_discoverer() -> SubstrateDiscoverer:
    from containers import Services
    return SubstrateDiscoverer(
        artifact_io=Services.artifact_io(),
        task_router=Services.task_router(),
        prompt_guard=Services.prompt_guard(),
    )


def run_substrate_discovery(planspace: Path, codespace: Path) -> bool:
    """Run the Stage 3.5 Shared Integration Substrate discovery."""
    return _default_discoverer().run_substrate_discovery(planspace, codespace)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns 0 on success, 1 on failure.

    Usage::

        python -m substrate <planspace> <codespace>
    """
    parser = argparse.ArgumentParser(
        prog="substrate",
        description=(
            "Stage 3.5 Shared Integration Substrate (SIS) discovery. "
            "Discovers shared integration seams across vacuum sections."
        ),
    )
    parser.add_argument(
        "planspace", type=Path,
        help="Planspace directory containing artifacts/.",
    )
    parser.add_argument(
        "codespace", type=Path,
        help="Codespace directory (project source root).",
    )
    args = parser.parse_args(argv)

    planspace: Path = args.planspace.resolve()
    codespace: Path = args.codespace.resolve()

    if not planspace.is_dir():
        print(
            f"[SUBSTRATE][ERROR] Planspace not found: {planspace}",
            file=sys.stderr,
        )
        return 1

    if not codespace.is_dir():
        print(
            f"[SUBSTRATE][ERROR] Codespace not found: {codespace}",
            file=sys.stderr,
        )
        return 1

    ok = run_substrate_discovery(planspace, codespace)
    return 0 if ok else 1
