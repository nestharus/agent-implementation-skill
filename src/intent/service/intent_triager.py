"""Intent triage service."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from risk.repository.history import RiskHistory
from risk.types import PostureProfile
from dispatch.types import ALIGNMENT_CHANGED_PENDING

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        LogService,
        ModelPolicyService,
        PromptGuard,
        SignalReader,
        TaskRouterService,
    )

_SUMMARY_SNIPPET_TRUNCATION = 500

_DEFAULT_PROPOSAL_MAX = 5
_DEFAULT_IMPLEMENTATION_MAX = 5
_DEFAULT_EXPANSION_MAX = 2
_DEFAULT_MAX_NEW_SURFACES = 8
_DEFAULT_MAX_NEW_AXES = 6
_DEFAULT_RISK_BUDGET_HINT = 4


class IntentTriager:
    """Intent triage service."""

    def __init__(
        self,
        communicator: Communicator,
        dispatcher: AgentDispatcher,
        logger: LogService,
        policies: ModelPolicyService,
        prompt_guard: PromptGuard,
        signals: SignalReader,
        task_router: TaskRouterService,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._communicator = communicator
        self._dispatcher = dispatcher
        self._logger = logger
        self._policies = policies
        self._prompt_guard = prompt_guard
        self._signals = signals
        self._task_router = task_router
        self._risk_history = RiskHistory(artifact_io=artifact_io)

    def run_intent_triage(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        *,
        related_files_count: int = 0,
        incoming_notes_count: int = 0,
        solve_count: int = 0,
        section_summary: str = "",
    ) -> dict:
        """Dispatch intent-triager (GLM) and return the triage result.

        Returns a dict with at least ``intent_mode`` and ``budgets``.
        Falls back to full on failure.

        Recovery order when the signal file is missing:
        1. Try parsing the agent stdout output for JSON.
        2. If found, backfill the canonical signal file and use it.
        3. If still missing, auto-escalate to a stronger model (one retry).
        4. If escalation also fails, default to full.
        """
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)

        triage_signal_path = paths.intent_triage_signal(section_number)
        triage_prompt_path = paths.intent_triage_prompt(section_number)
        triage_output_path = paths.intent_triage_output(section_number)

        risk_kw = dict(
            related_files_count=related_files_count,
            incoming_notes_count=incoming_notes_count,
            solve_count=solve_count,
        )

        triage_prompt_text = _build_triage_prompt(
            section_number, paths, triage_signal_path,
            related_files_count, incoming_notes_count, solve_count, section_summary,
        )

        if not self._prompt_guard.write_validated(triage_prompt_text, triage_prompt_path):
            return _augment_risk_hints(
                _full_default(section_number), section_number, planspace, self._risk_history, **risk_kw,
            )
        self._communicator.log_artifact(planspace, f"prompt:intent-triage-{section_number}")

        result = self._dispatch_triage(
            policy, triage_prompt_path, triage_output_path,
            planspace, codespace, section_number,
        )

        if result == ALIGNMENT_CHANGED_PENDING:
            return _augment_risk_hints(
                _full_default(section_number), section_number, planspace, self._risk_history, **risk_kw,
            )

        triage = self._signals.read(
            triage_signal_path, expected_fields=["intent_mode"],
        )

        # -- Stdout fallback: try to recover triage from agent output ------
        if triage is None:
            triage = self._recover_from_stdout(
                triage_output_path, triage_signal_path, section_number,
            )

        if triage:
            escalated = self._try_escalation(
                triage, section_number, planspace, codespace,
            )
            if escalated is not None:
                return _augment_risk_hints(
                    escalated, section_number, planspace, self._risk_history, **risk_kw,
                )

            self._logger.log(
                f"Section {section_number}: intent triage → "
                f"{triage.get('intent_mode', 'unknown')}",
            )
            return _augment_risk_hints(
                triage, section_number, planspace, self._risk_history, **risk_kw,
            )

        # -- Auto-escalation: signal still missing after stdout parse ------
        triage = self._auto_escalate(
            section_number, planspace, codespace,
            triage_signal_path, triage_output_path,
        )
        if triage:
            self._logger.log(
                f"Section {section_number}: auto-escalated triage → "
                f"{triage.get('intent_mode', 'unknown')}",
            )
            return _augment_risk_hints(
                triage, section_number, planspace, self._risk_history, **risk_kw,
            )

        self._logger.log(
            f"Section {section_number}: intent triage signal missing or "
            f"malformed — defaulting to full (uncertainty → more strategy)",
        )
        return _augment_risk_hints(
            _full_default(section_number), section_number, planspace, self._risk_history, **risk_kw,
        )

    def _dispatch_triage(
        self,
        policy, triage_prompt_path, triage_output_path,
        planspace, codespace, section_number,
    ):
        return self._dispatcher.dispatch(
            self._policies.resolve(policy, "intent_triage"),
            triage_prompt_path,
            triage_output_path,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("intent.triage"),
        )

    def _try_escalation(
        self,
        triage, section_number, planspace, codespace,
    ):
        if not triage.get("escalate"):
            return None

        paths = PathRegistry(planspace)
        triage_prompt_path = paths.intent_triage_prompt(section_number)
        triage_output_path = paths.intent_triage_output(section_number)
        triage_signal_path = paths.intent_triage_signal(section_number)
        policy = self._policies.load(planspace)
        self._logger.log(
            f"Section {section_number}: triage flagged escalation — "
            f"re-dispatching with stronger model",
        )
        escalation_model = self._policies.resolve(policy, "intent_triage_escalation")
        self._dispatcher.dispatch(
            escalation_model,
            triage_prompt_path,
            triage_output_path,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("intent.triage"),
        )
        escalated = self._signals.read(
            triage_signal_path, expected_fields=["intent_mode"],
        )
        if escalated:
            self._logger.log(
                f"Section {section_number}: escalated triage → "
                f"{escalated.get('intent_mode', 'unknown')}",
            )
            return escalated
        return None

    def _recover_from_stdout(
        self,
        output_path: Path,
        signal_path: Path,
        section_number: str,
    ) -> dict | None:
        """Try to recover the triage signal from agent stdout output.

        Returns the parsed dict (and backfills the signal file) or None.
        """
        parsed = _try_parse_stdout(output_path)
        if parsed is None:
            return None
        self._logger.log(
            f"Section {section_number}: recovered triage from stdout "
            f"output — backfilling signal file",
        )
        _backfill_signal(parsed, signal_path)
        return parsed

    def _auto_escalate(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        triage_signal_path: Path,
        triage_output_path: Path,
    ) -> dict | None:
        """Auto-escalate to a stronger model when both signal and stdout fail."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        triage_prompt_path = paths.intent_triage_prompt(section_number)

        self._logger.log(
            f"Section {section_number}: triage signal and stdout both "
            f"missing — auto-escalating to stronger model",
        )
        escalation_model = self._policies.resolve(policy, "intent_triage_escalation")
        self._dispatcher.dispatch(
            escalation_model,
            triage_prompt_path,
            triage_output_path,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("intent.triage"),
        )

        # Check signal file first
        triage = self._signals.read(
            triage_signal_path, expected_fields=["intent_mode"],
        )
        if triage:
            return triage

        # Stdout fallback on escalation output
        parsed = _try_parse_stdout(triage_output_path)
        if parsed:
            _backfill_signal(parsed, triage_signal_path)
            return parsed

        return None

    def load_triage_result(
        self,
        section_number: str,
        planspace: Path,
    ) -> dict | None:
        """Load a previously-written triage result from signal file."""
        triage_signal_path = PathRegistry(planspace).intent_triage_signal(section_number)
        triage = self._signals.read(
            triage_signal_path,
            expected_fields=["intent_mode"],
        )
        if triage is None:
            return None
        return _augment_risk_hints(triage, section_number, planspace, self._risk_history)


