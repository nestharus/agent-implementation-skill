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

from signals.repository.artifact_io import read_json
from orchestrator.path_registry import PathRegistry
from scan.substrate.dispatch import dispatch_substrate_agent as _dispatch_agent
from scan.substrate.helpers import (
    count_existing_related as _count_existing_related,
    list_section_files as _list_section_files,
    read_project_mode as _read_project_mode,
    section_number as _section_number,
    write_status as _write_status,
)
from scan.substrate.policy import (
    DEFAULT_TRIGGER_THRESHOLD as _DEFAULT_TRIGGER_THRESHOLD,
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

# WORKFLOW_HOME: scan/ -> src/
WORKFLOW_HOME = Path(__file__).resolve().parent.parent.parent

def _registry_for_artifacts(artifacts_dir: Path) -> PathRegistry:
    return PathRegistry(artifacts_dir.parent)


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

    # ---- Step 1: Read project mode ----
    project_mode = _read_project_mode(artifacts_dir)
    if project_mode is None:
        print("[SUBSTRATE] No project-mode signal found -- writing NEEDS_PARENT")
        _write_status(
            artifacts_dir,
            state="NEEDS_PARENT",
            project_mode="unknown",
            total_sections=0,
            vacuum_sections=[],
            notes="No project-mode signal from scan stage",
        )
        return False

    # ---- Step 2: Load section specs ----
    if not sections_dir.is_dir():
        print(f"[SUBSTRATE] Sections directory not found: {sections_dir}")
        _write_status(
            artifacts_dir,
            state="NEEDS_PARENT",
            project_mode=project_mode,
            total_sections=0,
            vacuum_sections=[],
            notes=f"Sections directory not found: {sections_dir}",
        )
        return False

    section_files = _list_section_files(sections_dir)
    total_sections = len(section_files)
    if total_sections == 0:
        print("[SUBSTRATE] No section files found")
        _write_status(
            artifacts_dir,
            state="NEEDS_PARENT",
            project_mode=project_mode,
            total_sections=0,
            vacuum_sections=[],
            notes="No section files found",
        )
        return False

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
    else:
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
        return True  # Skip is a success -- not an error

    print(f"[SUBSTRATE] Triggered: {trigger_reason}")

    # ---- Read model policy ----
    model_policy = _read_model_policy(artifacts_dir)

    # ---- Phase A: Shard exploration ----
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
            agent_file="substrate-shard-explorer.md",
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

    # Check we have at least one valid shard to proceed
    valid_shards = [
        s for s in target_sections if s not in shard_failures
    ]
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

    # ---- Phase B: Pruning ----
    print("[SUBSTRATE] Phase B: Pruner (strategic merge)")
    pruner_prompt = write_pruner_prompt(
        planspace, codespace, valid_shards,
    )
    pruner_output = logs_dir / "pruner-output.txt"

    pruner_ok = _dispatch_agent(
        model=model_policy["substrate_pruner"],
        prompt_path=pruner_prompt,
        output_path=pruner_output,
        codespace=codespace,
        agent_file="substrate-pruner.md",
    )

    substrate_dir = registry.substrate_dir()
    substrate_md_path = substrate_dir / "substrate.md"
    seed_plan_path = substrate_dir / "seed-plan.json"
    prune_signal_path = substrate_dir / "prune-signal.json"

    seed_plan = read_seed_plan_failclosed(seed_plan_path)
    if not pruner_ok or seed_plan is None:
        print("[SUBSTRATE] Pruner failed -- aborting")
        _write_status(
            artifacts_dir,
            state="RAN",
            project_mode=project_mode,
            total_sections=total_sections,
            vacuum_sections=vacuum_sections,
            notes="Pruner failed -- no seed plan produced",
            threshold=trigger_threshold,
        )
        return False

    # Verify substrate.md was written
    if not substrate_md_path.is_file():
        print("[SUBSTRATE] Pruner did not write substrate.md -- aborting")
        _write_status(
            artifacts_dir,
            state="RAN",
            project_mode=project_mode,
            total_sections=total_sections,
            vacuum_sections=vacuum_sections,
            notes="Pruner completed but substrate.md missing",
            threshold=trigger_threshold,
        )
        return False

    # Check prune-signal.json for NEEDS_PARENT
    if prune_signal_path.is_file():
        prune_signal = read_json(prune_signal_path)
        if isinstance(prune_signal, dict):
            status_val = prune_signal.get("state", "").upper()
            if status_val == "NEEDS_PARENT":
                reason = prune_signal.get("reason", "no reason given")
                print(
                    f"[SUBSTRATE] Pruner signalled NEEDS_PARENT: "
                    f"{reason}"
                )
                _write_status(
                    artifacts_dir,
                    state="NEEDS_PARENT",
                    project_mode=project_mode,
                    total_sections=total_sections,
                    vacuum_sections=vacuum_sections,
                    notes=f"Pruner deferred: {reason}",
                    threshold=trigger_threshold,
                )
                return False
        else:
            print(
                f"[SUBSTRATE][WARN] prune-signal.json malformed "
                "-- renaming to .malformed.json"
            )

    # ---- Phase C: Seeding ----
    print("[SUBSTRATE] Phase C: Seeder (anchor creation + wiring)")
    seeder_prompt = write_seeder_prompt(planspace, codespace)
    seeder_output = logs_dir / "seeder-output.txt"

    seeder_ok = _dispatch_agent(
        model=model_policy["substrate_seeder"],
        prompt_path=seeder_prompt,
        output_path=seeder_output,
        codespace=codespace,
        agent_file="substrate-seeder.md",
    )

    if not seeder_ok:
        print("[SUBSTRATE][WARN] Seeder agent returned non-zero -- "
              "attempting to apply any signals that were written")

    # Verify seed-signal.json completion marker
    seed_signal_path = substrate_dir / "seed-signal.json"
    if seed_signal_path.is_file():
        seed_signal = read_json(seed_signal_path)
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

    # ---- Write final status ----
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
