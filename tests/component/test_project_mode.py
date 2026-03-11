import json

from scan.project_mode import resolve_project_mode, write_mode_contract


def test_resolve_project_mode_prefers_json_signal(planspace) -> None:
    signal_path = planspace / "artifacts" / "signals" / "project-mode.json"
    signal_path.write_text(
        json.dumps({"mode": "greenfield", "constraints": ["seed needed"]}),
        encoding="utf-8",
    )

    mode, constraints = resolve_project_mode(planspace, "parent")

    assert mode == "greenfield"
    assert constraints == ["seed needed"]


def test_resolve_project_mode_uses_text_fallback_for_malformed_json(
    planspace,
) -> None:
    signal_path = planspace / "artifacts" / "signals" / "project-mode.json"
    signal_path.write_text("{not json", encoding="utf-8")
    text_path = planspace / "artifacts" / "project-mode.txt"
    text_path.write_text("brownfield\n", encoding="utf-8")

    mode, constraints = resolve_project_mode(planspace, "parent")

    assert mode == "brownfield"
    assert constraints == []
    assert signal_path.with_suffix(".malformed.json").exists()


def test_resolve_project_mode_pauses_and_rereads_after_resume(
    planspace, monkeypatch,
) -> None:
    calls = []

    def fake_pause(target, parent, message) -> None:
        calls.append((target, parent, message))
        (target / "artifacts" / "signals" / "project-mode.json").write_text(
            json.dumps({"mode": "brownfield", "constraints": ["keep api"]}),
            encoding="utf-8",
        )

    monkeypatch.setattr("scan.project_mode.pause_for_parent", fake_pause)

    mode, constraints = resolve_project_mode(planspace, "parent")

    assert mode == "brownfield"
    assert constraints == ["keep api"]
    assert calls == [
        (
            planspace,
            "parent",
            "pause:needs_parent:project-mode-missing — "
            "scan stage did not write project-mode signal",
        ),
    ]


def test_write_mode_contract_persists_expected_shape(planspace) -> None:
    write_mode_contract(planspace, "greenfield", ["seed needed"])

    written = json.loads(
        (planspace / "artifacts" / "mode-contract.json").read_text(
            encoding="utf-8",
        ),
    )
    assert written == {
        "mode": "greenfield",
        "constraints": ["seed needed"],
        "expected_outputs": [
            "integration proposals",
            "code changes",
            "alignment checks",
        ],
    }
