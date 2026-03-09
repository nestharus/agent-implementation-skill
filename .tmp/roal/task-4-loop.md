# Task 4: ROAL Loop Orchestrator — Package Builder, Loop, Threshold Enforcement

## Context

You are implementing the Risk-Optimization Adaptive Loop (ROAL). This is Task 4 of 6 — the loop orchestrator.

**Prerequisite**: Tasks 1-3 have been completed. The `src/scripts/lib/risk/` subpackage has types, serialization, quantifier, posture, history, and engagement modules.

Read these files first:
- `src/scripts/lib/risk/types.py` — all ROAL data types
- `src/scripts/lib/risk/serialization.py` — JSON serialization
- `src/scripts/lib/risk/quantifier.py` — risk scoring
- `src/scripts/lib/risk/posture.py` — posture selection with oscillation prevention
- `src/scripts/lib/risk/history.py` — risk history service
- `src/scripts/lib/risk/engagement.py` — engagement mode determination
- `src/scripts/lib/core/path_registry.py` — PathRegistry with risk accessors
- `src/scripts/lib/core/artifact_io.py` — read_json/write_json
- `src/scripts/lib/dispatch/agent_executor.py` — how agents are dispatched
- `src/scripts/lib/pipelines/implementation_pass.py` — example pipeline module
- `src/scripts/lib/pipelines/proposal_pass.py` — example pipeline module

## What to Create

### 1. `src/scripts/lib/risk/package_builder.py`

Builds and refreshes risk packages from existing artifacts.

```python
def build_package(
    scope: str,
    layer: str,
    problem_id: str,
    source: str,
    steps: list[PackageStep],
) -> RiskPackage:
    """Create a new risk package from explicit step definitions."""

def build_package_from_proposal(
    scope: str,
    planspace: Path,
) -> RiskPackage:
    """Build a package from proposal-state and microstrategy artifacts.

    Reads:
    - proposal excerpt for the section
    - microstrategy if present
    - readiness artifacts
    - problem frame

    Produces a package with steps derived from the proposal structure.
    For a typical proposal this might be:
    - s1: explore (refresh understanding)
    - s2: edit (implement changes)
    - s3: verify (alignment check)

    If microstrategy is present, use its finer-grained steps.
    """

def refresh_package(
    existing: RiskPackage,
    completed_steps: list[str],
    new_evidence: dict,
) -> RiskPackage:
    """Refresh a package after accepted steps complete.

    Removes completed steps, updates prerequisites,
    and may add new steps based on surfaced evidence.
    """

def write_package(paths: PathRegistry, package: RiskPackage) -> Path:
    """Persist a package to the risk directory."""

def read_package(paths: PathRegistry, scope: str) -> RiskPackage | None:
    """Read an existing package from the risk directory."""
```

### 2. `src/scripts/lib/risk/threshold.py`

Mechanical threshold enforcement (script responsibility, not agent).

```python
def validate_risk_plan(plan: RiskPlan, parameters: dict) -> list[str]:
    """Validate that a risk plan meets policy requirements.

    Checks:
    - Output is well-formed (all required fields present)
    - Accepted steps have residual risk below policy threshold for their class
    - Hard invariants are satisfied
    - Dispatch shapes stay within bounded substrate

    Returns list of violations (empty = valid).
    """

def enforce_thresholds(
    plan: RiskPlan,
    assessments: dict[str, StepAssessment],
    parameters: dict,
) -> RiskPlan:
    """Enforce thresholds mechanically.

    If any accepted step has residual_risk above threshold for its class,
    downgrade it to reject_defer.

    This is the script's fail-closed enforcement — it does not choose
    mitigations, it only validates decisions.
    """

def load_default_parameters() -> dict:
    """Return default risk parameters.

    Returns dict with:
    - posture_bands: {P0: [0, 19], P1: [20, 39], ...}
    - step_thresholds: {explore: 60, stabilize: 60, edit: 45, coordinate: 35, verify: 50}
    - cooldown_iterations: 2
    - relaxation_required_successes: 3
    - history_adjustment_bound: 10.0
    """
```

### 3. `src/scripts/lib/risk/loop.py`

The ROAL loop orchestrator. This is the main entry point.

