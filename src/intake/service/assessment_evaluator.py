"""Post-implementation governance assessment helpers."""

from __future__ import annotations

from pathlib import Path

from enum import Enum
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, PromptGuard


class AssessmentVerdict(str, Enum):
    """Post-implementation governance assessment verdict."""

    ACCEPT = "accept"
    ACCEPT_WITH_DEBT = "accept_with_debt"
    REFACTOR_REQUIRED = "refactor_required"

    def __str__(self) -> str:  # noqa: D105
        return self.value


_VALID_VERDICTS = set(AssessmentVerdict)
_DEBT_KEY_HASH_LENGTH = 16


def _compose_assessment_text(
    section_number: str,
    governance_packet: Path,
    trace_index: Path,
    trace_map: Path,
    integration_proposal: Path,
    problem_frame: Path,
    assessment_output: Path,
) -> str:
    """Return the post-implementation assessment prompt text."""
    return f"""# Task: Post-Implementation Governance Assessment for Section {section_number}

## Files to Read
1. Governance packet: `{governance_packet}`
2. Trace index: `{trace_index}`
3. Trace map: `{trace_map}`
4. Integration proposal: `{integration_proposal}`
5. Problem frame: `{problem_frame}`

## Required Output

Write the assessment JSON to:
`{assessment_output}`

## Instructions

Assess the landed implementation for governance-visible risks that were not
fully visible during planning.

Use these lenses:
- Structural coupling/cohesion
- Pattern conformance
- Coherence with neighboring regions
- Security surface
- Scalability
- Operability

Reference governance records by ID when applicable. Only cite problem and
pattern IDs that exist in the governance packet.

Required JSON shape:
```json
{{
  "section": "{section_number}",
  "verdict": "accept | accept_with_debt | refactor_required",
  "lenses": {{
    "coupling": {{"ok": true, "notes": ""}},
    "pattern_conformance": {{"ok": true, "notes": ""}},
    "coherence": {{"ok": true, "notes": ""}},
    "security": {{"ok": true, "notes": ""}},
    "scalability": {{"ok": true, "notes": ""}},
    "operability": {{"ok": true, "notes": ""}}
  }},
  "debt_items": [
    {{
      "category": "coupling|security|scalability|pattern-drift|coherence|operability",
      "region": "affected module or section",
      "description": "what the risk is",
      "severity": "low|medium|high",
      "acceptance_rationale": "why it is acceptable for now",
      "mitigation": "what was done or is planned"
    }}
  ],
  "refactor_reasons": [],
  "problem_ids_addressed": [],
  "pattern_ids_followed": [],
  "profile_id": ""
}}
```

Be conservative. When uncertain, prefer `accept_with_debt` over silent acceptance.
"""


def _debt_key(entry: dict) -> str:
    """Compute a stable key from the material payload of a debt entry.

    Includes identity fields (section, category, region, description) plus
    materiality fields (severity, mitigation, acceptance_rationale, governance
    lineage). A change in any of these triggers re-promotion per PAT-0012.
    """
    import hashlib

    parts = "|".join([
        str(entry.get("section", "")),
        str(entry.get("category", "")),
        str(entry.get("region", "")),
        str(entry.get("description", "")),
        str(entry.get("severity", "")),
        str(entry.get("mitigation", "")),
        str(entry.get("acceptance_rationale", "")),
        ",".join(str(x) for x in entry.get("problem_ids", []) if x),
        ",".join(str(x) for x in entry.get("pattern_ids", []) if x),
        str(entry.get("profile_id", "")),
    ])
    return hashlib.sha256(parts.encode()).hexdigest()[:_DEBT_KEY_HASH_LENGTH]


