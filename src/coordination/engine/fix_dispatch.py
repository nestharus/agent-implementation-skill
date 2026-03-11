from pathlib import Path
from typing import Any

from signals.repository.artifact_io import read_json
from dispatch.service.model_policy import resolve
from orchestrator.path_registry import PathRegistry

from dispatch.prompt.template import SRC_TEMPLATE_DIR, TASK_SUBMISSION_SEMANTICS, load_template, render
from dispatch.service.prompt_safety import validate_dynamic_content
from signals.service.communication import _log_artifact, log
from orchestrator.service.context_assembly import materialize_context_sidecar
from taskrouter.agents import resolve_agent_path
from dispatch.engine.section_dispatch import dispatch_agent
from dispatch.helpers.utils import write_model_choice_signal
from flow.service.section_ingestion import ingest_and_submit
from taskrouter import agent_for


def write_coordinator_fix_prompt(
    group: list[dict[str, Any]], planspace: Path, codespace: Path,
    group_id: int,
) -> Path:
    """Write a prompt to fix a group of related problems.

    The prompt lists the grouped problems with section context, the
    affected files, and instructs the agent to fix ALL listed problems
    in a coordinated way. Model selection is policy-driven.
    """
    paths = PathRegistry(planspace)
    artifacts = paths.coordination_dir()
    artifacts.mkdir(parents=True, exist_ok=True)
    prompt_path = artifacts / f"fix-{group_id}-prompt.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    problem_descriptions = []
    for i, p in enumerate(group):
        desc = (
            f"### Problem {i + 1} (Section {p['section']}, "
            f"type: {p['type']})\n"
            f"{p['description']}"
        )
        problem_descriptions.append(desc)
    problems_text = "\n\n".join(problem_descriptions)

    # Collect all unique files across the group
    all_files: list[str] = []
    seen: set[str] = set()
    for p in group:
        for f in p.get("files", []):
            if f not in seen:
                all_files.append(f)
                seen.add(f)

    file_list = "\n".join(f"- `{codespace / f}`" for f in all_files)

    # Collect section specs for context (include both actual spec and excerpts)
    section_nums = sorted({p["section"] for p in group})
    sec_dir = paths.sections_dir()
    section_specs = "\n".join(
        f"- Section {n} specification:"
        f" `{sec_dir / f'section-{n}.md'}`\n"
        f"  - Proposal excerpt:"
        f" `{sec_dir / f'section-{n}-proposal-excerpt.md'}`"
        for n in section_nums
    )
    alignment_specs = "\n".join(
        f"- Section {n} alignment excerpt:"
        f" `{sec_dir / f'section-{n}-alignment-excerpt.md'}`"
        for n in section_nums
    )

    codemap_path = paths.codemap()
    corrections_path = paths.corrections()
    codemap_block = ""
    if codemap_path.exists():
        corrections_line = ""
        if corrections_path.exists():
            corrections_line = (
                f"- Codemap corrections (authoritative fixes): "
                f"`{corrections_path}`\n"
            )
        codemap_block = (
            f"\n## Project Understanding\n"
            f"- Codemap: `{codemap_path}`\n"
            f"{corrections_line}"
            f"\nIf codemap corrections exist, treat them as authoritative "
            f"over codemap.md.\n"
        )

    # Include cross-section tools — prefer digest if available
    tools_block = ""
    tool_digest_path = paths.tool_digest()
    tool_registry_path = paths.tool_registry()
    if tool_digest_path.exists():
        tools_block = (
            f"\n## Available Tools\n"
            f"See tool digest: `{tool_digest_path}`\n"
        )
    elif tool_registry_path.exists():
        reg = read_json(tool_registry_path)
        if reg is not None:
            cross_tools = [
                t for t in (reg if isinstance(reg, list)
                            else reg.get("tools", []))
                if t.get("scope") == "cross-section"
            ]
            if cross_tools:
                tool_lines = "\n".join(
                    f"- `{t.get('path', '?')}` "
                    f"[{t.get('status', 'experimental')}]: "
                    f"{t.get('description', '')}"
                    for t in cross_tools
                )
                tools_block = (
                    f"\n## Available Cross-Section Tools\n{tool_lines}\n"
                )
        else:
            malformed_path = tool_registry_path.with_suffix(".malformed.json")
            if malformed_path.exists() and not tool_registry_path.exists():
                try:
                    import shutil
                    shutil.copy2(malformed_path, tool_registry_path)
                except OSError:
                    pass
            tools_block = (
                f"\n## Tool Registry Warning\n"
                "Tool registry exists but is malformed; "
                f"see `{tool_registry_path}`.\n"
                f"Malformed artifact preserved at "
                f"`{malformed_path}`.\n"
                f"Consider dispatching tool-registrar repair before "
                f"relying on tool context.\n"
            )

    task_submission_path = artifacts / f"signals/task-requests-coord-{group_id}.json"

    template = load_template("coordination/coordinator-fix.md", SRC_TEMPLATE_DIR)
    rendered = render(template, {
        "group_id": str(group_id),
        "problems_text": problems_text,
        "file_list": file_list,
        "section_specs": section_specs,
        "alignment_specs": alignment_specs,
        "codemap_block": codemap_block,
        "tools_block": tools_block,
        "task_submission_path": str(task_submission_path),
        "task_submission_semantics": TASK_SUBMISSION_SEMANTICS,
        "modified_report": str(modified_report),
        "codespace": str(codespace),
    })
    # V3: Validate dynamic content — violations block dispatch
    violations = validate_dynamic_content(rendered)
    if violations:
        log(f"  ERROR: prompt {prompt_path.name} blocked — template "
            f"violations: {violations}")
        return None

    # Materialize sidecar BEFORE writing prompt so it exists at prompt-write time
    sidecar_path = materialize_context_sidecar(
        str(resolve_agent_path("coordination-fixer.md")),
        planspace,
    )

    prompt_path.write_text(rendered, encoding="utf-8")
    # Append context sidecar reference (materialized before rendering)
    if sidecar_path:
        with prompt_path.open("a", encoding="utf-8") as f:
            f.write(
                f"\n## Scoped Context\n"
                f"Agent context sidecar with resolved inputs: "
                f"`{sidecar_path}`\n"
            )
    _log_artifact(planspace, f"prompt:coordinator-fix-{group_id}")
    return prompt_path


