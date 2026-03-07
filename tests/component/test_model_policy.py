"""Component tests for ModelPolicy loading and lookup."""

from __future__ import annotations

import json

from src.scripts.lib.core.model_policy import ModelPolicy, load_model_policy, resolve


def test_load_model_policy_returns_defaults_when_file_missing(tmp_path) -> None:
    policy = load_model_policy(tmp_path)

    assert isinstance(policy, ModelPolicy)
    assert policy.setup == "claude-opus"
    assert policy["proposal"] == "gpt-5.4-high"
    assert policy["escalation_triggers"]["stall_count"] == 2
    assert policy.get("scan") == {}


def test_load_model_policy_merges_overrides_and_nested_triggers(tmp_path) -> None:
    policy_path = tmp_path / "artifacts" / "model-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({
        "proposal": "gpt-5.4-xhigh",
        "scan": {"codemap_build": "scan-model"},
        "escalation_triggers": {"stall_count": 5},
    }), encoding="utf-8")

    policy = load_model_policy(tmp_path)

    assert policy.proposal == "gpt-5.4-xhigh"
    assert policy["alignment"] == "claude-opus"
    assert policy["escalation_triggers"] == {
        "stall_count": 5,
        "max_attempts_before_escalation": 3,
    }
    assert resolve(policy, "scan.codemap_build") == "scan-model"


def test_load_model_policy_preserves_known_non_section_loop_keys(tmp_path) -> None:
    policy_path = tmp_path / "artifacts" / "model-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({
        "substrate_trigger_min_vacuum_sections": 7,
        "future_key": "future-model",
    }), encoding="utf-8")

    policy = load_model_policy(tmp_path)

    assert policy["substrate_trigger_min_vacuum_sections"] == 7
    assert policy["future_key"] == "future-model"
    assert policy.get("missing", "fallback") == "fallback"


def test_load_model_policy_renames_non_object_json_and_falls_back(tmp_path) -> None:
    policy_path = tmp_path / "artifacts" / "model-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text('["not", "an", "object"]', encoding="utf-8")

    policy = load_model_policy(tmp_path)

    assert policy.setup == "claude-opus"
    assert not policy_path.exists()
    assert (policy_path.parent / "model-policy.malformed.json").exists()


def test_resolve_raises_for_missing_or_non_nested_key(tmp_path) -> None:
    policy_path = tmp_path / "artifacts" / "model-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps({"scan": {}}), encoding="utf-8")
    policy = load_model_policy(tmp_path)

    try:
        resolve(policy, "scan.codemap_build")
    except KeyError:
        pass
    else:
        raise AssertionError("Expected KeyError for missing dotted scan key")
