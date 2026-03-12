"""Philosophy bootstrap orchestration.

Contains ``ensure_global_philosophy`` and its supporting infrastructure:
constants, path helpers, signal / status writers, context collection,
the bootstrap-prompter sub-workflow, user-input request flow, and the
grounding validator.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from containers import Services
from signals.repository.artifact_io import read_json, write_json
from staleness.helpers.hashing import content_hash
from orchestrator.path_registry import PathRegistry
from signals.service.communication import _log_artifact, log
from taskrouter import agent_for

from intent.service.philosophy_classifier import (
    VALID_SOURCE_TYPES,
    _classify_distiller_result,
    _classify_guidance_result,
    _classify_selector_result,
    _classify_verifier_result,
    _invalid_source_map_detail,
    _manifest_source_mode,
    _user_source_is_substantive,
)
from intent.service.philosophy_catalog import (
    _declared_principle_ids,
    build_philosophy_catalog,
)
from intent.service.philosophy_dispatch import (
    _attempt_output_path,
    _dispatch_classified_signal_stage,
)

# ── constants ─────────────────────────────────────────────────────────

BOOTSTRAP_SIGNAL_NAME = "philosophy-bootstrap-signal.json"
BOOTSTRAP_STATUS_NAME = "philosophy-bootstrap-status.json"
BOOTSTRAP_GUIDANCE_NAME = "philosophy-bootstrap-guidance.json"
BOOTSTRAP_DECISIONS_NAME = "philosophy-bootstrap-decisions.md"
USER_SOURCE_NAME = "philosophy-source-user.md"


# ── time helpers ──────────────────────────────────────────────────────

def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── path helpers ──────────────────────────────────────────────────────

def _bootstrap_signal_path(paths: PathRegistry) -> Path:
    return paths.signals_dir() / BOOTSTRAP_SIGNAL_NAME


def _bootstrap_status_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / BOOTSTRAP_STATUS_NAME


def _bootstrap_diagnostics_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / "philosophy-bootstrap-diagnostics.json"


def _bootstrap_guidance_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / BOOTSTRAP_GUIDANCE_NAME


def _bootstrap_decisions_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / BOOTSTRAP_DECISIONS_NAME


def _user_source_path(paths: PathRegistry) -> Path:
    return paths.intent_global_dir() / USER_SOURCE_NAME


# ── signal / status writers ───────────────────────────────────────────

def _clear_bootstrap_signal(paths: PathRegistry) -> None:
    _bootstrap_signal_path(paths).unlink(missing_ok=True)


def _write_bootstrap_status(
    paths: PathRegistry,
    *,
    bootstrap_state: str,
    blocking_state: str | None,
    source_mode: str,
    detail: str,
) -> None:
    signal_path = _bootstrap_signal_path(paths)
    write_json(_bootstrap_status_path(paths), {
        "bootstrap_state": bootstrap_state,
        "blocking_state": blocking_state,
        "source_mode": source_mode,
        "detail": detail,
        "active_signal": str(signal_path) if signal_path.exists() else None,
        "updated_at": _timestamp_now(),
    })


def _write_bootstrap_signal(
    paths: PathRegistry,
    *,
    state: str,
    detail: str,
    needs: str,
    why_blocked: str,
    extras: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "section": "global",
        "state": state,
        "detail": detail,
        "needs": needs,
        "why_blocked": why_blocked,
    }
    if extras:
        payload.update(extras)
    write_json(_bootstrap_signal_path(paths), payload)


def _bootstrap_result(
    *,
    status: str,
    blocking_state: str | None,
    philosophy_path: Path | None,
    detail: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "blocking_state": blocking_state,
        "philosophy_path": philosophy_path,
        "detail": detail,
    }


def _block_bootstrap(
    paths: PathRegistry,
    *,
    status: str,
    bootstrap_state: str,
    blocking_state: str,
    source_mode: str,
    detail: str,
    needs: str,
    why_blocked: str,
    philosophy_path: Path | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _write_bootstrap_signal(
        paths,
        state=blocking_state,
        detail=detail,
        needs=needs,
        why_blocked=why_blocked,
        extras=extras,
    )
    _write_bootstrap_status(
        paths,
        bootstrap_state=bootstrap_state,
        blocking_state=blocking_state,
        source_mode=source_mode,
        detail=detail,
    )
    return _bootstrap_result(
        status=status,
        blocking_state=blocking_state,
        philosophy_path=philosophy_path,
        detail=detail,
    )


def _write_bootstrap_diagnostics(
    paths: PathRegistry,
    *,
    stage: str,
    attempts: list[dict[str, Any]],
    final_outcome: str,
) -> None:
    write_json(_bootstrap_diagnostics_path(paths), {
        "stage": stage,
        "attempts": attempts,
        "final_outcome": final_outcome,
        "updated_at": _timestamp_now(),
    })


# ── context collection ────────────────────────────────────────────────

def _collect_bootstrap_context_artifacts(
    planspace: Path,
    codespace: Path,
    paths: PathRegistry,
) -> list[tuple[str, Path]]:
    context: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, candidate: Path) -> None:
        if not candidate.exists() or not candidate.is_file():
            return
        resolved = candidate.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        context.append((label, candidate))

    for readme_root, label_prefix in (
        (codespace, "repo_readme"),
        (planspace, "planspace_readme"),
    ):
        for candidate in sorted(readme_root.glob("[Rr][Ee][Aa][Dd][Mm][Ee]*.md"))[:2]:
            add(label_prefix, candidate)

    add("project_mode", paths.project_mode_txt())
    add("strategic_state", paths.strategic_state())
    add("codemap", paths.codemap())

    sections_dir = paths.sections_dir()
    for section_spec in sorted(sections_dir.glob("section-*.md"))[:12]:
        add("section_spec", section_spec)

    proposals_dir = paths.proposals_dir()
    for proposal in sorted(proposals_dir.glob("section-*-integration-proposal.md"))[:6]:
        add("proposal", proposal)

    decisions_dir = paths.decisions_dir()
    for decision in sorted(decisions_dir.glob("*.md"))[:6]:
        add("decision", decision)

    notes_dir = paths.notes_dir()
    for note in sorted(notes_dir.glob("*.md"))[:6]:
        add("note", note)

    return context


# ── bootstrap prompter (optional guidance generation) ─────────────────

def _run_bootstrap_prompter(
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict[str, Any],
    paths: PathRegistry,
) -> dict[str, Any] | None:
    context_artifacts = _collect_bootstrap_context_artifacts(
        planspace,
        codespace,
        paths,
    )
    if not context_artifacts:
        log("Intent bootstrap: no project-shaping artifacts available for "
            "bootstrap guidance — skipping optional prompter")
        return None

    guidance_path = _bootstrap_guidance_path(paths)
    prompt_path = paths.philosophy_bootstrap_guidance_prompt()
    output_path = paths.philosophy_bootstrap_guidance_output()
    artifacts_block = "\n".join(
        f"- `{artifact}` ({label})"
        for label, artifact in context_artifacts
    )
    prompt_text = f"""# Task: Generate Optional Philosophy Bootstrap Guidance