```python
def run_risk_loop(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
    max_iterations: int = 5,
) -> RiskPlan:
    """Run the full ROAL loop for a package.

    Stages:
    A. Package is already provided (built or refreshed by caller)
    B. Risk assessment — dispatch Risk Agent, parse assessment
    C. Mitigation planning — dispatch Tool Agent, parse plan
    D. Threshold enforcement — mechanical validation
    E. Return the validated plan (caller handles execution)
    F. Reassessment happens when caller calls run_risk_loop again after execution

    The loop iterates if threshold enforcement rejects the plan (up to max_iterations).

    Parameters:
        planspace: Path to the planspace directory
        scope: Section scope (e.g., "section-03")
        layer: Current layer (e.g., "implementation")
        package: The risk package to assess
        dispatch_fn: Function to dispatch agents (matches dispatch_agent signature)
        max_iterations: Max assessment-planning iterations before falling back to P4

    Returns:
        Validated RiskPlan with accepted frontier and deferred/reopen steps
    """

def run_lightweight_risk_check(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
) -> RiskPlan:
    """Run a lightweight risk check (single assessment, no full loop).

    Used when engagement mode is LIGHT.
    Dispatches Risk Agent only, applies default posture based on score.
    No Tool Agent dispatch — posture is mechanically derived.
    """

def build_risk_assessment_prompt(
    package: RiskPackage,
    planspace: Path,
    scope: str,
) -> str:
    """Build the prompt for the Risk Agent.

    Assembles context from planspace artifacts:
    - section spec, proposal excerpt, alignment excerpt
    - problem frame, microstrategy
    - readiness, proposal-state
    - consequence notes, impact artifacts
    - tool registry, codemap
    - risk history
    - monitor signals

    Returns the assembled prompt string.
    """

def build_optimization_prompt(
    assessment: RiskAssessment,
    package: RiskPackage,
    parameters: dict,
    planspace: Path,
) -> str:
    """Build the prompt for the Tool Agent (Execution Optimizer).

    Includes:
    - The full risk assessment
    - The current package
    - Risk parameters and thresholds
    - Tool registry
    - Risk history for pattern context
    """

def parse_risk_assessment(response: str) -> RiskAssessment | None:
    """Parse the Risk Agent's JSON response into a RiskAssessment.

    Extracts JSON from the response (may be wrapped in code fences).
    Returns None if parsing fails.
    """

def parse_risk_plan(response: str) -> RiskPlan | None:
    """Parse the Tool Agent's JSON response into a RiskPlan.

    Extracts JSON from the response (may be wrapped in code fences).
    Returns None if parsing fails.
    """
```

### 4. Update `src/scripts/lib/risk/__init__.py`

Export all public functions and types from the risk subpackage.

### 5. Tests

Create `tests/component/test_risk_package_builder.py`:
- Test build_package creates correct structure
- Test build_package_from_proposal with minimal proposal
- Test refresh_package removes completed steps
- Test write_package and read_package round-trip

Create `tests/component/test_risk_threshold.py`:
- Test validate_risk_plan with valid plan returns empty violations
- Test validate_risk_plan catches overrisk accepted steps
- Test enforce_thresholds downgrades over-threshold steps to reject_defer
- Test load_default_parameters returns expected structure

Create `tests/component/test_risk_loop.py`:
- Test build_risk_assessment_prompt includes expected context
- Test build_optimization_prompt includes assessment and parameters
- Test parse_risk_assessment with valid JSON
- Test parse_risk_assessment with code-fenced JSON
- Test parse_risk_assessment with invalid JSON returns None
- Test parse_risk_plan similar tests
- Test run_lightweight_risk_check with mocked dispatch
- Test run_risk_loop with mocked dispatch (single iteration, plan passes)
- Test run_risk_loop with mocked dispatch (plan fails threshold, retries)

## Important Rules

- Use `from __future__ import annotations` in every new file
- Import from `lib.risk.types` for all ROAL types
- Import from `lib.risk.serialization` for JSON conversion
- The loop.py module dispatches agents via a `dispatch_fn` callable parameter — it does NOT import dispatch_agent directly. This keeps it testable.
- For prompt building, read artifacts via PathRegistry and artifact_io
- For JSON extraction from agent responses, use a simple regex for code fences or try json.loads directly
- No backwards compatibility layers
- No placeholder stubs

## Verification

```bash
uv run pytest tests/component/test_risk_package_builder.py tests/component/test_risk_threshold.py tests/component/test_risk_loop.py -v
```

All tests must pass.