# -- Pure functions (no Services usage) ------------------------------------

_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_RAW_JSON_RE = re.compile(r"\{[^{}]*\"intent_mode\"[^{}]*\}", re.DOTALL)
_TRIAGE_LINE_RE = re.compile(
    r"TRIAGE:\s+\S+\s*→\s+(\w+)",
    re.IGNORECASE,
)


def _try_parse_stdout(output_path: Path) -> dict | None:
    """Try to extract triage JSON from the agent stdout output file.

    Attempts three strategies in order:
    1. Fenced ``json`` code blocks.
    2. Raw JSON containing an ``intent_mode`` key.
    3. ``TRIAGE:`` summary lines (e.g. ``TRIAGE: 06 → full (reason) expansion=0``).

    Returns the parsed dict (with at least ``intent_mode``) or *None*.
    """
    if not output_path.exists():
        return None
    try:
        text = output_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None

    # Strategy 1: fenced ```json blocks
    for match in _FENCED_JSON_RE.finditer(text):
        try:
            candidate = json.loads(match.group(1))
            if isinstance(candidate, dict) and "intent_mode" in candidate:
                return candidate
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 2: raw JSON with intent_mode key
    for match in _RAW_JSON_RE.finditer(text):
        try:
            candidate = json.loads(match.group(0))
            if isinstance(candidate, dict) and "intent_mode" in candidate:
                return candidate
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 3: TRIAGE: summary line
    m = _TRIAGE_LINE_RE.search(text)
    if m:
        mode = m.group(1).strip().lower()
        if mode in {"full", "lightweight", "cached"}:
            return {"intent_mode": mode, "confidence": "medium"}

    return None


