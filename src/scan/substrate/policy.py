"""Shared configuration readers for substrate discovery."""

from __future__ import annotations

from pathlib import Path

from signals.repository.artifact_io import read_json
from scan.substrate.helpers import _registry_for_artifacts

DEFAULT_SUBSTRATE_MODELS: dict[str, str] = {
    "substrate_shard": "gpt-high",
    "substrate_pruner": "gpt-xhigh",
    "substrate_seeder": "gpt-high",
}

DEFAULT_TRIGGER_THRESHOLD = 2


def read_substrate_model_policy(artifacts_dir: Path) -> dict[str, str]:
    """Read substrate model assignments from ``model-policy.json``."""
    policy = dict(DEFAULT_SUBSTRATE_MODELS)
    policy_path = _registry_for_artifacts(artifacts_dir).model_policy()
    if policy_path.is_file():
        data = read_json(policy_path)
        if isinstance(data, dict):
            for key in DEFAULT_SUBSTRATE_MODELS:
                if key in data and isinstance(data[key], str):
                    policy[key] = data[key]
        else:
            print(
                "[SUBSTRATE][WARN] model-policy.json exists but is "
                "invalid -- renaming to .malformed.json"
            )
    return policy


def read_trigger_signals(artifacts_dir: Path) -> list[str]:
    """Read signal-driven SIS trigger requests."""
    signals_dir = _registry_for_artifacts(artifacts_dir).signals_dir()
    if not signals_dir.is_dir():
        return []

    triggered: list[str] = []
    for path in sorted(signals_dir.iterdir()):
        if not path.name.startswith("substrate-trigger-") or not path.name.endswith(".json"):
            continue

        data = read_json(path)
        if isinstance(data, dict) and "section" in data:
            triggered.append(str(data["section"]))
        elif isinstance(data, dict) and "sections" in data:
            for section in data["sections"]:
                triggered.append(str(section))
        else:
            print(
                f"[SUBSTRATE][WARN] {path.name} malformed "
                f"-- renaming to .malformed.json"
            )
    return triggered


def read_trigger_threshold(artifacts_dir: Path) -> int:
    """Read the vacuum section threshold from policy config."""
    policy_path = _registry_for_artifacts(artifacts_dir).model_policy()
    if policy_path.is_file():
        data = read_json(policy_path)
        if isinstance(data, dict):
            value = data.get("substrate_trigger_min_vacuum_sections")
            if isinstance(value, int) and value >= 1:
                return value
        else:
            print(
                "[SUBSTRATE][WARN] model-policy.json malformed while "
                "reading trigger threshold -- renaming to "
                ".malformed.json"
            )
    return DEFAULT_TRIGGER_THRESHOLD