class AssessmentEvaluator:
    """Evaluates post-implementation governance assessments.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        prompt_guard: PromptGuard,
    ) -> None:
        self._artifact_io = artifact_io
        self._prompt_guard = prompt_guard

    def write_post_impl_assessment_prompt(
        self,
        section_number: str,
        planspace: Path,
    ) -> Path | None:
        """Write the post-implementation assessment prompt."""

        paths = PathRegistry(planspace)
        prompt_path = paths.post_impl_assessment_prompt(section_number)
        governance_packet = paths.governance_packet(section_number)
        trace_index = paths.trace_index(section_number)
        trace_map = paths.trace_map(section_number)
        integration_proposal = paths.proposal(section_number)
        problem_frame = paths.problem_frame(section_number)
        assessment_output = paths.post_impl_assessment(section_number)

        content = _compose_assessment_text(
            section_number=section_number,
            governance_packet=governance_packet,
            trace_index=trace_index,
            trace_map=trace_map,
            integration_proposal=integration_proposal,
            problem_frame=problem_frame,
            assessment_output=assessment_output,
        )

        if not self._prompt_guard.write_validated(content, prompt_path):
            return None
        return prompt_path

    def read_post_impl_assessment(
        self,
        section_number: str,
        planspace: Path,
    ) -> dict | None:
        """Read and validate an assessment result."""
        path = PathRegistry(planspace).post_impl_assessment(section_number)
        data = self._artifact_io.read_json(path)
        if data is None:
            return None
        if not isinstance(data, dict):
            self._artifact_io.rename_malformed(path)
            return None

        verdict = data.get("verdict", "")
        if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
            self._artifact_io.rename_malformed(path)
            return None

        if str(data.get("section", "")).strip() != section_number:
            self._artifact_io.rename_malformed(path)
            return None

        for key in ("problem_ids_addressed", "pattern_ids_followed", "debt_items", "refactor_reasons"):
            value = data.get(key, [])
            if not isinstance(value, list):
                self._artifact_io.rename_malformed(path)
                return None

        profile_id = data.get("profile_id", "")
        if not isinstance(profile_id, str):
            self._artifact_io.rename_malformed(path)
            return None

        lenses = data.get("lenses", {})
        if not isinstance(lenses, dict):
            self._artifact_io.rename_malformed(path)
            return None

        return data

    def _collect_debt_candidates(
        self,
        signals_dir: Path,
    ) -> tuple[list[dict], list[Path]]:
        """Parse risk-register-signal files into debt candidates.

        Returns (candidates, consumed_signal_paths).
        """
        candidates: list[dict] = []
        consumed_signals: list[Path] = []
        for signal_path in sorted(signals_dir.glob("*-risk-register-signal.json")):
            data = self._artifact_io.read_json(signal_path)
            if not isinstance(data, dict):
                continue
            section = data.get("section", "unknown")
            debt_items = data.get("debt_items", [])
            if not isinstance(debt_items, list):
                debt_items = []
            for item in debt_items:
                if not isinstance(item, dict):
                    continue
                candidates.append({
                    "section": section,
                    "category": item.get("category", ""),
                    "region": item.get("region", ""),
                    "description": item.get("description", ""),
                    "severity": item.get("severity", "medium"),
                    "acceptance_rationale": item.get("acceptance_rationale", ""),
                    "mitigation": item.get("mitigation", ""),
                    "source": "post_impl_assessment",
                    "problem_ids": data.get("problem_ids", []),
                    "pattern_ids": data.get("pattern_ids", []),
                    "profile_id": data.get("profile_id", ""),
                })
            consumed_signals.append(signal_path)
        return candidates, consumed_signals

    def promote_debt_signals(self, planspace: Path) -> list[dict]:
        """Consume risk-register-signal files and stage them for register promotion.

        Reads all risk-register-signal-*.json files, extracts typed debt_items,
        deduplicates against existing staging entries, writes a consolidated
        staging artifact, and returns only newly promoted entries.
        """
        import logging

        logger = logging.getLogger(__name__)
        paths = PathRegistry(planspace)
        signals_dir = paths.signals_dir()
        if not signals_dir.exists():
            return []

        candidates, consumed_signals = self._collect_debt_candidates(signals_dir)
        if not candidates:
            return []

        # Deduplicate against existing staging entries
        staging = self._artifact_io.read_json(paths.risk_register_staging())
        existing = staging if isinstance(staging, list) else []
        existing_keys = {_debt_key(entry) for entry in existing if isinstance(entry, dict)}

        new_entries: list[dict] = []
        for candidate in candidates:
            key = _debt_key(candidate)
            if key not in existing_keys:
                candidate["debt_key"] = key
                new_entries.append(candidate)
                existing_keys.add(key)

        if new_entries:
            existing.extend(new_entries)
            self._artifact_io.write_json(paths.risk_register_staging(), existing)
            logger.info("Staged %d new debt entries (skipped %d duplicates)",
                         len(new_entries), len(candidates) - len(new_entries))

        # Record promotion receipts so signals are not re-consumed
        receipts_path = paths.signals_dir() / "debt-promotion-receipts.json"
        receipts = self._artifact_io.read_json(receipts_path)
        receipt_list = receipts if isinstance(receipts, list) else []
        for signal_path in consumed_signals:
            receipt_list.append({
                "signal": signal_path.name,
                "entries_promoted": len([
                    e for e in new_entries
                    if any(signal_path.name.startswith(f"section-{e.get('section', '')}")
                           for _ in [None])
                ]),
            })
        self._artifact_io.write_json(receipts_path, receipt_list)

        return new_entries