def _dispatch_fix_group(
    group: list[dict[str, Any]], group_id: int,
    planspace: Path, codespace: Path, parent: str,
    default_fix_model: str = "",
) -> tuple[int, list[str] | None]:
    """Dispatch an agent to fix a single problem group.

    Returns (group_id, list_of_modified_files) on success.
    Returns (group_id, None) if ALIGNMENT_CHANGED_PENDING sentinel received.

    The ``default_fix_model`` should come from ``policy["coordination_fix"]``
    so that model selection is strictly policy-driven.
    """
    from dispatch.service.model_policy import load_model_policy as read_model_policy

    paths = PathRegistry(planspace)
    artifacts = paths.coordination_dir()
    policy = read_model_policy(planspace)
    fix_prompt = write_coordinator_fix_prompt(
        group, planspace, codespace, group_id,
    )
    if fix_prompt is None:
        log(f"  coordinator: fix group {group_id} prompt blocked "
            f"by template safety — skipping dispatch")
        return group_id, None
    fix_output = artifacts / f"fix-{group_id}-output.md"
    modified_report = artifacts / f"fix-{group_id}-modified.txt"

    # Check for model escalation (triggered by coordination churn)
    if not default_fix_model:
        default_fix_model = resolve(policy, "coordination_fix")
    fix_model = default_fix_model
    coord_escalated_from = None
    escalation_file = artifacts / "model-escalation.txt"
    if escalation_file.exists():
        coord_escalated_from = fix_model
        fix_model = escalation_file.read_text(encoding="utf-8").strip()
        log(f"  coordinator: using escalated model {fix_model}")

    write_model_choice_signal(
        planspace, f"coord-{group_id}", "coordination-fix",
        fix_model,
        "escalated due to coordination churn" if coord_escalated_from
        else "default model",
        coord_escalated_from,
    )

    log(f"  coordinator: dispatching fix for group {group_id} "
        f"({len(group)} problems)")
    result = dispatch_agent(
        fix_model, fix_prompt, fix_output,
        planspace, parent, codespace=codespace,
        agent_file=agent_for("coordination.fix"),
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return group_id, None  # Sentinel — caller must check

    # V6: Submit agent-emitted follow-up work into the queue
    ingest_and_submit(
        planspace,
        db_path=paths.run_db(),
        submitted_by=f"coordination-fix-{group_id}",
        signal_path=artifacts / f"signals/task-requests-coord-{group_id}.json",
        origin_refs=[str(fix_prompt)],
    )

    # Collect modified files from the report (validated to be safe
    # relative paths under codespace — same logic as collect_modified_files)
    codespace_resolved = codespace.resolve()
    modified: list[str] = []
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    log(f"  coordinator: WARNING — fix path outside "
                        f"codespace, skipping: {line}")
                    continue
            else:
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    log(f"  coordinator: WARNING — fix path escapes "
                        f"codespace, skipping: {line}")
                    continue
            modified.append(str(rel))
    return group_id, modified
