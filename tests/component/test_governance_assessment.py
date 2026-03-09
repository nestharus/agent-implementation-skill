from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.governance.assessment import (
    read_post_impl_assessment,
    record_assessment_governance,
    write_post_impl_assessment_prompt,
)


def test_write_post_impl_assessment_prompt_uses_validated_writer(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    planspace.mkdir()
    codespace.mkdir()

    prompt_path = write_post_impl_assessment_prompt("01", planspace, codespace)
    prompt_text = prompt_path.read_text(encoding="utf-8")

    assert "Governance packet" in prompt_text
    assert "Trace index" in prompt_text
    assert "Write the assessment JSON to:" in prompt_text
    assert "section-01-post-impl-assessment.json" in prompt_text


def test_read_post_impl_assessment_validates_verdict_and_preserves_corrupt_file(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    assessment_path = (
        planspace
        / "artifacts"
        / "governance"
        / "section-01-post-impl-assessment.json"
    )
    assessment_path.parent.mkdir(parents=True, exist_ok=True)

    assessment_path.write_text(
        json.dumps(
            {
                "section": "01",
                "verdict": "accept_with_debt",
                "lenses": {},
                "debt_items": ["watch coupling"],
                "refactor_reasons": [],
                "problem_ids_addressed": ["PRB-0009"],
                "pattern_ids_followed": ["PAT-0003"],
                "profile_id": "PHI-global",
            }
        ),
        encoding="utf-8",
    )
    assert read_post_impl_assessment("01", planspace)["verdict"] == "accept_with_debt"

    assessment_path.write_text(
        json.dumps(
            {
                "section": "01",
                "verdict": "unknown",
                "lenses": {},
                "debt_items": [],
                "refactor_reasons": [],
                "problem_ids_addressed": [],
                "pattern_ids_followed": [],
                "profile_id": "PHI-global",
            }
        ),
        encoding="utf-8",
    )

    assert read_post_impl_assessment("01", planspace) is None
    assert (
        planspace
        / "artifacts"
        / "governance"
        / "section-01-post-impl-assessment.malformed.json"
    ).exists()


def test_record_assessment_governance_updates_trace_index(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    trace_path = planspace / "artifacts" / "trace" / "section-01.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(
            {
                "section": "01",
                "governance": {
                    "packet_path": "packet.json",
                    "packet_hash": "hash",
                    "problem_ids": [],
                    "pattern_ids": [],
                    "profile_id": "",
                },
            }
        ),
        encoding="utf-8",
    )

    record_assessment_governance(
        "01",
        planspace,
        {
            "problem_ids_addressed": ["PRB-0009"],
            "pattern_ids_followed": ["PAT-0003"],
            "profile_id": "PHI-global",
        },
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["governance"]["problem_ids"] == ["PRB-0009"]
    assert trace["governance"]["pattern_ids"] == ["PAT-0003"]
    assert trace["governance"]["profile_id"] == "PHI-global"