## Context
The repository bootstrap confirmed that no authoritative philosophy
source is currently usable. The user must provide philosophy input in
their own words. Your job is to surface project-shaped tensions that may
help the user articulate that philosophy.

## Available Project-Shaping Artifacts
{artifacts_block}

Read only what you need. Guidance must be shaped by these artifacts,
not by generic software doctrine.

## Output
Write JSON to: `{guidance_path}`

```json
{{
  "project_frame": "Brief summary of the project context relevant to philosophy",
  "prompts": [
    {{
      "prompt": "How should the system handle uncertainty in this project?",
      "why_this_matters": "Project materials suggest risk around acting before certainty."
    }}
  ],
  "notes": [
    "These prompts are optional guidance, not required categories.",
    "Write philosophy in any form — prose, bullets, fragments, examples."
  ]
}}
```

## Rules
- Do NOT decide the philosophy for the user
- Do NOT require a fixed response shape
- Prefer 2-6 prompts that surface likely tensions specific to this project
- Focus on reasoning principles, tradeoffs, authority boundaries, uncertainty handling, escalation, and scope doctrine
- Avoid implementation tactics, framework choices, and feature requirements
- If the artifacts do not support meaningful project-shaped prompts, write an empty `prompts` list and explain the context in `project_frame`
"""
    if not Services.prompt_guard().write_validated(prompt_text, prompt_path):
        log("Intent bootstrap: bootstrap guidance prompt validation failed "
            "— continuing without optional guidance")
        return None
    _log_artifact(planspace, "prompt:philosophy-bootstrap-guidance")

    guidance_path.unlink(missing_ok=True)
    result = Services.dispatcher().dispatch(
        Services.policies().resolve(policy,"intent_philosophy_bootstrap_prompter"),
        prompt_path,
        output_path,
        planspace,
        parent,
        codespace=codespace,
        agent_file=agent_for("intent.philosophy_bootstrap"),
    )
    if result == "ALIGNMENT_CHANGED_PENDING":
        return None

    classification = _classify_guidance_result(guidance_path)
    if classification["state"] == "valid_nonempty":
        return classification["data"]

    log("Intent bootstrap: optional bootstrap guidance produced "
        f"{classification['state']} — continuing without it")
    return None


# ── user-source template and decisions ────────────────────────────────

def _write_user_source_template(paths: PathRegistry) -> Path:
    user_source = _user_source_path(paths)
    if user_source.exists() and user_source.stat().st_size > 0:
        return user_source
    user_source.write_text(
        "# Philosophy Source — User\n\n"
        "Describe in your own words how you want this system to think and decide.\n\n"
        "Freeform prose, bullets, fragments, examples, and anti-patterns are all\n"
        "acceptable. There is no required format.\n\n"
        "## Your Philosophy\n",
        encoding="utf-8",
    )
    return user_source


def _write_bootstrap_decisions(
    paths: PathRegistry,
    *,
    detail: str,
    guidance: dict[str, Any] | None,
    overwrite: bool = True,
) -> Path:
    decisions_path = _bootstrap_decisions_path(paths)
    if not overwrite and decisions_path.exists() and decisions_path.stat().st_size > 0:
        return decisions_path

    user_source = _write_user_source_template(paths)
    lines = [
        "# Philosophy Bootstrap Decisions",
        "",
        detail,
        "",
        "Write your philosophy in your own words at:",
        f"- `{user_source}`",
        "",
        "Freeform input is accepted. Prose, bullets, fragments, examples, and anti-patterns are all valid.",
        "",
        "Focus on reasoning principles that should govern how the system thinks and decides across tasks.",
        "Do not use this file to list frameworks, implementation tactics, or local build steps.",
    ]

    if guidance:
        lines.extend([
            "",
            "## Optional Project-Shaped Prompts",
            "",
            guidance.get("project_frame", ""),
        ])
        for entry in guidance.get("prompts", []):
            if not isinstance(entry, dict):
                continue
            prompt = entry.get("prompt", "").strip()
            why = entry.get("why_this_matters", "").strip()
            if not prompt or not why:
                continue
            lines.append(f"- {prompt}")
            lines.append(f"  Why this matters: {why}")
        notes = [
            note.strip()
            for note in guidance.get("notes", [])
            if isinstance(note, str) and note.strip()
        ]
        if notes:
            lines.extend(["", "## Notes"])
            for note in notes:
                lines.append(f"- {note}")
    else:
        lines.extend([
            "",
            "## Notes",
            "- Optional prompts were unavailable. Write the philosophy directly in whatever form is natural.",
            "- Reasoning principles matter here; frameworks and implementation recipes do not.",
        ])

    decisions_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return decisions_path


def _request_user_philosophy(
    paths: PathRegistry,
    *,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict[str, Any],
    detail: str,
    needs: str,
    why_blocked: str,
    signal_detail: str | None = None,
    source_mode: str = "none",
    extras: dict[str, Any] | None = None,
    overwrite_decisions: bool = True,
) -> dict[str, Any]:
    guidance = _run_bootstrap_prompter(
        planspace,
        codespace,
        parent,
        policy,
        paths,
    )
    user_source = _write_user_source_template(paths)
    decisions_path = _write_bootstrap_decisions(
        paths,
        detail=detail,
        guidance=guidance,
        overwrite=overwrite_decisions,
    )
    merged_extras = dict(extras or {})
    merged_extras.setdefault("decision_path", str(decisions_path))
    merged_extras.setdefault("user_source_path", str(user_source))
    if guidance is not None:
        merged_extras.setdefault(
            "guidance_path",
            str(_bootstrap_guidance_path(paths)),
        )
    return _block_bootstrap(
        paths,
        status="needs_user_input",
        bootstrap_state="needs_user_input",
        blocking_state="NEED_DECISION",
        source_mode=source_mode,
        detail=signal_detail or detail,
        needs=needs,
        why_blocked=why_blocked,
        extras=merged_extras,
    )


# ── grounding validation ──────────────────────────────────────────────

def _grounding_failure_source_mode(
    paths: PathRegistry,
    source_map: dict[str, Any] | None,
) -> str:
    """Infer the correct source_mode for grounding failure metadata."""
    if isinstance(source_map, dict) and source_map:
        source_types = {
            entry.get("source_type")
            for entry in source_map.values()
            if isinstance(entry, dict)
        }
        if source_types == {"user_source"}:
            return "user_source"
        if source_types:
            return "repo_sources"

    status = read_json(_bootstrap_status_path(paths))
    if isinstance(status, dict):
        mode = status.get("source_mode")
        if mode in {"user_source", "repo_sources"}:
            return mode

    return "repo_sources"


def sha256_file(path: Path) -> str:
    """Return hex sha256 of file contents, or empty string on error."""
    from staleness.helpers.hashing import file_hash
    return file_hash(path)


def validate_philosophy_grounding(
    philosophy_path: Path,
    source_map_path: Path,
    artifacts: Path,
) -> bool:
    """Validate that distilled philosophy is grounded in source files."""
    paths = PathRegistry(artifacts.parent)
    detail: str | None = None
    extras: dict[str, Any] | None = None
    failure_source_mode = "repo_sources"

    if not source_map_path.exists() or source_map_path.stat().st_size == 0:
        detail = (
            "Philosophy source map is missing or empty. Distilled philosophy "
            "cannot be verified as grounded. Section execution will be "
            "blocked until philosophy is available."
        )
        extras = {}
    elif source_map_path.exists():
        source_map = read_json(source_map_path)
        if source_map is None:
            log("Intent bootstrap: malformed source map — "
                "preserving as .malformed.json")
            detail = (
                "Philosophy source map is malformed. Section execution will "
                "be blocked until philosophy is available."
            )
            extras = {}
        elif not isinstance(source_map, dict):
            detail = (
                "Philosophy source map is not a JSON object. Section "
                "execution will be blocked until philosophy is available."
            )
            extras = {}
        else:
            failure_source_mode = _grounding_failure_source_mode(
                paths,
                source_map,
            )
            try:
                philosophy_text = philosophy_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return False

            principle_ids = _declared_principle_ids(philosophy_text)
            if not principle_ids:
                return True

            map_keys = set(source_map.keys())
            unmapped = principle_ids - map_keys
            schema_error = _invalid_source_map_detail(source_map)
            if schema_error is not None:
                detail = (
                    "Philosophy source map has invalid entries "
                    f"({schema_error}). Section execution will be blocked "
                    "until philosophy is available."
                )
                extras = {}
            elif unmapped:
                detail = (
                    f"Principle IDs missing from source map: "
                    f"{sorted(unmapped)}. Distilled philosophy may contain "
                    f"invented principles. Section execution will be blocked."
                )
                extras = {
                    "unmapped_principles": sorted(unmapped),
                    "total_principles": len(principle_ids),
                    "mapped_principles": len(principle_ids - unmapped),
                }
            else:
                # Verify that each source_file in the map still exists.
                stale_sources = [
                    entry.get("source_file", "")
                    for entry in source_map.values()
                    if isinstance(entry, dict)
                    and not Path(entry.get("source_file", "")).exists()
                ]
                if stale_sources:
                    detail = (
                        f"Source map references {len(stale_sources)} file(s) "
                        f"that no longer exist on disk: {stale_sources[:5]}. "
                        "Philosophy must be re-distilled from current sources."
                    )
                    extras = {"stale_source_files": stale_sources}
                else:
                    return True

    if detail is not None:
        _write_bootstrap_signal(
            paths,
            state="NEEDS_PARENT",
            detail=detail,
            needs=(
                "Repair the philosophy bootstrap artifacts so each principle "
                "is grounded in a valid source map."
            ),
            why_blocked=(
                "The distilled philosophy cannot be trusted until its source "
                "map is valid and complete."
            ),
            extras=extras,
        )
        _write_bootstrap_status(
            paths,
            bootstrap_state="failed",
            blocking_state="NEEDS_PARENT",
            source_mode=failure_source_mode,
            detail=detail,
        )
        return False
    return False


# ── main orchestration ────────────────────────────────────────────────

def ensure_global_philosophy(
    planspace: Path,
    codespace: Path,
    parent: str,
) -> dict[str, Any]:
    """Ensure the operational philosophy exists; distill if missing."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    intent_global = paths.intent_global_dir()
    intent_global.mkdir(parents=True, exist_ok=True)
    philosophy_path = paths.philosophy()
    user_source = _user_source_path(paths)
    _write_bootstrap_status(
        paths,
        bootstrap_state="discovering",
        blocking_state=None,
        source_mode="none",
        detail="Discovering philosophy sources for bootstrap.",
    )

    if philosophy_path.exists() and philosophy_path.stat().st_size > 0:
        source_map_path = intent_global / "philosophy-source-map.json"
        if not source_map_path.exists():
            log("Intent bootstrap: philosophy exists but source-map "
                "missing — regenerating (fail-closed)")
        else:
            manifest_path = intent_global / "philosophy-source-manifest.json"
            if manifest_path.exists():
                manifest = read_json(manifest_path)
                if isinstance(manifest, dict):
                    sources_changed = False
                    for entry in manifest.get("sources", []):
                        src = Path(entry.get("path", ""))
                        if not src.exists():
                            sources_changed = True
                            break
                        if sha256_file(src) != entry.get("hash", ""):
                            sources_changed = True
                            break

                    catalog_fp_path = (
                        intent_global / "philosophy-catalog-fingerprint.txt"
                    )
                    catalog_changed = False
                    if catalog_fp_path.exists():
                        prev_fp = catalog_fp_path.read_text(
                            encoding="utf-8",
                        ).strip()
                        current_catalog = build_philosophy_catalog(
                            planspace, codespace,
                        )
                        current_fp = content_hash(
                            json.dumps(current_catalog, sort_keys=True),
                        )
                        if prev_fp != current_fp:
                            catalog_changed = True
                            log("Intent bootstrap: philosophy candidate "
                                "catalog changed — rerunning selector")

                    if sources_changed:
                        log("Intent bootstrap: philosophy sources "
                            "changed — regenerating")
                    elif not catalog_changed:
                        _clear_bootstrap_signal(paths)
                        ready_detail = (
                            "Operational philosophy is ready and source "
                            "inputs are unchanged."
                        )
                        source_mode = _manifest_source_mode(manifest)
                        _write_bootstrap_status(
                            paths,
                            bootstrap_state="ready",
                            blocking_state=None,
                            source_mode=source_mode,
                            detail=ready_detail,
                        )
                        return _bootstrap_result(
                            status="ready",
                            blocking_state=None,
                            philosophy_path=philosophy_path,
                            detail=ready_detail,
                        )
                else:
                    log("Intent bootstrap: source manifest malformed — "
                        "regenerating philosophy")
            else:
                _clear_bootstrap_signal(paths)
                ready_detail = "Operational philosophy already exists."
                _write_bootstrap_status(
                    paths,
                    bootstrap_state="ready",
                    blocking_state=None,
                    source_mode="repo_sources",
                    detail=ready_detail,
                )
                return _bootstrap_result(
                    status="ready",
                    blocking_state=None,
                    philosophy_path=philosophy_path,
                    detail=ready_detail,
                )

    source_records: list[dict[str, Any]] | None = None
    source_mode = "none"
    if _user_source_is_substantive(user_source):
        source_records = [{
            "path": str(user_source),
            "reason": "user-provided philosophy bootstrap input",
            "source_type": "user_source",
        }]
        source_mode = "user_source"

    catalog = build_philosophy_catalog(planspace, codespace)
    catalog_path = paths.philosophy_candidate_catalog()
    write_json(catalog_path, catalog)
    if source_records is None and not catalog:
        log("Intent bootstrap: no markdown files found for philosophy "
            "catalog — requesting user bootstrap input")
        return _request_user_philosophy(
            paths,
            planspace=planspace,
            codespace=codespace,
            parent=parent,
            policy=policy,
            source_mode="none",
            detail=(
                "Bootstrap confirmed that the repository contains no "
                "philosophy source material to distill. The user must provide "
                "the initial philosophy input."
            ),
            signal_detail=(
                "No philosophy sources were found in the repository. See "
                "philosophy-bootstrap-decisions.md."
            ),
            needs=(
                "User philosophy input in philosophy-source-user.md so the "
                "distiller has an authorized source."
            ),
            why_blocked=(
                "Bootstrap cannot distill project philosophy without any "
                "candidate source files or user-provided philosophy input."
            ),
        )

    _clear_bootstrap_signal(paths)
    _write_bootstrap_status(
        paths,
        bootstrap_state="discovering",
        blocking_state=None,
        source_mode=source_mode,
        detail=(
            "Using user-provided philosophy bootstrap input."
            if source_mode == "user_source"
            else "Scanning candidate philosophy sources from repository files."
        ),
    )

    selected: dict[str, Any] | None = None
    if source_records is None:
        selector_prompt = paths.philosophy_select_prompt()
        selector_output = paths.philosophy_select_output()
        selected_signal = paths.signals_dir() / "philosophy-selected-sources.json"
        selected_signal.parent.mkdir(parents=True, exist_ok=True)

        selector_prompt_text = f"""# Task: Select Philosophy Source Files

## Context
Select which files from the candidate catalog contain execution
philosophy that should be distilled into the project's operational
philosophy.

Philosophy means cross-cutting reasoning about how the system should
think before it knows what to build: tradeoff rules, uncertainty rules,
escalation rules, authority boundaries, exploration doctrine, scope
doctrine, and durable strategic constraints.

## Input
Read the candidate catalog at: `{catalog_path}`

Each entry includes:
- `path`
- `size_kb`
- `preview_start` (first 15 lines)
- `preview_middle` (excerpt from the middle of the file)
- `headings` (all markdown headings found mechanically)

The previews are starting points only. You MAY read candidate files
directly before deciding. Do not use script-side heuristics; make the
semantic decision yourself from the catalog and any file reads you do.

## Selection Criteria
- Include only files that contain cross-cutting reasoning philosophy
- Exclude feature specs, API or schema docs, local architecture plans,
  framework choices, coding-style notes, checklists, and file-level
  tactics unless a specific section states durable doctrine
- Mixed documents are allowed only when your reason cites the exact
  philosophy-bearing section(s)
- Prefer fewer, higher-quality sources over many marginal ones
- Select 1-10 files maximum

## Output
Write a JSON signal to: `{selected_signal}`

```json
{{
  "status": "selected",
  "sources": [
    {{"path": "...", "reason": "Tradeoffs and Escalation sections define cross-cutting decision rules"}}
  ],
  "ambiguous": [
    {{"path": "...", "reason": "Preview suggests uncertainty-handling doctrine, but exact philosophy-bearing section is unclear"}}
  ],
  "additional_extensions": [".txt", ".rst"]
}}
```

The ``ambiguous`` field is **optional**. Include it only when the
catalog previews are genuinely insufficient to classify a candidate.
All selected sources plus any ambiguous candidates will be sent for
full-read verification. Do not nominate files you can classify from
the catalog and direct file reads.

The ``additional_extensions`` field is **optional**. Include it only
if you believe philosophy sources may exist in non-markdown formats
that were not included in the catalog. The catalog will be rebuilt
with these extensions and you will be re-invoked once.

If NO files contain cross-cutting reasoning philosophy, write:
```json
{{"status": "empty", "sources": []}}
```
"""
        if not Services.prompt_guard().write_validated(selector_prompt_text, selector_prompt):
            return _block_bootstrap(
                paths,
                status="failed",
                bootstrap_state="failed",
                blocking_state="NEEDS_PARENT",
                source_mode="none",
                detail=(
                    "Philosophy source selector prompt could not be validated. "
                    "Section execution will be blocked until bootstrap is repaired."
                ),
                needs="Repair the philosophy bootstrap selector prompt.",
                why_blocked=(
                    "Bootstrap cannot ask the selector agent to identify source "
                    "files until the prompt is valid."
                ),
            )
        _log_artifact(planspace, "prompt:philosophy-select")

        selector_models = [
            Services.policies().resolve(policy,"intent_philosophy_selector"),
            Services.policies().resolve(policy,"intent_philosophy_selector"),
            Services.policies().resolve(policy,"intent_philosophy_selector_escalation"),
        ]
        selector_run = _dispatch_classified_signal_stage(
            stage_name="selector",
            prompt_path=selector_prompt,
            output_path=selector_output,
            signal_path=selected_signal,
            models=selector_models,
            classifier=_classify_selector_result,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            agent_file=agent_for("intent.philosophy_selector"),
        )
        selected_classification = selector_run["classification"]

        if selected_classification["state"] == "valid_nonempty":
            selected = selected_classification["data"]
            _write_bootstrap_diagnostics(
                paths,
                stage="selector",
                attempts=selector_run["attempts"],
                final_outcome="selected",
            )
        elif selected_classification["state"] == "valid_empty":
            log("Intent bootstrap: source selector found no philosophy "
                "files in the repository catalog")
            _write_bootstrap_diagnostics(
                paths,
                stage="selector",
                attempts=selector_run["attempts"],
                final_outcome="need_decision",
            )
            return _request_user_philosophy(
                paths,
                planspace=planspace,
                codespace=codespace,
                parent=parent,
                policy=policy,
                source_mode="none",
                detail=(
                    "Bootstrap confirmed that the repository catalog contains "
                    "no distillable philosophy source set. The user must "
                    "provide the initial philosophy input."
                ),
                signal_detail=(
                    "No repository philosophy source set was found. See "
                    "philosophy-bootstrap-decisions.md."
                ),
                needs=(
                    "User philosophy input in philosophy-source-user.md so the "
                    "distiller has an authorized source."
                ),
                why_blocked=(
                    "The repository inputs genuinely contain no usable "
                    "philosophy source set for distillation."
                ),
            )
        else:
            _write_bootstrap_diagnostics(
                paths,
                stage="selector",
                attempts=selector_run["attempts"],
                final_outcome="needs_parent",
            )
            detail = (
                "Philosophy source selector did not write its required signal "
                "after retry and escalation. Section execution will be blocked "
                "until bootstrap is repaired."
            )
            if selected_classification["state"] == "malformed_signal":
                detail = (
                    "Philosophy source selector wrote a malformed signal after "
                    "retry and escalation. Section execution will be blocked "
                    "until bootstrap is repaired."
                )
            extras: dict[str, Any] = {}
            preserved = selected_classification.get("preserved")
            if preserved:
                extras["preserved_signal"] = preserved
            return _block_bootstrap(
                paths,
                status="failed",
                bootstrap_state="failed",
                blocking_state="NEEDS_PARENT",
                source_mode="none",
                detail=detail,
                needs="Repair the philosophy source selector agent output.",
                why_blocked=(
                    "Bootstrap cannot distinguish agent failure from an empty "
                    "repository until the selector emits a valid signal."
                ),
                extras=extras or None,
            )
    else:
        selected = {"sources": source_records}

    expansion_cap = 5
    if (selected and isinstance(selected.get("additional_extensions"), list)
            and selected["additional_extensions"]):
        raw_exts = selected["additional_extensions"][:expansion_cap]
        extra = frozenset(
            e for e in raw_exts
            if isinstance(e, str) and e.startswith(".")
            and len(e) <= 6 and "/" not in e and "\\" not in e
        )
        if extra:
            expanded_exts = frozenset({".md"}) | extra
            log(f"Intent bootstrap: selector requested extensions "
                f"{sorted(extra)} — rebuilding catalog (one-shot)")
            catalog = build_philosophy_catalog(
                planspace,
                codespace,
                extensions=expanded_exts,
            )
            write_json(catalog_path, catalog)

            expanded_run = _dispatch_classified_signal_stage(
                stage_name="selector-extension-pass",
                prompt_path=selector_prompt,
                output_path=paths.philosophy_select_output_extensions(),
                signal_path=selected_signal,
                models=selector_models,
                classifier=_classify_selector_result,
                planspace=planspace,
                parent=parent,
                codespace=codespace,
                agent_file=agent_for("intent.philosophy_selector"),
            )
            expanded_classification = expanded_run["classification"]
            if expanded_classification["state"] == "valid_nonempty":
                selected = expanded_classification["data"]
            elif expanded_classification["state"] == "valid_empty":
                log("Intent bootstrap: extension pass found no additional "
                    "philosophy sources — keeping original selection")
            else:
                log("Intent bootstrap: extension pass produced "
                    f"{expanded_classification['state']} — keeping original "
                    "selection")

    ambiguous_cap = 5
    shortlisted: list[dict[str, Any]] = []
    if source_mode != "user_source":
        seen_shortlisted: set[str] = set()
        for candidate_group, reason_fallback in (
            (selected.get("sources", []) if isinstance(selected, dict) else [],
             "selector shortlisted source"),
            (selected.get("ambiguous", [])[:ambiguous_cap]
             if isinstance(selected, dict)
             and isinstance(selected.get("ambiguous"), list)
             else [],
             "selector ambiguous candidate"),
        ):
            for entry in candidate_group:
                if not isinstance(entry, dict):
                    continue
                candidate_path = entry.get("path", "")
                if (
                    not isinstance(candidate_path, str)
                    or not Path(candidate_path).exists()
                ):
                    continue
                if candidate_path in seen_shortlisted:
                    continue
                seen_shortlisted.add(candidate_path)
                shortlisted.append({
                    "path": candidate_path,
                    "reason": entry.get("reason", reason_fallback),
                })

    if shortlisted:
        log(f"Intent bootstrap: verifying {len(shortlisted)} shortlisted "
            "philosophy candidate(s) (full-read invariant check)")
        verify_prompt = paths.philosophy_verify_prompt()
        verify_output = paths.philosophy_verify_output()
        verify_signal = (
            paths.signals_dir() / "philosophy-verified-sources.json"
        )
        verify_signal.parent.mkdir(parents=True, exist_ok=True)

        candidates_block = "\n".join(
            f"- `{entry['path']}` — {entry.get('reason', 'shortlisted')}"
            for entry in shortlisted
        )
        verify_prompt_text = f"""# Task: Verify Shortlisted Philosophy Sources

## Context
The source selector shortlisted these files as possible philosophy
sources for a project-wide invariant. Read EACH file in full and
confirm whether it contains execution philosophy.

Philosophy means cross-cutting reasoning about how the system should
think before it knows what to build: tradeoff rules, uncertainty rules,
escalation rules, authority boundaries, exploration doctrine, scope
doctrine, and durable strategic constraints.

## Candidates
{candidates_block}

## Instructions
For each candidate, read the FULL file and classify:
- **philosophy_source**: Contains cross-cutting reasoning philosophy.
  If mixed, cite the exact section(s) that justify inclusion.
- **not_philosophy**: Specification, requirements, architecture plans,
  implementation tactics, or irrelevant content without cross-cutting
  reasoning philosophy.

The verifier is authoritative. Every shortlisted file must be checked,
even if the selector already chose it.

## Output
Write a JSON signal to: `{verify_signal}`

```json
{{{{
  "verified_sources": [
    {{{{"path": "...", "reason": "Tradeoffs section contains cross-cutting reasoning philosophy"}}}}
  ],
  "rejected": [
    {{{{"path": "...", "reason": "Implementation plan only; no cross-cutting reasoning philosophy"}}}}
  ]
}}}}
```
"""
        if not Services.prompt_guard().write_validated(verify_prompt_text, verify_prompt):
            return _block_bootstrap(
                paths,
                status="failed",
                bootstrap_state="failed",
                blocking_state="NEEDS_PARENT",
                source_mode="repo_sources",
                detail=(
                    "Philosophy source verifier prompt could not be validated. "
                    "Section execution will be blocked until bootstrap is repaired."
                ),
                needs="Repair the philosophy verifier prompt.",
                why_blocked=(
                    "Bootstrap cannot confirm shortlisted philosophy sources "
                    "until the verifier prompt is valid."
                ),
            )
        _log_artifact(planspace, "prompt:philosophy-verify")

        verifier_model = Services.policies().resolve(policy,"intent_philosophy_verifier")
        verify_run = _dispatch_classified_signal_stage(
            stage_name="verifier",
            prompt_path=verify_prompt,
            output_path=verify_output,
            signal_path=verify_signal,
            models=[
                verifier_model,
                verifier_model,
                Services.policies().resolve(policy,"intent_philosophy_selector_escalation"),
            ],
            classifier=_classify_verifier_result,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            agent_file=agent_for("intent.philosophy_verifier"),
        )
        verified_classification = verify_run["classification"]
        if verified_classification["state"] == "valid_nonempty":
            verified = verified_classification["data"]
            selected["sources"] = verified["verified_sources"]
            log(f"Intent bootstrap: verifier confirmed "
                f"{len(verified['verified_sources'])} philosophy source(s)")
        elif verified_classification["state"] == "valid_empty":
            log("Intent bootstrap: verifier rejected all shortlisted "
                "philosophy candidates")
            return _request_user_philosophy(
                paths,
                planspace=planspace,
                codespace=codespace,
                parent=parent,
                policy=policy,
                source_mode="none",
                detail=(
                    "Bootstrap confirmed that none of the repository files "
                    "survived full-read philosophy verification. The user "
                    "must provide the initial philosophy input."
                ),
                signal_detail=(
                    "Verified repository candidates contained no philosophy "
                    "source. See philosophy-bootstrap-decisions.md."
                ),
                needs=(
                    "User philosophy input in philosophy-source-user.md so the "
                    "distiller has an authorized source."
                ),
                why_blocked=(
                    "Bootstrap cannot distill a project philosophy when the "
                    "verified shortlist contains no philosophy sources."
                ),
            )
        else:
            extras = {
                "shortlisted_candidates": [
                    entry["path"] for entry in shortlisted
                ],
            }
            preserved = verified_classification.get("preserved")
            if preserved:
                extras["preserved_signal"] = preserved
            return _block_bootstrap(
                paths,
                status="failed",
                bootstrap_state="failed",
                blocking_state="NEEDS_PARENT",
                source_mode="repo_sources",
                detail=(
                    "Philosophy verifier did not emit a valid signal for "
                    "shortlisted sources after retry and escalation. Section "
                    "execution will be blocked until bootstrap is repaired."
                ),
                needs="Repair the philosophy verifier agent output.",
                why_blocked=(
                    "Bootstrap cannot safely confirm the philosophy source set "
                    "until the verifier emits a valid signal."
                ),
                extras=extras,
            )

    if (not isinstance(selected, dict)
            or not isinstance(selected.get("sources"), list)
            or not selected["sources"]):
        log("Intent bootstrap: selector stage ended without a usable "
            "source set — blocking section (fail-closed)")
        return _block_bootstrap(
            paths,
            status="failed",
            bootstrap_state="failed",
            blocking_state="NEEDS_PARENT",
            source_mode="none",
            detail=(
                "Philosophy bootstrap ended selector processing without a "
                "usable source set. Section execution will be blocked until "
                "bootstrap is repaired."
            ),
            needs="Repair the philosophy selector bootstrap flow.",
            why_blocked=(
                "Bootstrap cannot distill philosophy until selector outputs "
                "resolve to a non-empty source set."
            ),
        )

    selected_sources = [
        source for source in selected["sources"]
        if isinstance(source, dict) and Path(source.get("path", "")).exists()
    ]
    sources = [
        {
            "path": Path(source["path"]),
            "source_type": source.get("source_type", "repo_source"),
        }
        for source in selected_sources
    ]
    if not sources:
        log("Intent bootstrap: selected source paths do not exist — "
            "skipping distillation (fail-closed)")
        return _block_bootstrap(
            paths,
            status="failed",
            bootstrap_state="failed",
            blocking_state="NEEDS_PARENT",
            source_mode="none",
            detail=(
                "Philosophy source selector returned source paths that do "
                "not exist. Section execution will be blocked until bootstrap "
                "is repaired."
            ),
            needs="Repair the philosophy source selection output.",
            why_blocked=(
                "Bootstrap cannot distill philosophy from source files that "
                "are not present on disk."
            ),
        )

    log(
        "Intent bootstrap: distilling operational philosophy from "
        f"{len(sources)} "
        f"{'user-provided' if source_mode == 'user_source' else 'selected'} "
        "source(s)",
    )
    _clear_bootstrap_signal(paths)
    _write_bootstrap_status(
        paths,
        bootstrap_state="distilling",
        blocking_state=None,
        source_mode=source_mode if source_mode != "none" else "repo_sources",
        detail=(
            f"Distilling operational philosophy from {len(sources)} "
            f"{'user' if source_mode == 'user_source' else 'repository'} "
            "source file(s)."
        ),
    )

    prompt_path = paths.philosophy_distill_prompt()
    output_path = paths.philosophy_distill_output()
    source_map_path = intent_global / "philosophy-source-map.json"

    decisions_path = _bootstrap_decisions_path(paths)
    sources_block = "\n".join(
        f"- `{source['path']}` (source_type: `{source['source_type']}`)"
        for source in sources
    )
    distill_prompt_text = f"""# Task: Distill Operational Philosophy

## Context
Convert the execution philosophy into an operational philosophy document
that alignment agents can use for per-section philosophy checks.

Philosophy means cross-cutting reasoning about how the system should
think before it knows what to build: tradeoff rules, uncertainty rules,
escalation rules, authority boundaries, exploration doctrine, scope
doctrine, and durable strategic constraints.

## Input
Read these philosophy source files:
{sources_block}

If a philosophy artifact already exists at `{philosophy_path}`, skip this task.

## Output
Write an operational philosophy to: `{philosophy_path}`

Structure:
1. Numbered principles (P1, P2, ...) — short, actionable
2. Interactions between principles (which ones tension with each other)
3. Expansion guidance (how new principles get added)

Write a source map to: `{source_map_path}`
Format: JSON mapping principle ID to an object with `source_type`,
`source_file`, and `source_section`.

If you are reading a user-authored bootstrap source and cannot extract
stable principles because the input is too thin, contradictory, or
genuinely ambiguous, do NOT invent filler. Instead:
- Rewrite `{decisions_path}` with concise follow-up clarification questions
- Leave `{philosophy_path}` empty
- Write `{{}}` to `{source_map_path}`

## Rules
- Extract only durable principles that apply across multiple tasks
- Extract only principles that constrain future decisions
- Extract only principles that are testable in alignment review
- Exclude implementation details unless they express genuine
  cross-cutting doctrine
- Keep principles short and operational (1-2 sentences each)
- Number them P1..PN for machine-stable references
- Note known tensions between principles explicitly
- Include expansion guidance: what classifies as absorbable vs tension vs contradiction
- Do NOT invent principles — every principle must trace to one of the source files
- Use `source_type: "user_source"` for user-authored bootstrap input and `source_type: "repo_source"` for repository files
- Do NOT target a fixed count; a small real seed philosophy is acceptable
- If the sources contain no extractable philosophy, leave
  `{philosophy_path}` empty and write `{{}}` to `{source_map_path}`
"""
    if not Services.prompt_guard().write_validated(distill_prompt_text, prompt_path):
        return _block_bootstrap(
            paths,
            status="failed",
            bootstrap_state="failed",
            blocking_state="NEEDS_PARENT",
            source_mode="repo_sources",
            detail=(
                "Philosophy distillation prompt could not be validated. "
                "Section execution will be blocked until bootstrap is repaired."
            ),
            needs="Repair the philosophy distillation prompt.",
            why_blocked=(
                "Bootstrap cannot distill operational philosophy until the "
                "distiller prompt is valid."
            ),
        )
    _log_artifact(planspace, "prompt:philosophy-distill")

    distiller_model = Services.policies().resolve(policy,"intent_philosophy")
    distill_classification: dict[str, Any] = {"state": "missing_signal", "data": None}
    for attempt in (1, 2):
        result = Services.dispatcher().dispatch(
            distiller_model,
            prompt_path,
            _attempt_output_path(output_path, attempt),
            planspace,
            parent,
            codespace=codespace,
            agent_file=agent_for("intent.philosophy_distiller"),
        )

        if result == "ALIGNMENT_CHANGED_PENDING":
            detail = "Alignment changed while philosophy bootstrap was running."
            return _bootstrap_result(
                status="ready",
                blocking_state=None,
                philosophy_path=philosophy_path,
                detail=detail,
            )

        distill_classification = _classify_distiller_result(
            philosophy_path,
            source_map_path,
        )
        if distill_classification["state"] == "valid_nonempty":
            break
        if attempt < 2:
            log("Intent bootstrap: distiller produced "
                f"{distill_classification['state']} on attempt {attempt}/2 "
                f"— retrying with {distiller_model}")

    if distill_classification["state"] != "valid_nonempty":
        if distill_classification["state"] == "valid_empty":
            if source_mode == "user_source":
                log("Intent bootstrap: user philosophy source needs follow-up "
                    "clarification before principles can be distilled")
                if not decisions_path.exists() or decisions_path.stat().st_size == 0:
                    guidance = None
                    guidance_classification = _classify_guidance_result(
                        _bootstrap_guidance_path(paths),
                    )
                    if guidance_classification["state"] == "valid_nonempty":
                        guidance = guidance_classification["data"]
                    _write_bootstrap_decisions(
                        paths,
                        detail=(
                            "The user-provided philosophy input was not yet "
                            "stable enough to distill into operational principles. "
                            "Please clarify the philosophy directly in the user "
                            "source file."
                        ),
                        guidance=guidance,
                        overwrite=True,
                    )
                return _request_user_philosophy(
                    paths,
                    planspace=planspace,
                    codespace=codespace,
                    parent=parent,
                    policy=policy,
                    source_mode="user_source",
                    detail=(
                        "The user-provided philosophy input is not yet stable "
                        "enough to distill. Please clarify it and resume."
                    ),
                    signal_detail=(
                        "User philosophy input needs clarification. See "
                        "philosophy-bootstrap-decisions.md."
                    ),
                    needs=(
                        "Clarify or expand philosophy-source-user.md so stable "
                        "cross-task reasoning principles can be extracted."
                    ),
                    why_blocked=(
                        "Bootstrap cannot invent filler when user philosophy "
                        "input is thin, contradictory, or ambiguous."
                    ),
                    extras={"sources": [str(source["path"]) for source in sources]},
                    overwrite_decisions=False,
                )
            detail = (
                "Verified philosophy sources contained no extractable "
                "cross-cutting reasoning philosophy. Section execution "
                "will be blocked until philosophy is available."
            )
            log("Intent bootstrap: distiller found no extractable "
                "philosophy in verified sources")
            return _request_user_philosophy(
                paths,
                planspace=planspace,
                codespace=codespace,
                parent=parent,
                policy=policy,
                source_mode="repo_sources",
                detail=(
                    "Bootstrap confirmed that the available repository "
                    "sources still do not contain extractable philosophy. "
                    "The user must provide the initial philosophy input."
                ),
                signal_detail=detail,
                needs=(
                    "Provide philosophy input in philosophy-source-user.md so "
                    "the distiller has an authorized source."
                ),
                why_blocked=(
                    "Bootstrap cannot invent philosophy when the verified "
                    "sources contain only implementation detail."
                ),
                extras={"sources": [str(source["path"]) for source in sources]},
            )
        detail = (
            "Philosophy distiller did not produce the required bootstrap "
            "artifacts despite source files being available. Section "
            "execution will be blocked until philosophy is available."
        )
        if distill_classification["state"] == "malformed_signal":
            detail = (
                "Philosophy distiller produced a malformed source map. "
                "Section execution will be blocked until bootstrap is "
                "repaired."
            )
        log("Intent bootstrap: philosophy distillation failed — "
            f"{distill_classification['state']} (fail-closed, blocking section)")
        extras = {"sources": [str(source["path"]) for source in sources]}
        preserved = distill_classification.get("preserved")
        if preserved:
            extras["preserved_signal"] = preserved
        return _block_bootstrap(
            paths,
            status="failed",
            bootstrap_state="failed",
            blocking_state="NEEDS_PARENT",
            source_mode="repo_sources",
            detail=detail,
            needs="Repair the philosophy distillation step.",
            why_blocked=(
                "Bootstrap cannot establish a global philosophy until the "
                "distiller emits valid grounded artifacts."
            ),
            extras=extras,
        )

    grounding_ok = validate_philosophy_grounding(
        philosophy_path,
        source_map_path,
        paths.artifacts,
    )
    if not grounding_ok:
        log("Intent bootstrap: philosophy grounding validation failed "
            "— blocking section (fail-closed)")
        return _bootstrap_result(
            status="failed",
            blocking_state="NEEDS_PARENT",
            philosophy_path=None,
            detail=(
                "Philosophy grounding validation failed. Section execution "
                "is blocked until bootstrap is repaired."
            ),
        )

    manifest_path = intent_global / "philosophy-source-manifest.json"
    write_json(manifest_path, {
        "sources": [
            {
                "path": str(source["path"]),
                "hash": sha256_file(source["path"]),
                "source_type": source["source_type"],
            }
            for source in sources
        ],
    })

    catalog_fp_path = intent_global / "philosophy-catalog-fingerprint.txt"
    catalog_fp = content_hash(json.dumps(catalog, sort_keys=True))
    catalog_fp_path.write_text(catalog_fp, encoding="utf-8")

    _clear_bootstrap_signal(paths)
    ready_detail = "Operational philosophy distilled and validated."
    _write_bootstrap_status(
        paths,
        bootstrap_state="ready",
        blocking_state=None,
        source_mode=source_mode if source_mode != "none" else "repo_sources",
        detail=ready_detail,
    )
    return _bootstrap_result(
        status="ready",
        blocking_state=None,
        philosophy_path=philosophy_path,
        detail=ready_detail,
    )
