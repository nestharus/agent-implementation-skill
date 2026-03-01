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
import json
import re
import subprocess
import sys
from pathlib import Path

from scan.related_files import extract_related_files

from .prompts import write_pruner_prompt, write_seeder_prompt, write_shard_prompt
from .related_files import apply_related_files_updates
from .schemas import read_seed_plan_failclosed, read_shard_failclosed

# WORKFLOW_HOME: scripts/substrate -> scripts -> src
WORKFLOW_HOME = Path(__file__).resolve().parent.parent.parent

# ---- Default model assignments ----

_DEFAULT_MODELS: dict[str, str] = {
    "substrate_shard": "gpt-codex-high",
    "substrate_pruner": "gpt-codex-xhigh",
    "substrate_seeder": "gpt-codex-high",
}


# ---- Model policy ----

_DEFAULT_TRIGGER_THRESHOLD = 2


def _read_model_policy(artifacts_dir: Path) -> dict[str, str]:
    """Read substrate model assignments from ``model-policy.json``.

    Looks for a ``"substrate_shard"``, ``"substrate_pruner"``, and
    ``"substrate_seeder"`` keys at the top level of the policy file.
    Falls back to defaults when the file is missing, malformed, or
    lacks the relevant keys.

    Returns a dict mapping task name -> model string.
    """
    policy = dict(_DEFAULT_MODELS)
    policy_path = artifacts_dir / "model-policy.json"
    if policy_path.is_file():
        try:
            data = json.loads(policy_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in _DEFAULT_MODELS:
                    if key in data and isinstance(data[key], str):
                        policy[key] = data[key]
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[SUBSTRATE][WARN] model-policy.json exists but is "
                f"invalid ({exc}) -- renaming to .malformed.json"
            )
            try:
                policy_path.rename(
                    policy_path.with_suffix(".malformed.json"))
            except OSError:
                pass
    return policy


def _read_trigger_signals(artifacts_dir: Path) -> list[str]:
    """Read signal-driven SIS trigger requests.

    Sections can request SIS via
    ``artifacts/signals/substrate-trigger-<NN>.json`` with at least
    ``{"section": "NN"}``. Returns a list of section number strings
    that requested SIS regardless of vacuum status.
    """
    signals_dir = artifacts_dir / "signals"
    if not signals_dir.is_dir():
        return []
    triggered: list[str] = []
    for p in sorted(signals_dir.iterdir()):
        if not p.name.startswith("substrate-trigger-") or not p.name.endswith(".json"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "section" in data:
                triggered.append(str(data["section"]))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[SUBSTRATE][WARN] {p.name} malformed ({exc}) "
                f"-- renaming to .malformed.json"
            )
            try:
                p.rename(p.with_suffix(".malformed.json"))
            except OSError:
                pass
    return triggered


def _read_trigger_threshold(artifacts_dir: Path) -> int:
    """Read the vacuum section threshold from policy config.

    Checks ``model-policy.json`` for ``substrate_trigger_min_vacuum_sections``.
    Falls back to ``_DEFAULT_TRIGGER_THRESHOLD``.
    """
    policy_path = artifacts_dir / "model-policy.json"
    if policy_path.is_file():
        try:
            data = json.loads(policy_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                val = data.get("substrate_trigger_min_vacuum_sections")
                if isinstance(val, int) and val >= 1:
                    return val
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[SUBSTRATE][WARN] model-policy.json malformed while "
                f"reading trigger threshold ({exc}) -- renaming to "
                f".malformed.json"
            )
            try:
                policy_path.rename(
                    policy_path.with_suffix(".malformed.json"))
            except OSError:
                pass
    return _DEFAULT_TRIGGER_THRESHOLD


# ---- Agent dispatch ----

def _dispatch_agent(
    model: str,
    prompt_path: Path,
    output_path: Path,
    codespace: Path | None = None,
    *,
    agent_file: str,
) -> bool:
    """Run an agent via ``uv run --frozen agents`` and capture output.

    Parameters
    ----------
    model:
        Model name (e.g. ``"gpt-codex-high"``).
    prompt_path:
        ``--file`` path containing the agent prompt.
    output_path:
        Path to write combined stdout+stderr.
    codespace:
        If given, passed as ``--project`` so the agent runs with the
        correct working directory.
    agent_file:
        REQUIRED basename of the agent definition file (e.g.
        ``"substrate-shard-explorer.md"``).  Every dispatch must have
        behavioral constraints.

    Returns
    -------
    bool
        ``True`` if the agent exited with return code 0.
    """
    if not agent_file:
        raise ValueError(
            "agent_file is required — every dispatch must have "
            "behavioral constraints"
        )
    agent_path = WORKFLOW_HOME / "agents" / agent_file
    if not agent_path.exists():
        raise FileNotFoundError(f"Agent file not found: {agent_path}")

    cmd = [
        "uv", "run", "--frozen", "agents",
        "--model", model,
        "--file", str(prompt_path),
        "--agent-file", str(agent_path),
    ]
    if codespace:
        cmd.extend(["--project", str(codespace)])

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output_path.write_text(
            result.stdout + result.stderr, encoding="utf-8",
        )
        if result.returncode != 0:
            print(
                f"[SUBSTRATE][WARN] Agent returned "
                f"{result.returncode} for {prompt_path.name}"
            )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        output_path.write_text(
            "TIMEOUT: Agent exceeded 600s time limit\n",
            encoding="utf-8",
        )
        print(
            f"[SUBSTRATE][WARN] Agent timed out for {prompt_path.name}"
        )
        return False


