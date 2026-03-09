# Task 1: ROAL Foundation — risk/ subpackage, types, schemas, PathRegistry

## Context

You are implementing the Risk-Optimization Adaptive Loop (ROAL) for the agent-implementation-skill codebase. This is Task 1 of 6 — the foundation layer.

Read these files first to understand the codebase patterns:
- `src/scripts/lib/core/path_registry.py` — PathRegistry pattern (add risk accessors here)
- `src/scripts/lib/core/artifact_io.py` — read_json/write_json helpers
- `src/scripts/lib/core/pipeline_state.py` — example of a state module
- `src/scripts/lib/dispatch/dispatch_metadata.py` — example of a small lib module
- `src/scripts/lib/repositories/strategic_state.py` — strategic state (will be extended later)
- `tests/component/test_path_registry.py` — existing PathRegistry tests

## What to Create

### 1. New subpackage: `src/scripts/lib/risk/`

Create `src/scripts/lib/risk/__init__.py` with exports.

### 2. `src/scripts/lib/risk/types.py`

Define all ROAL data types as Python dataclasses or enums. Use `from __future__ import annotations`.

```python
from enum import Enum
from dataclasses import dataclass, field

class StepClass(str, Enum):
    EXPLORE = "explore"
    STABILIZE = "stabilize"
    EDIT = "edit"
    COORDINATE = "coordinate"
    VERIFY = "verify"

class PostureProfile(str, Enum):
    P0_DIRECT = "P0"
    P1_LIGHT = "P1"
    P2_STANDARD = "P2"
    P3_GUARDED = "P3"
    P4_REOPEN = "P4"

class RiskType(str, Enum):
    CONTEXT_ROT = "context_rot"
    SILENT_DRIFT = "silent_drift"
    SCOPE_CREEP = "scope_creep"
    BRUTE_FORCE_REGRESSION = "brute_force_regression"
    CROSS_SECTION_INCOHERENCE = "cross_section_incoherence"
    TOOL_ISLAND_ISOLATION = "tool_island_isolation"
    STALE_ARTIFACT_CONTAMINATION = "stale_artifact_contamination"

class StepDecision(str, Enum):
    ACCEPT = "accept"
    REJECT_DEFER = "reject_defer"
    REJECT_REOPEN = "reject_reopen"

class RiskMode(str, Enum):
    SKIP = "skip"
    LIGHT = "light"
    FULL = "full"

class RiskConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
```

Define dataclasses for the artifact shapes:

```python
@dataclass
class RiskVector:
    context_rot: int = 0          # 0-4
    silent_drift: int = 0         # 0-4
    scope_creep: int = 0          # 0-4
    brute_force_regression: int = 0  # 0-4
    cross_section_incoherence: int = 0  # 0-4
    tool_island_isolation: int = 0     # 0-4
    stale_artifact_contamination: int = 0  # 0-4

@dataclass
class RiskModifiers:
    blast_radius: int = 0        # 0-4
    reversibility: int = 4       # 0-4 (4 = easy revert)
    observability: int = 4       # 0-4 (4 = easy detect)
    confidence: float = 0.5      # 0.0-1.0

@dataclass
class UnderstandingInventory:
    confirmed: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

@dataclass
class PackageStep:
    step_id: str
    step_class: StepClass
    summary: str
    prerequisites: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    expected_resolutions: list[str] = field(default_factory=list)
    mutation_surface: list[str] = field(default_factory=list)
    verification_surface: list[str] = field(default_factory=list)
    reversibility: str = "medium"  # high/medium/low

@dataclass
class StepAssessment:
    step_id: str
    step_class: StepClass
    summary: str
    prerequisites: list[str]
    risk_vector: RiskVector
    modifiers: RiskModifiers
    raw_risk: int               # 0-100
    dominant_risks: list[RiskType]

@dataclass
class RiskAssessment:
    assessment_id: str
    layer: str
    package_id: str
    assessment_scope: str
    understanding_inventory: UnderstandingInventory
    package_raw_risk: int
    assessment_confidence: float
    dominant_risks: list[RiskType]
    step_assessments: list[StepAssessment]
    frontier_candidates: list[str]
    reopen_recommendations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

@dataclass
class StepMitigation:
    step_id: str
    decision: StepDecision
    posture: PostureProfile | None = None
    mitigations: list[str] = field(default_factory=list)
    residual_risk: int | None = None
    reason: str | None = None
    wait_for: list[str] = field(default_factory=list)
    route_to: str | None = None
    dispatch_shape: dict | None = None

@dataclass
class RiskPlan:
    plan_id: str
    assessment_id: str
    package_id: str
    layer: str
    step_decisions: list[StepMitigation]
    accepted_frontier: list[str]
    deferred_steps: list[str]
    reopen_steps: list[str]
    expected_reassessment_inputs: list[str] = field(default_factory=list)

@dataclass
class RiskPackage:
    package_id: str
    layer: str
    scope: str
    origin_problem_id: str
    origin_source: str
    steps: list[PackageStep]

@dataclass
class RiskHistoryEntry:
    package_id: str
    step_id: str
    layer: str
    step_class: StepClass
    posture: PostureProfile
    predicted_risk: int
    actual_outcome: str        # "success" / "failure" / "partial"
    surfaced_surprises: list[str] = field(default_factory=list)
    verification_outcome: str | None = None
    dominant_risks: list[RiskType] = field(default_factory=list)
    blast_radius_band: int = 0

@dataclass
class IntentRiskHint:
    risk_mode: RiskMode
    risk_confidence: RiskConfidence
    risk_budget_hint: int = 0
    posture_floor: PostureProfile | None = None
```

