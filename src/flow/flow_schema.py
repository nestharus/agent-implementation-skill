"""Flow declaration schema — data structures and parsing for v2 task flows.

Agents can emit task requests in two formats:

1. Legacy (v1): single JSON object or JSONL with ``task_type`` per entry.
2. v2 envelope: ``{"version": 2, "actions": [...]}`` with chain/fanout
   flow control.

This module provides the data structures, parser, and validator for
both formats. Legacy requests are normalized into FlowDeclarations
with a single child chain action so downstream code has one shape.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# task_router lives alongside flow_schema (in src/scripts/)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from task_router import TASK_ROUTES  # noqa: E402


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TaskSpec:
    task_type: str
    concern_scope: str = ""
    payload_path: str = ""
    priority: str = "normal"
    problem_id: str = ""


@dataclass
class ChainAction:
    kind: str = "chain"  # literal "chain"
    steps: list[TaskSpec] = field(default_factory=list)


@dataclass
class GateSpec:
    mode: str = "all"
    failure_policy: str = "include"
    synthesis: TaskSpec | None = None


@dataclass
class BranchSpec:
    label: str = ""
    chain_ref: str = ""  # named package reference
    args: dict = field(default_factory=dict)
    steps: list[TaskSpec] = field(default_factory=list)


@dataclass
class FanoutAction:
    kind: str = "fanout"  # literal "fanout"
    branches: list[BranchSpec] = field(default_factory=list)
    gate: GateSpec | None = None


@dataclass
class FlowDeclaration:
    version: int
    actions: list  # list of ChainAction | FanoutAction


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _dict_to_task_spec(d: dict) -> TaskSpec:
    """Convert a raw dict to a TaskSpec, ignoring unknown fields."""
    return TaskSpec(
        task_type=d.get("task_type", ""),
        concern_scope=d.get("concern_scope", ""),
        payload_path=d.get("payload_path", ""),
        priority=d.get("priority", "normal"),
        problem_id=d.get("problem_id", ""),
    )


def _dict_to_gate_spec(d: dict) -> GateSpec:
    """Convert a raw dict to a GateSpec."""
    synthesis = None
    if "synthesis" in d and isinstance(d["synthesis"], dict):
        synthesis = _dict_to_task_spec(d["synthesis"])
    return GateSpec(
        mode=d.get("mode", "all"),
        failure_policy=d.get("failure_policy", "include"),
        synthesis=synthesis,
    )


def _dict_to_branch_spec(d: dict) -> BranchSpec:
    """Convert a raw dict to a BranchSpec."""
    steps = []
    for s in d.get("steps", []):
        if isinstance(s, dict):
            steps.append(_dict_to_task_spec(s))
    return BranchSpec(
        label=d.get("label", ""),
        chain_ref=d.get("chain_ref", ""),
        args=d.get("args", {}),
        steps=steps,
    )


def _parse_action(d: dict) -> ChainAction | FanoutAction | None:
    """Parse a single action dict into a typed action, or None on error."""
    kind = d.get("kind", "")
    if kind == "chain":
        steps = []
        for s in d.get("steps", []):
            if isinstance(s, dict):
                steps.append(_dict_to_task_spec(s))
        return ChainAction(kind="chain", steps=steps)
    if kind == "fanout":
        branches = []
        for b in d.get("branches", []):
            if isinstance(b, dict):
                branches.append(_dict_to_branch_spec(b))
        gate = None
        if "gate" in d and isinstance(d["gate"], dict):
            gate = _dict_to_gate_spec(d["gate"])
        return FanoutAction(kind="fanout", branches=branches, gate=gate)
    # Unknown kind — return None so validation catches it
    return None


def _is_legacy_task(d: dict) -> bool:
    """Return True if *d* looks like a legacy single-step task request."""
    return "task_type" in d and "actions" not in d and "version" not in d


def normalize_flow_declaration(raw: object) -> FlowDeclaration:
    """Normalize raw parsed JSON into a FlowDeclaration.

    Legacy one-step requests become new child chains with one step.
    They do NOT extend the current chain.

    Raises ``ValueError`` on structurally unparseable input.
    """
    # --- Legacy: single dict with task_type ---
    if isinstance(raw, dict) and _is_legacy_task(raw):
        step = _dict_to_task_spec(raw)
        return FlowDeclaration(
            version=1,
            actions=[ChainAction(kind="chain", steps=[step])],
        )

    # --- Legacy: list of dicts (each with task_type) ---
    if isinstance(raw, list):
        steps = []
        for entry in raw:
            if isinstance(entry, dict) and "task_type" in entry:
                steps.append(_dict_to_task_spec(entry))
        if not steps:
            raise ValueError("JSON array contained no valid task entries")
        return FlowDeclaration(
            version=1,
            actions=[ChainAction(kind="chain", steps=steps)],
        )

    # --- v2 envelope ---
    if isinstance(raw, dict) and "actions" in raw:
        version = raw.get("version")
        if version is None:
            raise ValueError(
                "Envelope with 'actions' must include 'version' field"
            )
        actions = []
        raw_actions = raw.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("'actions' must be a list")
        for a in raw_actions:
            if isinstance(a, dict):
                parsed = _parse_action(a)
                if parsed is not None:
                    actions.append(parsed)
                else:
                    # Unknown action kind — keep raw dict so validation
                    # can report it
                    actions.append(a)
        return FlowDeclaration(version=int(version), actions=actions)

    raise ValueError(
        f"Cannot normalize to FlowDeclaration: unexpected type "
        f"{type(raw).__name__}"
    )


def parse_flow_signal(signal_path: Path) -> FlowDeclaration:
    """Read a task-request JSON file and parse into FlowDeclaration.

    Handles:
    - Legacy one-step JSON (single dict with task_type)
    - JSON arrays of legacy tasks
    - JSONL (one task per line)
    - v2 envelope (version: 2, actions: [...])

    Raises ``ValueError`` on malformed input.
    """
    if not signal_path.exists():
        raise ValueError(f"Signal file not found: {signal_path}")

    raw_text = signal_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError(f"Signal file is empty: {signal_path}")

    # Try full JSON parse first (object, array, or v2 envelope)
    try:
        parsed = json.loads(raw_text)
        return normalize_flow_declaration(parsed)
    except json.JSONDecodeError:
        pass

    # Try JSONL (one JSON object per line)
    entries: list[dict] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Malformed JSONL in {signal_path}: {exc}"
            ) from exc

    if not entries:
        raise ValueError(f"No valid entries in JSONL file: {signal_path}")

    return normalize_flow_declaration(entries)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _known_task_types() -> frozenset[str]:
    """Return the set of known task types from the task router."""
    return frozenset(TASK_ROUTES.keys())


def validate_flow_declaration(decl: FlowDeclaration) -> list[str]:
    """Validate a FlowDeclaration. Returns list of error strings (empty = valid).

    Rules:
    - version must be 1 (legacy-normalized) or 2 (new envelope)
    - actions must be a non-empty list
    - At most one top-level chain action
    - chain.steps must each have a known task_type
    - fanout branches must provide either steps or chain_ref (not both, not neither)
    - gate.mode only supports "all" initially
    - Unknown task_type values are rejected
    - Unknown chain_ref values are rejected
    """
    errors: list[str] = []
    known_types = _known_task_types()

    # Version check
    if decl.version not in (1, 2):
        errors.append(
            f"Unsupported version: {decl.version} (expected 1 or 2)"
        )

    # Actions must be a list
    if not isinstance(decl.actions, list):
        errors.append("'actions' must be a list")
        return errors

    if not decl.actions:
        errors.append("'actions' list is empty — nothing to dispatch")
        return errors

    # At most one top-level chain action
    chain_count = sum(
        1 for a in decl.actions
        if isinstance(a, ChainAction)
    )
    if chain_count > 1:
        errors.append(
            f"At most one top-level chain action allowed, found {chain_count}"
        )

    for i, action in enumerate(decl.actions):
        if isinstance(action, ChainAction):
            # Validate each step
            if not action.steps:
                errors.append(f"actions[{i}]: chain has no steps")
            for j, step in enumerate(action.steps):
                if not step.task_type:
                    errors.append(
                        f"actions[{i}].steps[{j}]: missing task_type"
                    )
                elif step.task_type not in known_types:
                    errors.append(
                        f"actions[{i}].steps[{j}]: unknown task_type "
                        f"{step.task_type!r}"
                    )
                if not step.payload_path:
                    errors.append(
                        f"actions[{i}].steps[{j}]: missing payload_path "
                        f"(queued tasks require payload-backed context)"
                    )
        elif isinstance(action, FanoutAction):
            if not action.branches:
                errors.append(f"actions[{i}]: fanout has no branches")
            for k, branch in enumerate(action.branches):
                has_steps = bool(branch.steps)
                has_ref = bool(branch.chain_ref)
                if has_steps and has_ref:
                    errors.append(
                        f"actions[{i}].branches[{k}]: branch must provide "
                        f"either steps or chain_ref, not both"
                    )
                elif not has_steps and not has_ref:
                    errors.append(
                        f"actions[{i}].branches[{k}]: branch must provide "
                        f"either steps or chain_ref"
                    )
                # Validate steps if present
                for j, step in enumerate(branch.steps):
                    if not step.task_type:
                        errors.append(
                            f"actions[{i}].branches[{k}].steps[{j}]: "
                            f"missing task_type"
                        )
                    elif step.task_type not in known_types:
                        errors.append(
                            f"actions[{i}].branches[{k}].steps[{j}]: "
                            f"unknown task_type {step.task_type!r}"
                        )
                    if not step.payload_path:
                        errors.append(
                            f"actions[{i}].branches[{k}].steps[{j}]: "
                            f"missing payload_path (queued tasks require "
                            f"payload-backed context)"
                        )
                # Validate chain_ref if present — check against catalog.
                if has_ref:
                    from flow_catalog import KNOWN_PACKAGES
                    if branch.chain_ref not in KNOWN_PACKAGES:
                        errors.append(
                            f"actions[{i}].branches[{k}]: unknown chain_ref "
                            f"{branch.chain_ref!r}"
                        )
            # Gate validation
            if action.gate:
                if action.gate.mode != "all":
                    errors.append(
                        f"actions[{i}].gate: unsupported mode "
                        f"{action.gate.mode!r} (only 'all' supported)"
                    )
                if action.gate.synthesis:
                    tt = action.gate.synthesis.task_type
                    if tt and tt not in known_types:
                        errors.append(
                            f"actions[{i}].gate.synthesis: unknown "
                            f"task_type {tt!r}"
                        )
        else:
            # Unknown action type (was a raw dict that _parse_action
            # could not resolve)
            kind = (
                action.get("kind", "<missing>")
                if isinstance(action, dict)
                else type(action).__name__
            )
            errors.append(
                f"actions[{i}]: unknown action kind {kind!r}"
            )

    return errors
