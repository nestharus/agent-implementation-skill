"""Post-implementation governance assessment helpers."""

from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import read_json, rename_malformed
from lib.core.path_registry import PathRegistry
from prompt_safety import write_validated_prompt
from section_loop.section_engine.traceability import update_trace_governance

_VALID_VERDICTS = {"accept", "accept_with_debt", "refactor_required"}


def write_post_impl_assessment_prompt(
    section_number: str,
    planspace: Path,
    codespace: Path,
) -> Path | None:
    """Write the post-implementation assessment prompt."""
    del codespace

    paths = PathRegistry(planspace)
    prompt_path = paths.post_impl_assessment_prompt(section_number)
    governance_packet = paths.governance_packet(section_number)
    trace_index = paths.trace_dir() / f"section-{section_number}.json"
    trace_map = paths.trace_map(section_number)
    integration_proposal = paths.proposal(section_number)
    problem_frame = paths.problem_frame(section_number)
    assessment_output = paths.post_impl_assessment(section_number)

    content = f"""# Task: Post-Implementation Governance Assessment for Section {section_number}

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
  "debt_items": [],
  "refactor_reasons": [],
  "problem_ids_addressed": [],
  "pattern_ids_followed": [],
  "profile_id": ""
}}
```

Be conservative. When uncertain, prefer `accept_with_debt` over silent acceptance.
"""

    if not write_validated_prompt(content, prompt_path):
        return None
    return prompt_path


def read_post_impl_assessment(
    section_number: str,
    planspace: Path,
) -> dict | None:
    """Read and validate an assessment result."""
    path = PathRegistry(planspace).post_impl_assessment(section_number)
    data = read_json(path)
    if data is None:
        return None
    if not isinstance(data, dict):
        rename_malformed(path)
        return None

    verdict = data.get("verdict", "")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        rename_malformed(path)
        return None

    if str(data.get("section", "")).strip() != section_number:
        rename_malformed(path)
        return None

    for key in ("problem_ids_addressed", "pattern_ids_followed", "debt_items", "refactor_reasons"):
        value = data.get(key, [])
        if not isinstance(value, list):
            rename_malformed(path)
            return None

    profile_id = data.get("profile_id", "")
    if not isinstance(profile_id, str):
        rename_malformed(path)
        return None

    lenses = data.get("lenses", {})
    if not isinstance(lenses, dict):
        rename_malformed(path)
        return None

    return data


def record_assessment_governance(
    section_number: str,
    planspace: Path,
    assessment: dict,
) -> None:
    """Record governance IDs from an assessment into the trace index."""
    problem_ids = assessment.get("problem_ids_addressed")
    if not isinstance(problem_ids, list):
        problem_ids = []

    pattern_ids = assessment.get("pattern_ids_followed")
    if not isinstance(pattern_ids, list):
        pattern_ids = []

    profile_id = assessment.get("profile_id")
    if not isinstance(profile_id, str):
        profile_id = ""

    update_trace_governance(
        planspace,
        section_number,
        problem_ids=[str(item) for item in problem_ids if str(item).strip()],
        pattern_ids=[str(item) for item in pattern_ids if str(item).strip()],
        profile_id=profile_id,
    )
