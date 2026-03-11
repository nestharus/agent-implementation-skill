"""Component tests for substrate policy readers."""

from __future__ import annotations

import json

from src.scan.substrate_policy import (
    DEFAULT_SUBSTRATE_MODELS,
    DEFAULT_TRIGGER_THRESHOLD,
    read_substrate_model_policy,
    read_trigger_signals,
    read_trigger_threshold,
)


def test_read_substrate_model_policy_uses_defaults_and_overrides(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    policy_path = artifacts_dir / "model-policy.json"
    policy_path.write_text(
        json.dumps({"substrate_shard": "custom-shard"}),
        encoding="utf-8",
    )

    policy = read_substrate_model_policy(artifacts_dir)

    assert policy["substrate_shard"] == "custom-shard"
    assert policy["substrate_pruner"] == DEFAULT_SUBSTRATE_MODELS["substrate_pruner"]


def test_read_substrate_model_policy_renames_malformed_json(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    policy_path = artifacts_dir / "model-policy.json"
    policy_path.write_text("{bad json", encoding="utf-8")

    policy = read_substrate_model_policy(artifacts_dir)

    assert policy == DEFAULT_SUBSTRATE_MODELS
    assert not policy_path.exists()
    assert policy_path.with_suffix(".malformed.json").exists()


def test_read_trigger_signals_reads_single_and_multi_section_signals(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    signals_dir = artifacts_dir / "signals"
    signals_dir.mkdir()
    (signals_dir / "substrate-trigger-01.json").write_text(
        json.dumps({"section": "01"}),
        encoding="utf-8",
    )
    (signals_dir / "substrate-trigger-reconciliation.json").write_text(
        json.dumps({"sections": ["02", 3]}),
        encoding="utf-8",
    )
    (signals_dir / "ignore.json").write_text(json.dumps({"section": "99"}), encoding="utf-8")

    assert read_trigger_signals(artifacts_dir) == ["01", "02", "3"]


def test_read_trigger_signals_renames_malformed_json(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    signals_dir = artifacts_dir / "signals"
    signals_dir.mkdir()
    signal_path = signals_dir / "substrate-trigger-01.json"
    signal_path.write_text("{bad json", encoding="utf-8")

    assert read_trigger_signals(artifacts_dir) == []
    assert not signal_path.exists()
    assert signal_path.with_suffix(".malformed.json").exists()


def test_read_trigger_threshold_defaults_and_validates(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()

    assert read_trigger_threshold(artifacts_dir) == DEFAULT_TRIGGER_THRESHOLD

    policy_path = artifacts_dir / "model-policy.json"
    policy_path.write_text(
        json.dumps({"substrate_trigger_min_vacuum_sections": 5}),
        encoding="utf-8",
    )
    assert read_trigger_threshold(artifacts_dir) == 5

    policy_path.write_text(
        json.dumps({"substrate_trigger_min_vacuum_sections": 0}),
        encoding="utf-8",
    )
    assert read_trigger_threshold(artifacts_dir) == DEFAULT_TRIGGER_THRESHOLD