def _backfill_signal(parsed: dict, signal_path: Path) -> None:
    """Write *parsed* as canonical JSON to *signal_path*."""
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(
        json.dumps(parsed, indent=2) + "\n",
        encoding="utf-8",
    )


def _gather_triage_refs(paths, section_number):
    triage_refs = []
    for label, path in [
        ("Section spec", paths.section_spec(section_number)),
        ("Proposal excerpt", paths.proposal_excerpt(section_number)),
        ("Alignment excerpt", paths.alignment_excerpt(section_number)),
        ("Problem brief", paths.problem_frame(section_number)),
        ("Codemap summary", paths.codemap()),
        ("Codemap corrections (authoritative)", paths.corrections()),
    ]:
        if path.exists():
            triage_refs.append(f"- {label}: `{path}`")
    return "\n".join(triage_refs) if triage_refs else "- (none)"


def _compose_triage_text(
    section_number: str,
    triage_refs_block: str,
    triage_signal_path,
    related_files_count: int,
    incoming_notes_count: int,
    solve_count: int,
    summary_snippet: str,
) -> str:
    """Return the intent triage prompt text."""
    return f"""# Task: Intent Triage for Section {section_number}

## Context
Decide whether this section needs the full bidirectional intent cycle
(problem + philosophy alignment with surface discovery and expansion)
or lightweight alignment (no fresh intent expansion this cycle; if valid
intent artifacts already exist, alignment may still use intent-judge,
otherwise it falls back to alignment-judge).

## Section Artifacts (read these for grounded assessment)
{triage_refs_block}

## Section Characteristics
- Related files: {related_files_count}
- Incoming cross-section notes: {incoming_notes_count}
- Previous solve attempts: {solve_count}
- Summary: {summary_snippet}

## Decision Factors

Consider these factors when choosing intent mode:

- **Integration breadth**: How many files and modules does this section touch?
- **Cross-section coupling**: Are there incoming notes or dependencies from other sections?
- **Environment uncertainty**: Are there unresolved related files or missing code references?
  Sections with zero related files have more unknowns to resolve than sections with many.
- **Failure history**: Have prior attempts at this section failed?
- **Risk of hidden constraints**: Does the summary suggest architectural complexity?

Weigh these factors heuristically. Sections that are narrow, well-understood,
and have no failure history lean lightweight. Sections with broad integration,
uncertainty, or prior failures lean full.

## Risk Handoff

- `risk_mode`: your assessment of how much ROAL scrutiny this section
  needs based on the section's problem structure, complexity, and
  history.
- `risk_budget_hint`: extra ROAL iteration budget (0 for simple work,
  2-4 for complex or uncertain work).

## Output
Write a JSON signal to: `{triage_signal_path}`

```json
{{
  "section": "{section_number}",
  "intent_mode": "full"|"lightweight"|"cached",
  "confidence": "high"|"medium"|"low",
  "risk_mode": "light"|"full",
  "risk_budget_hint": 0,
  "escalate": false,
  "budgets": {{
    "proposal_max": 5,
    "implementation_max": 5,
    "intent_expansion_max": 2,
    "max_new_surfaces_per_cycle": 8,
    "max_new_axes_total": 6
  }},
  "reason": "<why this mode was chosen>"
}}
```
"""


