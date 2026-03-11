from __future__ import annotations

import sqlite3
from pathlib import Path

from evals.agentic.collectors import collect_outputs
from evals.agentic.scenario_loader import CollectSpec, StructuralCheck, load_scenarios
from evals.agentic.seed_engine import cleanup_scenario, seed_scenario
from evals.agentic.structural_checks import run_structural_checks


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_load_scenarios_discovers_and_filters_existing_fixtures() -> None:
    specs = load_scenarios(PROJECT_ROOT)

    scenario_ids = {spec.id for spec in specs}
    assert "readiness-triggers-research-planner" in scenario_ids
    assert "scan-quick-greenfield-related-files" in scenario_ids

    filtered = load_scenarios(
        PROJECT_ROOT,
        category="happy-path",
        wave=1,
        cost_tier="cheap",
    )
    assert filtered
    assert all(spec.category == "happy-path" for spec in filtered)
    assert all(spec.wave == 1 for spec in filtered)
    assert all(spec.cost_tier == "cheap" for spec in filtered)


def test_seed_scenario_creates_isolated_workspace_with_db(tmp_path: Path) -> None:
    spec = load_scenarios(
        PROJECT_ROOT,
        scenario_id="readiness-triggers-research-planner",
    )[0]

    state = seed_scenario(spec, PROJECT_ROOT, base_tmp=tmp_path)
    try:
        assert state.planspace.exists()
        assert state.codespace.exists()
        assert (state.planspace / "run.db").exists()
        assert (state.planspace / "artifacts" / "signals").exists()
        assert (
            state.planspace
            / "artifacts"
            / "sections"
            / "section-01.md"
        ).exists()
        assert (
            state.codespace / "src" / "payments" / "confirm.py"
        ).exists()
    finally:
        cleanup_scenario(state)

    assert not state.root.exists()


def test_collect_outputs_and_structural_checks_cover_core_predicates(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    (planspace / "artifacts" / "signals").mkdir(parents=True)
    (planspace / "docs").mkdir(parents=True)

    (planspace / "artifacts" / "research-plan.json").write_text(
        '{\n  "section": "01",\n  "tickets": []\n}\n',
        encoding="utf-8",
    )
    (planspace / "artifacts" / "signals" / "ready.json").write_text(
        '{\n  "state": "planned",\n  "section": "01"\n}\n',
        encoding="utf-8",
    )
    (planspace / "docs" / "notes.md").write_text(
        "# Context\n\nworkflow ready\n",
        encoding="utf-8",
    )

    with sqlite3.connect(planspace / "run.db") as conn:
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, task_type TEXT, status TEXT)"
        )
        conn.execute(
            "INSERT INTO tasks(id, task_type, status) VALUES (1, 'research_domain_ticket', 'pending')"
        )
        conn.commit()

    spec = CollectSpec(
        file_globs=["artifacts/**/*.json", "docs/*.md"],
        db_queries=[
            {
                "name": "tasks",
                "sql": "SELECT id, task_type, status FROM tasks ORDER BY id",
            }
        ],
    )

    collected = collect_outputs(spec, planspace)
    checks = [
        StructuralCheck(type="exists", path="artifacts/research-plan.json", required=True),
        StructuralCheck(type="json_valid", path="artifacts/research-plan.json", required=True),
        StructuralCheck(
            type="json_has_keys",
            path="artifacts/research-plan.json",
            keys=["section", "tickets"],
            required=True,
        ),
        StructuralCheck(
            type="markdown_has_heading",
            path="docs/notes.md",
            heading="Context",
            required=True,
        ),
        StructuralCheck(
            type="db_min_rows",
            query="tasks",
            where={"task_type": "research_domain_ticket"},
            min=1,
            required=True,
        ),
        StructuralCheck(
            type="glob_min_count",
            path="artifacts/**/*.json",
            min=2,
            required=True,
        ),
        StructuralCheck(
            type="text_contains",
            path="docs/notes.md",
            pattern="workflow ready",
            required=True,
        ),
        StructuralCheck(
            type="path_absent",
            path="artifacts/missing.json",
            required=True,
        ),
        StructuralCheck(
            type="signal_state",
            path="artifacts/signals/ready.json",
            allowed_states=["planned", "complete"],
            required=True,
        ),
    ]

    results = run_structural_checks(checks, collected, planspace)
    assert results
    assert all(result.verdict == "PASS" for result in results)
