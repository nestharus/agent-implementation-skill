"""Philosophy bootstrap orchestration.

Contains ``ensure_global_philosophy`` and its supporting infrastructure:
constants, path helpers, signal / status writers, context collection,
the bootstrap-prompter sub-workflow, user-input request flow, and the
grounding validator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from coordination.repository.notes import list_all_notes
from orchestrator.path_registry import PathRegistry
from orchestrator.repository.decisions import list_all_decisions_md

from intent.service.philosophy_classifier import (
    SOURCE_MODE_NONE,
    SOURCE_MODE_REPO,
    SOURCE_MODE_SPEC,
    SOURCE_MODE_USER,
    ClassifierState,
    PhilosophyClassifier,
    STATE_VALID_EMPTY,
    STATE_VALID_NONEMPTY,
    _manifest_source_mode,
    _user_source_is_substantive,
)
from intent.service.philosophy_catalog import (
    build_philosophy_catalog,
)
from intent.service.philosophy_grounding import (
    PhilosophyGrounding,
)
from intent.service.philosophy_bootstrap_state import (
    BOOTSTRAP_DISCOVERING,
    BOOTSTRAP_DISTILLING,
    BOOTSTRAP_FAILED,
    BOOTSTRAP_NEEDS_USER_INPUT,
    BOOTSTRAP_READY,
    BootstrapResult,
    PhilosophyBootstrapState,
    bootstrap_decisions_path as _bootstrap_decisions_path,
    bootstrap_guidance_path as _bootstrap_guidance_path,
    bootstrap_result as _bootstrap_result,
    user_source_path as _user_source_path,
)
from intent.service.philosophy_dispatcher import (
    PhilosophyDispatcher,
    _attempt_output_path,
)
from intent.service.philosophy_prompts import (
    compose_bootstrap_guidance_text as _compose_bootstrap_guidance_text,
    compose_distiller_text as _compose_distiller_text,
    compose_source_selector_text as _compose_source_selector_text,
    compose_verify_sources_text as _compose_verify_sources_text,
)
from pipeline.context import DispatchContext
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import BLOCKING_NEEDS_PARENT, BLOCKING_NEED_DECISION, SIGNAL_NEEDS_PARENT, SIGNAL_NEED_DECISION

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        AgentDispatcher,
        Communicator,
        HasherService,
        LogService,
        ModelPolicyService,
        PromptGuard,
        TaskRouterService,
    )

_MAX_SECTION_SPECS = 12
_MAX_PROPOSALS = 6
_MAX_DECISIONS = 6
_MAX_NOTES = 6


def _list_section_specs(sections_dir: Path) -> list[Path]:
    """Named listing helper for section spec files (PAT-0003)."""
    if not sections_dir.is_dir():
        return []
    return sorted(sections_dir.glob("section-*.md"))


def _list_integration_proposals(proposals_dir: Path) -> list[Path]:
    """Named listing helper for integration proposal files (PAT-0003)."""
    if not proposals_dir.is_dir():
        return []
    return sorted(proposals_dir.glob("section-*-integration-proposal.md"))
_MAX_FILE_EXTENSION_LENGTH = 6
_MAX_DISTILLER_ATTEMPTS = 2
_MAX_README_FILES_PER_DIR = 2


# ── context collection (pure — no Services) ──────────────────────────

def _collect_bootstrap_context_artifacts(
    planspace: Path,
    codespace: Path,
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

    paths = PathRegistry(planspace)
    for readme_root, label_prefix in (
        (codespace, "repo_readme"),
        (planspace, "planspace_readme"),
    ):
        for candidate in sorted(readme_root.glob("[Rr][Ee][Aa][Dd][Mm][Ee]*.md"))[:_MAX_README_FILES_PER_DIR]:
            add(label_prefix, candidate)

    add("project_mode", paths.project_mode_txt())
    add("strategic_state", paths.strategic_state())
    add("codemap", paths.codemap())

    sections_dir = paths.sections_dir()
    for section_spec in _list_section_specs(sections_dir)[:_MAX_SECTION_SPECS]:
        add("section_spec", section_spec)

    proposals_dir = paths.proposals_dir()
    for proposal in _list_integration_proposals(proposals_dir)[:_MAX_PROPOSALS]:
        add("proposal", proposal)

    for decision in list_all_decisions_md(paths.decisions_dir())[:_MAX_DECISIONS]:
        add("decision", decision)

    for note in list_all_notes(paths)[:_MAX_NOTES]:
        add("note", note)

    return context


# ── user-source template (pure — no Services) ────────────────────────

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


# ── bootstrap context ─────────────────────────────────────────────────

@dataclass
class _BootstrapContext:
    """Shared state threaded through bootstrap phases."""

    planspace: Path
    codespace: Path
    paths: PathRegistry
    policy: Any
    intent_global: Path
    philosophy_path: Path
    catalog: list[dict[str, Any]] = field(default_factory=list)
    source_mode: str = SOURCE_MODE_NONE
    source_records: list[dict[str, Any]] | None = None
    selected: dict[str, Any] | None = None
    selector_models: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


_EXTENSION_CAP = 5
_AMBIGUOUS_CAP = 5


class PhilosophyBootstrapper:
    """Philosophy bootstrap orchestration."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        bootstrap_state: PhilosophyBootstrapState,
        classifier: PhilosophyClassifier,
        communicator: Communicator,
        dispatcher: AgentDispatcher,
        grounding: PhilosophyGrounding,
        hasher: HasherService,
        logger: LogService,
        philosophy_dispatcher: PhilosophyDispatcher,
        policies: ModelPolicyService,
        prompt_guard: PromptGuard,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._bootstrap_state = bootstrap_state
        self._classifier = classifier
        self._communicator = communicator
        self._dispatcher = dispatcher
        self._grounding = grounding
        self._hasher = hasher
        self._logger = logger
        self._philosophy_dispatcher = philosophy_dispatcher
        self._policies = policies
        self._prompt_guard = prompt_guard
        self._task_router = task_router

    # ── QA / spec-derived helpers ─────────────────────────────────────

    def _is_qa_mode(self, planspace: Path) -> bool:
        """Check whether qa_mode is enabled in parameters.json."""
        params_path = PathRegistry(planspace).parameters()
        if not params_path.exists():
            return False
        data = self._artifact_io.read_json(params_path)
        if not isinstance(data, dict):
            return False
        return bool(data.get("qa_mode"))

    def _resolve_spec_source(self, ctx: _BootstrapContext) -> bool:
        """If a spec file exists in QA mode, set source records to it.

        Returns True if spec source was resolved, False otherwise.
        """
        spec_path = ctx.paths.artifacts / "spec.md"
        if not spec_path.exists() or spec_path.stat().st_size == 0:
            return False
        ctx.source_records = [{
            "path": str(spec_path),
            "reason": "spec-derived philosophy (QA mode — no user source available)",
            "source_type": SOURCE_MODE_USER,
        }]
        ctx.source_mode = SOURCE_MODE_SPEC
        self._logger.log(
            "Intent bootstrap: QA mode — using project spec as "
            "philosophy source (spec-derived)",
        )
        return True

    # ── bootstrap prompter (optional guidance generation) ─────────────

    def _run_bootstrap_prompter(
        self,
        planspace: Path,
        codespace: Path,
    ) -> dict[str, Any] | None:
        paths = PathRegistry(planspace)
        policy = self._policies.load(planspace)
        context_artifacts = _collect_bootstrap_context_artifacts(
            planspace,
            codespace,
        )
        if not context_artifacts:
            self._logger.log("Intent bootstrap: no project-shaping artifacts available for "
                "bootstrap guidance — skipping optional prompter")
            return None

        guidance_path = _bootstrap_guidance_path(paths)
        prompt_path = paths.philosophy_bootstrap_guidance_prompt()
        output_path = paths.philosophy_bootstrap_guidance_output()
        artifacts_block = "\n".join(
            f"- `{artifact}` ({label})"
            for label, artifact in context_artifacts
        )
        prompt_text = _compose_bootstrap_guidance_text(artifacts_block, guidance_path)
        if not self._prompt_guard.write_validated(prompt_text, prompt_path):
            self._logger.log("Intent bootstrap: bootstrap guidance prompt validation failed "
                "— continuing without optional guidance")
            return None
        self._communicator.log_artifact(planspace, "prompt:philosophy-bootstrap-guidance")

        guidance_path.unlink(missing_ok=True)
        result = self._dispatcher.dispatch(
            self._policies.resolve(policy,"intent_philosophy_bootstrap_prompter"),
            prompt_path,
            output_path,
            planspace,
            codespace=codespace,
            agent_file=self._task_router.agent_for("intent.philosophy_bootstrap"),
        )
        if result == ALIGNMENT_CHANGED_PENDING:
            return None

        classification = self._classifier._classify_guidance_result(guidance_path)
        if classification["state"] == STATE_VALID_NONEMPTY:
            return classification["data"]

        self._logger.log("Intent bootstrap: optional bootstrap guidance produced "
            f"{classification['state']} — continuing without it")
        return None

    # ── user-input request ────────────────────────────────────────────

    def _request_user_philosophy(
        self,
        *,
        ctx: DispatchContext,
        detail: str,
        needs: str,
        why_blocked: str,
        signal_detail: str | None = None,
        source_mode: str = SOURCE_MODE_NONE,
        extras: dict[str, Any] | None = None,
        overwrite_decisions: bool = True,
    ) -> dict[str, Any]:
        paths = PathRegistry(ctx.planspace)
        guidance = self._run_bootstrap_prompter(
            ctx.planspace,
            ctx.codespace,
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
        return self._bootstrap_state.block_bootstrap(
            paths,
            bootstrap_state=BOOTSTRAP_NEEDS_USER_INPUT,
            blocking_state=BLOCKING_NEED_DECISION,
            source_mode=source_mode,
            detail=signal_detail or detail,
            needs=needs,
            why_blocked=why_blocked,
            extras=merged_extras,
        )

    # ── bootstrap phases ─────────────────────────────────────────────

    def _check_philosophy_freshness(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Return 'ready' result if philosophy exists and inputs are unchanged."""
        if not ctx.philosophy_path.exists() or ctx.philosophy_path.stat().st_size == 0:
            return None

        source_map_path = ctx.intent_global / "philosophy-source-map.json"
        if not source_map_path.exists():
            self._logger.log("Intent bootstrap: philosophy exists but source-map "
                "missing — regenerating (fail-closed)")
            return None

        manifest_path = ctx.intent_global / "philosophy-source-manifest.json"
        if not manifest_path.exists():
            self._bootstrap_state.clear_bootstrap_signal(ctx.paths)
            ready_detail = "Operational philosophy already exists."
            self._bootstrap_state.write_bootstrap_status(
                ctx.paths,
                bootstrap_state=BOOTSTRAP_READY,
                blocking_state=None,
                source_mode=SOURCE_MODE_REPO,
                detail=ready_detail,
            )
            return _bootstrap_result(
                status=BOOTSTRAP_READY,
                blocking_state=None,
                philosophy_path=ctx.philosophy_path,
                detail=ready_detail,
            )

        manifest = self._artifact_io.read_json(manifest_path)
        if not isinstance(manifest, dict):
            self._logger.log("Intent bootstrap: source manifest malformed — "
                "regenerating philosophy")
            return None

        sources_changed = any(
            not Path(entry.get("path", "")).exists()
            or self._grounding.sha256_file(Path(entry.get("path", ""))) != entry.get("hash", "")
            for entry in manifest.get("sources", [])
        )

        catalog_fp_path = ctx.intent_global / "philosophy-catalog-fingerprint.txt"
        catalog_changed = False
        if catalog_fp_path.exists():
            prev_fp = catalog_fp_path.read_text(encoding="utf-8").strip()
            current_catalog = build_philosophy_catalog(ctx.planspace, ctx.codespace)
            current_fp = self._hasher.content_hash(
                json.dumps(current_catalog, sort_keys=True),
            )
            if prev_fp != current_fp:
                catalog_changed = True
                self._logger.log("Intent bootstrap: philosophy candidate "
                    "catalog changed — rerunning selector")

        if sources_changed:
            self._logger.log("Intent bootstrap: philosophy sources "
                "changed — regenerating")
            return None
        if catalog_changed:
            return None

        self._bootstrap_state.clear_bootstrap_signal(ctx.paths)
        ready_detail = "Operational philosophy is ready and source inputs are unchanged."
        source_mode = _manifest_source_mode(manifest)
        self._bootstrap_state.write_bootstrap_status(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_READY,
            blocking_state=None,
            source_mode=source_mode,
            detail=ready_detail,
        )
        return _bootstrap_result(
            status=BOOTSTRAP_READY,
            blocking_state=None,
            philosophy_path=ctx.philosophy_path,
            detail=ready_detail,
        )

    def _resolve_source_records(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Detect user source or build catalog; set ctx.source_records/mode/catalog."""
        user_source = _user_source_path(ctx.paths)
        if _user_source_is_substantive(user_source):
            ctx.source_records = [{
                "path": str(user_source),
                "reason": "user-provided philosophy bootstrap input",
                "source_type": SOURCE_MODE_USER,
            }]
            ctx.source_mode = SOURCE_MODE_USER

        ctx.catalog = build_philosophy_catalog(ctx.planspace, ctx.codespace)
        catalog_path = ctx.paths.philosophy_candidate_catalog()
        self._artifact_io.write_json(catalog_path, ctx.catalog)

        if ctx.source_records is None and not ctx.catalog:
            # In QA mode, fall back to spec-derived philosophy instead of
            # blocking for user input.
            if self._is_qa_mode(ctx.planspace) and self._resolve_spec_source(ctx):
                pass  # source_records now set — continue to selector/distiller
            else:
                self._logger.log("Intent bootstrap: no markdown files found for philosophy "
                    "catalog — requesting user bootstrap input")
                return self._request_user_philosophy(
                    ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
                    source_mode=SOURCE_MODE_NONE,
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

        self._bootstrap_state.clear_bootstrap_signal(ctx.paths)
        self._bootstrap_state.write_bootstrap_status(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_DISCOVERING,
            blocking_state=None,
            source_mode=ctx.source_mode,
            detail=(
                "Using user-provided philosophy bootstrap input."
                if ctx.source_mode == SOURCE_MODE_USER
                else "Scanning candidate philosophy sources from repository files."
            ),
        )
        return None

    def _handle_selector_empty(
        self, ctx: _BootstrapContext, selector_run: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle selector returning valid-empty (no philosophy files found)."""
        self._logger.log("Intent bootstrap: source selector found no philosophy "
            "files in the repository catalog")
        self._bootstrap_state.write_bootstrap_diagnostics(
            ctx.paths,
            stage="selector",
            attempts=selector_run["attempts"],
            final_outcome=SIGNAL_NEED_DECISION,
        )
        # In QA mode, fall back to spec-derived philosophy instead of
        # blocking for user input.
        if self._is_qa_mode(ctx.planspace) and self._resolve_spec_source(ctx):
            ctx.selected = {"sources": ctx.source_records}
            return None  # continue to distiller
        return self._request_user_philosophy(
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            source_mode=SOURCE_MODE_NONE,
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

    def _handle_selector_failure(
        self,
        ctx: _BootstrapContext,
        selector_run: dict[str, Any],
        selected_classification: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle selector agent failure (malformed or missing signal)."""
        self._bootstrap_state.write_bootstrap_diagnostics(
            ctx.paths,
            stage="selector",
            attempts=selector_run["attempts"],
            final_outcome=SIGNAL_NEEDS_PARENT,
        )
        detail = (
            "Philosophy source selector did not write its required signal "
            "after retry and escalation. Section execution will be blocked "
            "until bootstrap is repaired."
        )
        if selected_classification["state"] == ClassifierState.MALFORMED_SIGNAL:
            detail = (
                "Philosophy source selector wrote a malformed signal after "
                "retry and escalation. Section execution will be blocked "
                "until bootstrap is repaired."
            )
        extras: dict[str, Any] = {}
        preserved = selected_classification.get("preserved")
        if preserved:
            extras["preserved_signal"] = preserved
        return self._bootstrap_state.block_bootstrap(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_FAILED,
            blocking_state=BLOCKING_NEEDS_PARENT,
            source_mode=SOURCE_MODE_NONE,
            detail=detail,
            needs="Repair the philosophy source selector agent output.",
            why_blocked=(
                "Bootstrap cannot distinguish agent failure from an empty "
                "repository until the selector emits a valid signal."
            ),
            extras=extras or None,
        )

    def _run_source_selector(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Dispatch source selector agent or use user-provided records."""
        if ctx.source_records is not None:
            ctx.selected = {"sources": ctx.source_records}
            return None

        catalog_path = ctx.paths.philosophy_candidate_catalog()
        selector_prompt = ctx.paths.philosophy_select_prompt()
        selector_output = ctx.paths.philosophy_select_output()
        selected_signal = ctx.paths.signals_dir() / "philosophy-selected-sources.json"
        selected_signal.parent.mkdir(parents=True, exist_ok=True)

        selector_prompt_text = _compose_source_selector_text(catalog_path, selected_signal)
        if not self._prompt_guard.write_validated(selector_prompt_text, selector_prompt):
            return self._bootstrap_state.block_bootstrap(
                ctx.paths,
                status="failed",
                bootstrap_state=BOOTSTRAP_FAILED,
                blocking_state=BLOCKING_NEEDS_PARENT,
                source_mode=SOURCE_MODE_NONE,
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
        self._communicator.log_artifact(ctx.planspace, "prompt:philosophy-select")

        ctx.selector_models = [
            self._policies.resolve(ctx.policy, "intent_philosophy_selector"),
            self._policies.resolve(ctx.policy, "intent_philosophy_selector"),
            self._policies.resolve(ctx.policy, "intent_philosophy_selector_escalation"),
        ]
        selector_run = self._philosophy_dispatcher._dispatch_classified_signal_stage(
            stage_name="selector",
            prompt_path=selector_prompt,
            output_path=selector_output,
            signal_path=selected_signal,
            models=ctx.selector_models,
            classifier=self._classifier._classify_selector_result,
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            agent_file=self._task_router.agent_for("intent.philosophy_selector"),
        )
        selected_classification = selector_run["classification"]

        if selected_classification["state"] == STATE_VALID_NONEMPTY:
            ctx.selected = selected_classification["data"]
            self._bootstrap_state.write_bootstrap_diagnostics(
                ctx.paths,
                stage="selector",
                attempts=selector_run["attempts"],
                final_outcome="selected",
            )
            return None

        if selected_classification["state"] == STATE_VALID_EMPTY:
            return self._handle_selector_empty(ctx, selector_run)

        return self._handle_selector_failure(ctx, selector_run, selected_classification)

    def _run_extension_pass(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Rebuild catalog with additional extensions if selector requested them."""
        if not (ctx.selected
                and isinstance(ctx.selected.get("additional_extensions"), list)
                and ctx.selected["additional_extensions"]
                and ctx.selector_models):
            return None

        raw_exts = ctx.selected["additional_extensions"][:_EXTENSION_CAP]
        extra = frozenset(
            e for e in raw_exts
            if isinstance(e, str) and e.startswith(".")
            and len(e) <= _MAX_FILE_EXTENSION_LENGTH and "/" not in e and "\\" not in e
        )
        if not extra:
            return None

        expanded_exts = frozenset({".md"}) | extra
        self._logger.log(f"Intent bootstrap: selector requested extensions "
            f"{sorted(extra)} — rebuilding catalog (one-shot)")
        catalog_path = ctx.paths.philosophy_candidate_catalog()
        ctx.catalog = build_philosophy_catalog(
            ctx.planspace, ctx.codespace, extensions=expanded_exts,
        )
        self._artifact_io.write_json(catalog_path, ctx.catalog)

        selected_signal = ctx.paths.signals_dir() / "philosophy-selected-sources.json"
        expanded_run = self._philosophy_dispatcher._dispatch_classified_signal_stage(
            stage_name="selector-extension-pass",
            prompt_path=ctx.paths.philosophy_select_prompt(),
            output_path=ctx.paths.philosophy_select_output_extensions(),
            signal_path=selected_signal,
            models=ctx.selector_models,
            classifier=self._classifier._classify_selector_result,
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            agent_file=self._task_router.agent_for("intent.philosophy_selector"),
        )
        expanded_classification = expanded_run["classification"]
        if expanded_classification["state"] == STATE_VALID_NONEMPTY:
            ctx.selected = expanded_classification["data"]
        elif expanded_classification["state"] == STATE_VALID_EMPTY:
            self._logger.log("Intent bootstrap: extension pass found no additional "
                "philosophy sources — keeping original selection")
        else:
            self._logger.log("Intent bootstrap: extension pass produced "
                f"{expanded_classification['state']} — keeping original "
                "selection")
        return None

    def _handle_verifier_empty(self, ctx: _BootstrapContext) -> dict[str, Any]:
        """Handle verifier rejecting all shortlisted candidates."""
        self._logger.log("Intent bootstrap: verifier rejected all shortlisted "
            "philosophy candidates")
        return self._request_user_philosophy(
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            source_mode=SOURCE_MODE_NONE,
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

    def _handle_verifier_failure(
        self,
        ctx: _BootstrapContext,
        shortlisted: list[dict[str, Any]],
        verified_classification: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle verifier agent failure (malformed or missing signal)."""
        extras: dict[str, Any] = {
            "shortlisted_candidates": [entry["path"] for entry in shortlisted],
        }
        preserved = verified_classification.get("preserved")
        if preserved:
            extras["preserved_signal"] = preserved
        return self._bootstrap_state.block_bootstrap(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_FAILED,
            blocking_state=BLOCKING_NEEDS_PARENT,
            source_mode=SOURCE_MODE_REPO,
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

    def _run_source_verifier(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Build shortlist from selected+ambiguous, dispatch verifier agent."""
        shortlisted = _build_verification_shortlist(ctx)
        if not shortlisted:
            return None

        self._logger.log(f"Intent bootstrap: verifying {len(shortlisted)} shortlisted "
            "philosophy candidate(s) (full-read invariant check)")

        verify_prompt = ctx.paths.philosophy_verify_prompt()
        verify_output = ctx.paths.philosophy_verify_output()
        verify_signal = ctx.paths.signals_dir() / "philosophy-verified-sources.json"
        verify_signal.parent.mkdir(parents=True, exist_ok=True)

        candidates_block = "\n".join(
            f"- `{entry['path']}` — {entry.get('reason', 'shortlisted')}"
            for entry in shortlisted
        )
        verify_prompt_text = _compose_verify_sources_text(candidates_block, verify_signal)
        if not self._prompt_guard.write_validated(verify_prompt_text, verify_prompt):
            return self._bootstrap_state.block_bootstrap(
                ctx.paths,
                status="failed",
                bootstrap_state=BOOTSTRAP_FAILED,
                blocking_state=BLOCKING_NEEDS_PARENT,
                source_mode=SOURCE_MODE_REPO,
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
        self._communicator.log_artifact(ctx.planspace, "prompt:philosophy-verify")

        verifier_model = self._policies.resolve(ctx.policy, "intent_philosophy_verifier")
        verify_run = self._philosophy_dispatcher._dispatch_classified_signal_stage(
            stage_name="verifier",
            prompt_path=verify_prompt,
            output_path=verify_output,
            signal_path=verify_signal,
            models=[
                verifier_model,
                verifier_model,
                self._policies.resolve(ctx.policy, "intent_philosophy_selector_escalation"),
            ],
            classifier=self._classifier._classify_verifier_result,
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            agent_file=self._task_router.agent_for("intent.philosophy_verifier"),
        )
        verified_classification = verify_run["classification"]

        if verified_classification["state"] == STATE_VALID_NONEMPTY:
            verified = verified_classification["data"]
            ctx.selected["sources"] = verified["verified_sources"]
            self._logger.log(f"Intent bootstrap: verifier confirmed "
                f"{len(verified['verified_sources'])} philosophy source(s)")
            return None

        if verified_classification["state"] == STATE_VALID_EMPTY:
            return self._handle_verifier_empty(ctx)

        return self._handle_verifier_failure(ctx, shortlisted, verified_classification)

    def _validate_selected_sources(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Validate selected dict structure and filter to existing paths."""
        if (not isinstance(ctx.selected, dict)
                or not isinstance(ctx.selected.get("sources"), list)
                or not ctx.selected["sources"]):
            self._logger.log("Intent bootstrap: selector stage ended without a usable "
                "source set — blocking section (fail-closed)")
            return self._bootstrap_state.block_bootstrap(
                ctx.paths,
                status="failed",
                bootstrap_state=BOOTSTRAP_FAILED,
                blocking_state=BLOCKING_NEEDS_PARENT,
                source_mode=SOURCE_MODE_NONE,
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
            source for source in ctx.selected["sources"]
            if isinstance(source, dict) and Path(source.get("path", "")).exists()
        ]
        ctx.sources = [
            {
                "path": Path(source["path"]),
                "source_type": source.get("source_type", "repo_source"),
            }
            for source in selected_sources
        ]
        if not ctx.sources:
            self._logger.log("Intent bootstrap: selected source paths do not exist — "
                "skipping distillation (fail-closed)")
            return self._bootstrap_state.block_bootstrap(
                ctx.paths,
                status="failed",
                bootstrap_state=BOOTSTRAP_FAILED,
                blocking_state=BLOCKING_NEEDS_PARENT,
                source_mode=SOURCE_MODE_NONE,
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
        return None

    def _run_distiller(self, ctx: _BootstrapContext) -> dict[str, Any] | None:
        """Dispatch distiller agent and handle classification results."""
        _source_label = {
            SOURCE_MODE_USER: "user-provided",
            SOURCE_MODE_SPEC: "spec-derived",
        }
        source_label = _source_label.get(ctx.source_mode, "selected")
        self._logger.log(
            "Intent bootstrap: distilling operational philosophy from "
            f"{len(ctx.sources)} {source_label} source(s)",
        )
        self._bootstrap_state.clear_bootstrap_signal(ctx.paths)
        self._bootstrap_state.write_bootstrap_status(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_DISTILLING,
            blocking_state=None,
            source_mode=ctx.source_mode if ctx.source_mode != SOURCE_MODE_NONE else SOURCE_MODE_REPO,
            detail=(
                f"Distilling operational philosophy from {len(ctx.sources)} "
                f"{source_label} source file(s)."
            ),
        )

        prompt_path = ctx.paths.philosophy_distill_prompt()
        output_path = ctx.paths.philosophy_distill_output()
        source_map_path = ctx.intent_global / "philosophy-source-map.json"

        distill_prompt_text = _compose_distiller_text(
            sources=ctx.sources,
            philosophy_path=ctx.philosophy_path,
            source_map_path=source_map_path,
            decisions_path=_bootstrap_decisions_path(ctx.paths),
        )
        if not self._prompt_guard.write_validated(distill_prompt_text, prompt_path):
            return self._bootstrap_state.block_bootstrap(
                ctx.paths,
                status="failed",
                bootstrap_state=BOOTSTRAP_FAILED,
                blocking_state=BLOCKING_NEEDS_PARENT,
                source_mode=SOURCE_MODE_REPO,
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
        self._communicator.log_artifact(ctx.planspace, "prompt:philosophy-distill")

        distiller_model = self._policies.resolve(ctx.policy, "intent_philosophy")
        distill_classification: dict[str, Any] = {"state": ClassifierState.MISSING_SIGNAL, "data": None}
        for attempt in range(1, _MAX_DISTILLER_ATTEMPTS + 1):
            result = self._dispatcher.dispatch(
                distiller_model,
                prompt_path,
                _attempt_output_path(output_path, attempt),
                ctx.planspace,
                codespace=ctx.codespace,
                agent_file=self._task_router.agent_for("intent.philosophy_distiller"),
            )

            if result == ALIGNMENT_CHANGED_PENDING:
                return _bootstrap_result(
                    status=BOOTSTRAP_READY,
                    blocking_state=None,
                    philosophy_path=ctx.philosophy_path,
                    detail="Alignment changed while philosophy bootstrap was running.",
                )

            distill_classification = self._classifier._classify_distiller_result(
                ctx.philosophy_path, source_map_path,
            )
            if distill_classification["state"] == STATE_VALID_NONEMPTY:
                break
            if attempt < _MAX_DISTILLER_ATTEMPTS:
                self._logger.log("Intent bootstrap: distiller produced "
                    f"{distill_classification['state']} on attempt "
                    f"{attempt}/{_MAX_DISTILLER_ATTEMPTS} "
                    f"— retrying with {distiller_model}")

        if distill_classification["state"] == STATE_VALID_NONEMPTY:
            return None

        return self._handle_distiller_failure(ctx, distill_classification)

    def _handle_distiller_empty_user_source(
        self, ctx: _BootstrapContext, sources_list: list[str],
    ) -> dict[str, Any]:
        """Handle distiller empty result when source is user-provided."""
        self._logger.log("Intent bootstrap: user philosophy source needs follow-up "
            "clarification before principles can be distilled")
        decisions_path = _bootstrap_decisions_path(ctx.paths)
        if not decisions_path.exists() or decisions_path.stat().st_size == 0:
            guidance = None
            guidance_classification = self._classifier._classify_guidance_result(
                _bootstrap_guidance_path(ctx.paths),
            )
            if guidance_classification["state"] == STATE_VALID_NONEMPTY:
                guidance = guidance_classification["data"]
            _write_bootstrap_decisions(
                ctx.paths,
                detail=(
                    "The user-provided philosophy input was not yet "
                    "stable enough to distill into operational principles. "
                    "Please clarify the philosophy directly in the user "
                    "source file."
                ),
                guidance=guidance,
                overwrite=True,
            )
        return self._request_user_philosophy(
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            source_mode=SOURCE_MODE_USER,
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
            extras={"sources": sources_list},
            overwrite_decisions=False,
        )

    def _handle_distiller_empty_repo_source(
        self, ctx: _BootstrapContext, sources_list: list[str],
    ) -> dict[str, Any]:
        """Handle distiller empty result when source is repository files."""
        self._logger.log("Intent bootstrap: distiller found no extractable "
            "philosophy in verified sources")
        return self._request_user_philosophy(
            ctx=DispatchContext(ctx.planspace, ctx.codespace, _policies=self._policies),
            source_mode=SOURCE_MODE_REPO,
            detail=(
                "Bootstrap confirmed that the available repository "
                "sources still do not contain extractable philosophy. "
                "The user must provide the initial philosophy input."
            ),
            signal_detail=(
                "Verified philosophy sources contained no extractable "
                "cross-cutting reasoning philosophy. Section execution "
                "will be blocked until philosophy is available."
            ),
            needs=(
                "Provide philosophy input in philosophy-source-user.md so "
                "the distiller has an authorized source."
            ),
            why_blocked=(
                "Bootstrap cannot invent philosophy when the verified "
                "sources contain only implementation detail."
            ),
            extras={"sources": sources_list},
        )

    def _handle_distiller_failure(
        self,
        ctx: _BootstrapContext,
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        """Map a failed distiller classification to the appropriate bootstrap result."""
        sources_list = [str(source["path"]) for source in ctx.sources]

        if classification["state"] == STATE_VALID_EMPTY:
            if ctx.source_mode == SOURCE_MODE_USER:
                return self._handle_distiller_empty_user_source(ctx, sources_list)
            return self._handle_distiller_empty_repo_source(ctx, sources_list)

        detail = (
            "Philosophy distiller did not produce the required bootstrap "
            "artifacts despite source files being available. Section "
            "execution will be blocked until philosophy is available."
        )
        if classification["state"] == ClassifierState.MALFORMED_SIGNAL:
            detail = (
                "Philosophy distiller produced a malformed source map. "
                "Section execution will be blocked until bootstrap is "
                "repaired."
            )
        self._logger.log("Intent bootstrap: philosophy distillation failed — "
            f"{classification['state']} (fail-closed, blocking section)")
        extras: dict[str, Any] = {"sources": sources_list}
        preserved = classification.get("preserved")
        if preserved:
            extras["preserved_signal"] = preserved
        return self._bootstrap_state.block_bootstrap(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_FAILED,
            blocking_state=BLOCKING_NEEDS_PARENT,
            source_mode=SOURCE_MODE_REPO,
            detail=detail,
            needs="Repair the philosophy distillation step.",
            why_blocked=(
                "Bootstrap cannot establish a global philosophy until the "
                "distiller emits valid grounded artifacts."
            ),
            extras=extras,
        )

    def _finalize_philosophy(self, ctx: _BootstrapContext) -> dict[str, Any]:
        """Validate grounding, write manifest and fingerprint, return result."""
        source_map_path = ctx.intent_global / "philosophy-source-map.json"
        grounding_ok = self._grounding.validate_philosophy_grounding(
            ctx.philosophy_path, source_map_path, ctx.paths.artifacts,
        )
        if not grounding_ok:
            self._logger.log("Intent bootstrap: philosophy grounding validation failed "
                "— blocking section (fail-closed)")
            return _bootstrap_result(
                status="failed",
                blocking_state=BLOCKING_NEEDS_PARENT,
                philosophy_path=None,
                detail=(
                    "Philosophy grounding validation failed. Section execution "
                    "is blocked until bootstrap is repaired."
                ),
            )

        manifest_path = ctx.intent_global / "philosophy-source-manifest.json"
        self._artifact_io.write_json(manifest_path, {
            "sources": [
                {
                    "path": str(source["path"]),
                    "hash": self._grounding.sha256_file(source["path"]),
                    "source_type": source["source_type"],
                }
                for source in ctx.sources
            ],
        })

        catalog_fp_path = ctx.intent_global / "philosophy-catalog-fingerprint.txt"
        catalog_fp = self._hasher.content_hash(json.dumps(ctx.catalog, sort_keys=True))
        catalog_fp_path.write_text(catalog_fp, encoding="utf-8")

        self._bootstrap_state.clear_bootstrap_signal(ctx.paths)
        ready_detail = "Operational philosophy distilled and validated."
        self._bootstrap_state.write_bootstrap_status(
            ctx.paths,
            bootstrap_state=BOOTSTRAP_READY,
            blocking_state=None,
            source_mode=ctx.source_mode if ctx.source_mode != SOURCE_MODE_NONE else SOURCE_MODE_REPO,
            detail=ready_detail,
        )
        return _bootstrap_result(
            status=BOOTSTRAP_READY,
            blocking_state=None,
            philosophy_path=ctx.philosophy_path,
            detail=ready_detail,
        )

    # ── main orchestration ────────────────────────────────────────────

    def ensure_global_philosophy(
        self,
        planspace: Path,
        codespace: Path,
    ) -> BootstrapResult:
        """Ensure the operational philosophy exists; distill if missing."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        intent_global = paths.intent_global_dir()

        ctx = _BootstrapContext(
            planspace=planspace,
            codespace=codespace,
            paths=paths,
            policy=policy,
            intent_global=intent_global,
            philosophy_path=paths.philosophy(),
        )

        self._bootstrap_state.write_bootstrap_status(
            paths,
            bootstrap_state=BOOTSTRAP_DISCOVERING,
            blocking_state=None,
            source_mode=SOURCE_MODE_NONE,
            detail="Discovering philosophy sources for bootstrap.",
        )

        for phase in (
            self._check_philosophy_freshness,
            self._resolve_source_records,
            self._run_source_selector,
            self._run_extension_pass,
            self._run_source_verifier,
            self._validate_selected_sources,
            self._run_distiller,
        ):
            result = phase(ctx)
            if result is not None:
                return result

        return self._finalize_philosophy(ctx)


# -- Pure helper (no Services) ─────────────────────────────────────────

def _build_verification_shortlist(ctx: _BootstrapContext) -> list[dict[str, Any]]:
    """Build deduplicated shortlist from selected + ambiguous candidates."""
    if ctx.source_mode in (SOURCE_MODE_USER, SOURCE_MODE_SPEC):
        return []
    shortlisted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate_group, reason_fallback in (
        (ctx.selected.get("sources", []) if isinstance(ctx.selected, dict) else [],
         "selector shortlisted source"),
        (ctx.selected.get("ambiguous", [])[:_AMBIGUOUS_CAP]
         if isinstance(ctx.selected, dict)
         and isinstance(ctx.selected.get("ambiguous"), list)
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
            if candidate_path in seen:
                continue
            seen.add(candidate_path)
            shortlisted.append({
                "path": candidate_path,
                "reason": entry.get("reason", reason_fallback),
            })
    return shortlisted