### 3. `src/scripts/lib/risk/serialization.py`

JSON serialization/deserialization for all risk types. Use `artifact_io.read_json`/`write_json` from `lib.core.artifact_io`.

Functions needed:
- `serialize_assessment(assessment: RiskAssessment) -> dict` — convert to JSON-serializable dict
- `deserialize_assessment(data: dict) -> RiskAssessment` — parse from JSON dict
- `serialize_plan(plan: RiskPlan) -> dict`
- `deserialize_plan(data: dict) -> RiskPlan`
- `serialize_package(package: RiskPackage) -> dict`
- `deserialize_package(data: dict) -> RiskPackage`
- `serialize_history_entry(entry: RiskHistoryEntry) -> dict`
- `deserialize_history_entry(data: dict) -> RiskHistoryEntry`
- `write_risk_artifact(path: Path, data: dict) -> None` — wrapper around write_json
- `read_risk_artifact(path: Path) -> dict | None` — wrapper around read_json

Enum fields should serialize to their `.value` string and deserialize back.

### 4. PathRegistry extensions

Add these methods to `src/scripts/lib/core/path_registry.py`:

```python
# --- Risk artifact accessors ---

def risk_dir(self) -> Path:
    return self._artifacts / "risk"

def risk_package(self, scope: str) -> Path:
    return self.risk_dir() / f"{scope}-risk-package.json"

def risk_assessment(self, scope: str) -> Path:
    return self.risk_dir() / f"{scope}-risk-assessment.json"

def risk_plan(self, scope: str) -> Path:
    return self.risk_dir() / f"{scope}-risk-plan.json"

def risk_history(self) -> Path:
    return self.risk_dir() / "risk-history.jsonl"

def risk_summary(self, scope: str) -> Path:
    return self.risk_dir() / f"{scope}-risk-summary.md"

def risk_parameters(self) -> Path:
    return self.risk_dir() / "risk-parameters.json"
```

### 5. Tests

Create `tests/component/test_risk_types.py`:
- Test that all enums have expected values
- Test that dataclasses can be instantiated with defaults
- Test serialization round-trips for all types

Create `tests/component/test_risk_path_registry.py`:
- Test all new PathRegistry risk accessors return correct paths

## Important Rules

- Use `from __future__ import annotations` in every new file
- Follow the exact import patterns used in existing lib modules
- No backwards compatibility layers
- No placeholder/stub implementations — implement everything fully
- Run `uv run pytest tests/component/test_risk_types.py tests/component/test_risk_path_registry.py -v` to verify

## Verification

After implementation, run:
```bash
uv run pytest tests/component/test_risk_types.py tests/component/test_risk_path_registry.py tests/component/test_path_registry.py -v
```

All tests must pass. Existing PathRegistry tests must not break.