# ---- Project mode ----

def _read_project_mode(artifacts_dir: Path) -> str | None:
    """Read project mode from scan-stage signals.

    Checks ``artifacts/signals/project-mode.json`` first, then falls
    back to ``artifacts/project-mode.txt``.

    Returns one of ``"greenfield"``, ``"brownfield"``, ``"hybrid"``,
    or ``None`` if no mode signal exists.
    """
    json_path = artifacts_dir / "signals" / "project-mode.json"
    txt_path = artifacts_dir / "project-mode.txt"

    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            mode = data.get("mode", "").strip().lower()
            if mode in ("greenfield", "brownfield", "hybrid"):
                return mode
        except (json.JSONDecodeError, OSError) as exc:
            try:
                json_path.rename(json_path.with_suffix(".malformed.json"))
            except OSError:
                pass  # Best-effort preserve
            print(
                f"[SUBSTRATE][WARN] project-mode.json malformed "
                f"({exc}) -- preserved as .malformed.json, "
                f"trying text fallback"
            )

    if txt_path.is_file():
        mode = txt_path.read_text(encoding="utf-8").strip().lower()
        if mode in ("greenfield", "brownfield", "hybrid"):
            return mode

    return None


# ---- Section analysis ----

def _list_section_files(sections_dir: Path) -> list[Path]:
    """Return sorted list of ``section-N.md`` files."""
    files = [
        f
        for f in sections_dir.iterdir()
        if f.is_file()
        and re.match(r"section-\d+\.md$", f.name)
    ]
    return sorted(files)


def _section_number(path: Path) -> str:
    """Extract section number string from a section filename.

    ``section-03.md`` -> ``"03"``.
    """
    match = re.match(r"section-(\d+)\.md$", path.name)
    if match:
        return match.group(1)
    return path.stem.replace("section-", "")


def _count_existing_related(
    section_path: Path,
    codespace: Path,
) -> int:
    """Count how many related files in a section spec actually exist.

    Reads the ``## Related Files`` block, extracts ``### <path>``
    entries, and checks each against the codespace.
    """
    text = section_path.read_text(encoding="utf-8")
    related = extract_related_files(text)
    count = 0
    for rel_path in related:
        candidate = codespace / rel_path
        if candidate.exists():
            count += 1
    return count


# ---- Status writing ----

def _write_status(
    artifacts_dir: Path,
    state: str,
    project_mode: str,
    total_sections: int,
    vacuum_sections: list[str],
    notes: str,
    threshold: int = _DEFAULT_TRIGGER_THRESHOLD,
) -> None:
    """Write ``artifacts/substrate/status.json``."""
    status_dir = artifacts_dir / "substrate"
    status_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "state": state,
        "project_mode": project_mode,
        "total_sections": total_sections,
        "vacuum_sections": [int(s) for s in vacuum_sections],
        "threshold": threshold,
        "notes": notes,
    }
    (status_dir / "status.json").write_text(
        json.dumps(status, indent=2) + "\n", encoding="utf-8",
    )


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
    artifacts_dir = planspace / "artifacts"
    sections_dir = artifacts_dir / "sections"

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

    if project_mode == "greenfield":
        # Greenfield: run for ALL sections
        target_sections = [_section_number(sf) for sf in section_files]
        target_paths = {_section_number(sf): sf for sf in section_files}
        trigger_reason = (
            f"greenfield project -- running for all "
            f"{total_sections} sections"
        )
    elif len(vacuum_sections) >= trigger_threshold or signal_triggered:
        # Brownfield/hybrid with enough vacuum sections or signal-driven
        combined = list(
            dict.fromkeys(vacuum_sections + signal_triggered))
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
    shards_dir = artifacts_dir / "substrate" / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = artifacts_dir / "substrate" / "logs"
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

    substrate_dir = artifacts_dir / "substrate"
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
        try:
            prune_signal = json.loads(
                prune_signal_path.read_text(encoding="utf-8")
            )
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
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[SUBSTRATE][WARN] prune-signal.json malformed "
                f"({exc}) -- renaming to .malformed.json"
            )
            try:
                prune_signal_path.rename(
                    prune_signal_path.with_suffix(".malformed.json"))
            except OSError:
                pass

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
        try:
            seed_signal = json.loads(
                seed_signal_path.read_text(encoding="utf-8")
            )
            if isinstance(seed_signal, dict):
                print(
                    f"[SUBSTRATE] Seed signal: "
                    f"{seed_signal.get('state', 'unknown')}"
                )
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[SUBSTRATE][WARN] seed-signal.json malformed ({exc})"
                f" -- renaming to .malformed.json"
            )
            try:
                seed_signal_path.rename(
                    seed_signal_path.with_suffix(".malformed.json"))
            except OSError:
                pass

    # Write substrate.ref for each target section (input-ref mechanism)
    for section_num in target_sections:
        ref_dir = artifacts_dir / "inputs" / f"section-{section_num}"
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
