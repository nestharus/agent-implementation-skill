"""Component tests for shared coordination planning helpers."""

from __future__ import annotations

import json

from src.coordination.service.planner import (
    _parse_coordination_plan,
    write_coordination_plan_prompt,
)


def test_parse_coordination_plan_parses_fenced_json_and_coerces_bridge() -> None:
    problems = [
        {"section": "01"},
        {"section": "02"},
        {"section": "03"},
    ]
    agent_output = """Planner response

```json
{
  "groups": [
    {"problems": [0, 1], "strategy": "sequential", "bridge": true},
    {"problems": [2], "strategy": "parallel", "bridge": "later"}
  ],
  "batches": [[0], [1]]
}
```
"""

    plan = _parse_coordination_plan(agent_output, problems)

    assert plan is not None
    assert plan["groups"][0]["bridge"] == {"needed": True}
    assert plan["groups"][1]["bridge"] == {"needed": False}
    assert plan["batches"] == [[0], [1]]


def test_parse_coordination_plan_rejects_duplicate_problem_indices() -> None:
    problems = [{"section": "01"}, {"section": "02"}]
    agent_output = """{
      "groups": [
        {"problems": [0, 1]},
        {"problems": [1]}
      ]
    }"""

    assert _parse_coordination_plan(agent_output, problems) is None


def test_write_coordination_plan_prompt_writes_artifacts_and_refs(planspace) -> None:
    problems = [
        {"section": "01", "type": "misaligned", "description": "drift", "files": []},
    ]
    artifacts = planspace / "artifacts"
    (artifacts / "codemap.md").write_text("# Codemap\n", encoding="utf-8")
    corrections = artifacts / "signals" / "codemap-corrections.json"
    corrections.write_text("{}\n", encoding="utf-8")
    recurrence = artifacts / "coordination" / "recurrence.json"
    recurrence.write_text('{"recurring_sections": ["01"]}\n', encoding="utf-8")

    prompt_path = write_coordination_plan_prompt(problems, planspace)
    prompt = prompt_path.read_text(encoding="utf-8")
    stored = json.loads(
        (artifacts / "coordination" / "problems.json").read_text(encoding="utf-8"),
    )

    assert stored == problems
    assert "coordination/problems.json" in prompt
    assert "codemap-corrections.json" in prompt
    assert "recurrence.json" in prompt