def _build_triage_prompt(
    section_number, paths, triage_signal_path,
    related_files_count, incoming_notes_count, solve_count, section_summary,
):
    triage_refs_block = _gather_triage_refs(paths, section_number)
    return _compose_triage_text(
        section_number=section_number,
        triage_refs_block=triage_refs_block,
        triage_signal_path=triage_signal_path,
        related_files_count=related_files_count,
        incoming_notes_count=incoming_notes_count,
        solve_count=solve_count,
        summary_snippet=section_summary[:_SUMMARY_SNIPPET_TRUNCATION] if section_summary else "(none)",
    )


def _full_default(section_number: str) -> dict:
    """Default to full mode on triage failure."""
    return {
        "section": section_number,
        "intent_mode": "full",
        "confidence": "low",
        "budgets": {
            "proposal_max": _DEFAULT_PROPOSAL_MAX,
            "implementation_max": _DEFAULT_IMPLEMENTATION_MAX,
            "intent_expansion_max": _DEFAULT_EXPANSION_MAX,
            "max_new_surfaces_per_cycle": _DEFAULT_MAX_NEW_SURFACES,
            "max_new_axes_total": _DEFAULT_MAX_NEW_AXES,
        },
        "reason": "default full (triage unavailable — uncertainty favors strategy)",
        "risk_mode": "full",
        "risk_confidence": "low",
        "risk_budget_hint": _DEFAULT_RISK_BUDGET_HINT,
        "posture_floor": None,
    }


def _augment_risk_hints(
    triage: dict,
    section_number: str,
    planspace: Path,
    risk_history: RiskHistory,
    **_kwargs: object,
) -> dict:
    result = dict(triage)
    confidence = str(result.get("confidence", "low")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    result["confidence"] = confidence
    result.setdefault("risk_mode", "full")
    result.setdefault("risk_budget_hint", 0)
    result.setdefault("risk_confidence", confidence)
    result["posture_floor"] = _derive_posture_floor(section_number, planspace, risk_history)
    return result


def _derive_posture_floor(section_number: str, planspace: Path, risk_history: RiskHistory) -> str | None:
    history = risk_history.read_history(PathRegistry(planspace).risk_history())
    relevant = [
        entry
        for entry in history
        if f"section-{section_number}" in entry.package_id
    ]
    if not relevant:
        return None

    for entry in relevant:
        outcome = entry.actual_outcome.strip().lower()
        verification = (entry.verification_outcome or "").strip().lower()
        if outcome in {"failure", "failed", "blocked", "reopen"}:
            return PostureProfile.P3_GUARDED.value
        if verification in {"failure", "failed", "blocked"}:
            return PostureProfile.P3_GUARDED.value

    if any(
        entry.actual_outcome.strip().lower() in {"mixed", "partial", "warning"}
        for entry in relevant
    ):
        return PostureProfile.P2_STANDARD.value
    return None
