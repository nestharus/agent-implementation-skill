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
from pathlib import Path

from orchestrator.path_registry import PathRegistry
from scan.substrate.substrate_dispatcher import dispatch_substrate_agent as _dispatch_agent
from scan.substrate.substrate_state_reader import (
    count_existing_related as _count_existing_related,
    list_section_files as _list_section_files,
    read_project_mode as _read_project_mode,
    section_number as _section_number,
    write_status as _write_status,
)
from scan.substrate.policy import (
    read_substrate_model_policy as _read_model_policy,
    read_trigger_signals as _read_trigger_signals,
    read_trigger_threshold as _read_trigger_threshold,
)

from scan.substrate.prompt_builder import (
    write_pruner_prompt,
    write_seeder_prompt,
    write_shard_prompt,
)
from scan.substrate.related_files import apply_related_files_updates
from scan.substrate.schemas import read_seed_plan_failclosed, read_shard_failclosed
from containers import Services
from signals.types import BLOCKING_NEEDS_PARENT


# ---- Helpers ----


def _check_prerequisites(
    registry: PathRegistry,
    artifacts_dir: Path,
    sections_dir: Path,
) -> tuple[str, list[Path], int] | None:
    """Steps 1-2: read project mode and load section specs.

    Returns ``(project_mode, section_files, total_sections)`` on success,
    or ``None`` after writing a NEEDS_PARENT status on failure.
    """
    # ---- Step 1: Read project mode ----
    project_mode = _read_project_mode(artifacts_dir)
    if project_mode is None:
        print("[SUBSTRATE] No project-mode signal found -- writing NEEDS_PARENT")
        _write_status(
            artifacts_dir,
            state=BLOCKING_NEEDS_PARENT,
            project_mode="unknown",
            total_sections=0,
            vacuum_sections=[],
            notes="No project-mode signal from scan stage",
        )
        return None

    # ---- Step 2: Load section specs ----
    if not sections_dir.is_dir():
        print(f"[SUBSTRATE] Sections directory not found: {sections_dir}")
        _write_status(
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
        _write_status(
            artifacts_dir,
            state=BLOCKING_NEEDS_PARENT,
            project_mode=project_mode,
            total_sections=0,
            vacuum_sections=[],
            notes="No section files found",
        )
        return None

    return project_mode, section_files, total_sections


def _find_target_sections(
    section_files: list[Path],
    artifacts_dir: Path,
    codespace: Path,
    project_mode: str,
    total_sections: int,
) -> tuple[list[str], dict[str, Path], str, list[str], int] | None:
    """Steps 3-4: determine vacuum sections, read trigger signals, evaluate trigger rule.

    Returns ``(target_sections, target_paths, trigger_reason,
    vacuum_sections, trigger_threshold)`` on success, or ``None`` if
    the trigger rule says to skip (after writing status and printing).
    """
    # ---- Step 3: Determine vacuum sections ----
    vacuum_sections: list[str] = []
    for sf in section_files:
        num = _section_number(sf)
        existing = _count_existing_related(sf, codespace)
        if existing == 0:
            vacuum_sections.append(num)

    # V6/R68: Collect signal-driven trigger requests. Sections can
    # request SIS via a trigger signal even when they have related
    # files (e.g. friction signals from failed integration attempts).
    signal_triggered: list[str] = _read_trigger_signals(artifacts_dir)

    # ---- Step 4: Apply trigger rule ----
    trigger_threshold = _read_trigger_threshold(artifacts_dir)

    # Structural evidence drives SIS: vacuum sections + signal triggers.
    # project_mode is telemetry only — not a routing key.
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
        return (
            target_sections,
            target_paths,
            trigger_reason,
            vacuum_sections,
            trigger_threshold,
        )

    # Not enough vacuum sections and no signals -- skip
    print(
        f"[SUBSTRATE] SKIPPED: {project_mode} project with "
        f"{len(vacuum_sections)} vacuum section(s) "
        f"(threshold={trigger_threshold})"
    )
    _write_status(
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
    target_sections: list[str],
    target_paths: dict[str, Path],
    registry: PathRegistry,
    codespace: Path,
    planspace: Path,
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

        prompt_path = write_shard_prompt(
            section_num, section_path, planspace, codespace,
        )
        output_path = logs_dir / f"shard-{section_num}-output.txt"

        ok = _dispatch_agent(
            model=model_policy["substrate_shard"],
            prompt_path=prompt_path,
            output_path=output_path,
            codespace=codespace,
            agent_file=Services.task_router().agent_for("scan.substrate_shard"),
        )

        # Validate the shard was produced and is well-formed
        shard_path = shards_dir / f"shard-{section_num}.json"
        shard = read_shard_failclosed(shard_path)
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

    seed_plan = read_seed_plan_failclosed(seed_plan_path)
    if not pruner_ok or seed_plan is None:
        print("[SUBSTRATE] Pruner failed -- aborting")
        _write_status(artifacts_dir, state="RAN",
                      notes="Pruner failed -- no seed plan produced",
                      **status_kwargs)
        return None

    if not substrate_md_path.is_file():
        print("[SUBSTRATE] Pruner did not write substrate.md -- aborting")
        _write_status(artifacts_dir, state="RAN",
                      notes="Pruner completed but substrate.md missing",
                      **status_kwargs)
        return None

    if prune_signal_path.is_file():
        prune_signal = Services.artifact_io().read_json(prune_signal_path)
        if isinstance(prune_signal, dict):
            status_val = prune_signal.get("state", "").upper()
            if status_val == BLOCKING_NEEDS_PARENT:
                reason = prune_signal.get("reason", "no reason given")
                print(f"[SUBSTRATE] Pruner signalled NEEDS_PARENT: {reason}")
                _write_status(artifacts_dir, state=BLOCKING_NEEDS_PARENT,
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
    registry: PathRegistry,
    codespace: Path,
    planspace: Path,
    valid_shards: list[str],
    model_policy: dict,
    artifacts_dir: Path,
    project_mode: str,
    total_sections: int,
    vacuum_sections: list[str],
    trigger_threshold: int,
) -> tuple[dict, Path] | None:
    """Phase B: run pruner to strategically merge shards.

    Returns ``(seed_plan, substrate_md_path)`` on success, or ``None``
    on failure (writes status on failure).
    """
    print("[SUBSTRATE] Phase B: Pruner (strategic merge)")
    logs_dir = registry.substrate_dir() / "logs"
    pruner_prompt = write_pruner_prompt(
        planspace, codespace, valid_shards,
    )
    pruner_output = logs_dir / "pruner-output.txt"

    pruner_ok = _dispatch_agent(
        model=model_policy["substrate_pruner"],
        prompt_path=pruner_prompt,
        output_path=pruner_output,
        codespace=codespace,
        agent_file=Services.task_router().agent_for("scan.substrate_prune"),
    )

    return _validate_pruner_outputs(
        registry, artifacts_dir, project_mode, total_sections,
        vacuum_sections, trigger_threshold, pruner_ok,
    )


def _run_seeding_and_apply(
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
    seeder_prompt = write_seeder_prompt(planspace, codespace)
    seeder_output = logs_dir / "seeder-output.txt"

    seeder_ok = _dispatch_agent(
        model=model_policy["substrate_seeder"],
        prompt_path=seeder_prompt,
        output_path=seeder_output,
        codespace=codespace,
        agent_file=Services.task_router().agent_for("scan.substrate_seed"),
    )

    if not seeder_ok:
        print("[SUBSTRATE][WARN] Seeder agent returned non-zero -- "
              "attempting to apply any signals that were written")

    # Verify seed-signal.json completion marker
    substrate_dir = registry.substrate_dir()
    seed_signal_path = substrate_dir / "seed-signal.json"
    if seed_signal_path.is_file():
        seed_signal = Services.artifact_io().read_json(seed_signal_path)
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
    updated_count = apply_related_files_updates(planspace, codespace)
    print(f"[SUBSTRATE] Updated {updated_count} section spec(s)")

    return updated_count


# ---- Main orchestration ----

def run_substrate_discovery(planspace: Path, codespace: Path) -> bool:
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
    prereqs = _check_prerequisites(registry, artifacts_dir, sections_dir)
    if prereqs is None:
        return False
    project_mode, section_files, total_sections = prereqs

    # Steps 3-4: Determine targets and evaluate trigger rule
    targeting = _find_target_sections(
        section_files, artifacts_dir, codespace, project_mode, total_sections,
    )
    if targeting is None:
        return True  # Skip is a success -- not an error

    target_sections, target_paths, trigger_reason, vacuum_sections, trigger_threshold = targeting
    print(f"[SUBSTRATE] Triggered: {trigger_reason}")

    # Read model policy
    model_policy = _read_model_policy(artifacts_dir)

    # Phase A: Shard exploration
    valid_shards = _run_shard_exploration(
        target_sections, target_paths, registry, codespace, planspace,
        model_policy,
    )
    if not valid_shards:
        print("[SUBSTRATE] All shards failed -- aborting")
        _write_status(
            artifacts_dir,
            state="RAN",
            project_mode=project_mode,
            total_sections=total_sections,
            vacuum_sections=vacuum_sections,
            notes="All shard explorers failed -- no seed plan produced",
            threshold=trigger_threshold,
        )
        return False

    # Phase B: Pruning
    pruning_result = _run_pruning(
        registry, codespace, planspace, valid_shards, model_policy,
        artifacts_dir, project_mode, total_sections, vacuum_sections,
        trigger_threshold,
    )
    if pruning_result is None:
        return False
    seed_plan, substrate_md_path = pruning_result

    # Phase C: Seeding + apply related-files updates
    updated_count = _run_seeding_and_apply(
        registry, planspace, codespace, target_sections, model_policy,
        substrate_md_path,
    )

    # Write final status
    _write_status(
        artifacts_dir,
        state="RAN",
        project_mode=project_mode,
        total_sections=total_sections,
        vacuum_sections=vacuum_sections,
        notes=(
            f"Completed: {len(valid_shards)} shards, "
            f"{len(seed_plan.get('anchors', []))} anchors, "
            f"{updated_count} sections wired"
        ),
        threshold=trigger_threshold,
    )

    print("[SUBSTRATE] Done")
    return True


# ---- CLI ----

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
