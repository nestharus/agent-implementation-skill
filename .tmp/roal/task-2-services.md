# Task 2: ROAL Core Services — Quantifier, Posture, History

## Context

You are implementing the Risk-Optimization Adaptive Loop (ROAL). This is Task 2 of 6 — core risk services.

**Prerequisite**: Task 1 has been completed. The `src/scripts/lib/risk/` subpackage exists with `types.py` and `serialization.py`.

Read these files first:
- `src/scripts/lib/risk/types.py` — all ROAL data types (RiskVector, RiskModifiers, StepClass, PostureProfile, etc.)
- `src/scripts/lib/risk/serialization.py` — JSON serialization helpers
- `src/scripts/lib/core/artifact_io.py` — read_json/write_json
- `src/scripts/lib/core/path_registry.py` — PathRegistry with risk accessors

## What to Create

### 1. `src/scripts/lib/risk/quantifier.py`

The risk quantification service. Computes raw risk scores from risk vectors and modifiers.

**Core function: `compute_raw_risk`**

```python
def compute_raw_risk(
    risk_vector: RiskVector,
    modifiers: RiskModifiers,
    step_class: StepClass,
    history_adjustment: float = 0.0,
) -> int:
```

The scoring model:
1. Start with weighted sum of primary risk severities (each 0-4, weighted by step class)
2. Apply modifier penalties:
   - High blast_radius (3-4) amplifies risk
   - Low reversibility (0-1) amplifies risk
   - Low observability (0-1) amplifies risk
3. Apply confidence penalty: lower confidence pushes score toward middle (uncertain)
4. Apply bounded history adjustment (positive = underestimated in past, negative = overestimated)
5. Clamp to 0-100 range

**Step-class-specific weights** (stored as constants, not in prompts):

Different step classes weight risks differently:
- `explore`: low weight on all risks (exploration is low-stakes)
- `stabilize`: medium weight, higher on silent_drift and scope_creep
- `edit`: high weight on brute_force_regression, cross_section_incoherence
- `coordinate`: highest weight on cross_section_incoherence, stale_artifact_contamination
- `verify`: medium weight, focused on observability

Use a `STEP_CLASS_WEIGHTS` dict mapping `StepClass` to a dict of `RiskType` to weight (float, 0.5-2.0 range).

**Default posture bands** (from design):
- 0-19: P0 direct
- 20-39: P1 light
- 40-59: P2 standard
- 60-79: P3 guarded
- 80-100: P4 reopen/block

**Default execution thresholds by step class**:
- explore/stabilize: execute if residual risk <= 60
- edit (local): execute if residual risk <= 45
- coordinate: execute if residual risk <= 35
- verify: execute if residual risk <= 50

Functions needed:
- `compute_raw_risk(...)` — as described above
- `risk_to_posture(raw_risk: int) -> PostureProfile` — map score to posture band
- `is_step_acceptable(raw_risk: int, step_class: StepClass) -> bool` — check against threshold
- `load_risk_parameters(path: Path) -> dict` — load custom thresholds from risk-parameters.json (fall back to defaults)

### 2. `src/scripts/lib/risk/posture.py`

Posture selection with oscillation prevention.

**Hysteresis bands**: Each posture boundary has a small window. Moving up requires decisively crossing the upper edge OR failure evidence. Moving down requires staying safely below the lower edge across multiple iterations.

**One-step rule**: Only move one posture level per iteration unless a hard invariant is violated.

**Asymmetric evidence**: Tightening can happen after one failure. Relaxing requires multiple successful similar cases.

**Cooldown**: After failure, posture cannot relax for a configurable number of iterations (default: 2).

Functions needed:
```python
def select_posture(
    raw_risk: int,
    current_posture: PostureProfile | None,
    recent_outcomes: list[str],   # ["success", "failure", ...]
    cooldown_remaining: int = 0,
) -> PostureProfile:
```

```python
def can_relax_posture(
    current_posture: PostureProfile,
    consecutive_successes: int,
    cooldown_remaining: int,
) -> bool:
```

```python
def apply_one_step_rule(
    current: PostureProfile,
    target: PostureProfile,
    has_invariant_breach: bool = False,
) -> PostureProfile:
```

### 3. `src/scripts/lib/risk/history.py`

Append-only risk history service.

The history file is `risk-history.jsonl` — one JSON object per line, append-only.

Functions needed:
```python
def append_history_entry(history_path: Path, entry: RiskHistoryEntry) -> None:
    """Append a single entry to the JSONL history file."""

def read_history(history_path: Path) -> list[RiskHistoryEntry]:
    """Read all history entries."""

def compute_history_adjustment(
    history_path: Path,
    step_class: StepClass,
    dominant_risks: list[RiskType],
    blast_radius_band: int,
) -> float:
    """Compute bounded adjustment based on similar past packages.

    Similarity is defined by matching step_class + overlapping dominant_risks + same blast_radius_band.

    Returns a bounded float (-10 to +10):
    - Positive means past packages were underestimated (actual worse than predicted)
    - Negative means past packages were overestimated (actual better than predicted)
    """

def pattern_signature(
    step_class: StepClass,
    dominant_risks: list[RiskType],
    blast_radius_band: int,
) -> str:
    """Create a stable pattern key for history matching."""
```

### 4. `src/scripts/lib/risk/engagement.py`

Determines whether the full ROAL loop is needed or can be skipped.

```python
def determine_engagement(
    step_count: int,
    file_count: int,
    has_shared_seams: bool,
    has_consequence_notes: bool,
    has_stale_inputs: bool,
    has_recent_failures: bool,
    has_tool_changes: bool,
    triage_confidence: str,        # "high" / "medium" / "low"
    freshness_changed: bool,
) -> RiskMode:
    """Determine risk engagement mode: skip, light, or full.

    Full loop when any of:
    - step_count > 1
    - file_count > 1
    - has_shared_seams
    - has_consequence_notes
    - has_stale_inputs
    - has_recent_failures
    - has_tool_changes
    - triage_confidence != "high"
    - freshness_changed

    Skip when ALL of:
    - step_count == 1
    - file_count <= 1
    - no shared seams, consequence notes, stale inputs, failures, tool changes
    - triage_confidence == "high"
    - not freshness_changed

    Light for anything in between (currently same as skip conditions —
    all must be true for skip, otherwise full).
    """
```

### 5. Tests

Create `tests/component/test_risk_quantifier.py`:
- Test compute_raw_risk with zero risk vector returns low score
- Test compute_raw_risk with max risk vector returns high score
- Test step class weights affect scoring (same vector, different class, different score)
- Test modifier penalties (high blast radius amplifies, high reversibility reduces)
- Test confidence penalty pushes uncertain scores toward middle
- Test history adjustment shifts score within bounds
- Test risk_to_posture maps to correct bands
- Test is_step_acceptable with different step classes and thresholds

Create `tests/component/test_risk_posture.py`:
- Test select_posture returns correct posture for risk score
- Test one-step rule prevents jumping more than one level
- Test cooldown prevents relaxation after failure
- Test asymmetric evidence (one failure tightens, multiple successes needed to relax)
- Test can_relax_posture with various inputs

Create `tests/component/test_risk_history.py`:
- Test append and read round-trip
- Test compute_history_adjustment with no history returns 0
- Test compute_history_adjustment with underestimated history returns positive
- Test compute_history_adjustment with overestimated history returns negative
- Test pattern_signature is stable and deterministic
- Test adjustment is bounded to -10..+10

Create `tests/component/test_risk_engagement.py`:
- Test single bounded step with high confidence returns SKIP
- Test multi-step package returns FULL
- Test shared seams trigger FULL
- Test stale inputs trigger FULL

## Important Rules

- Use `from __future__ import annotations` in every new file
- Follow the exact import patterns used in existing lib modules
- Import from `lib.risk.types` for all ROAL types
- No backwards compatibility layers
- No placeholder stubs — implement everything fully
- Keep scoring parameters as module-level constants, not hardcoded in prompts

## Verification

```bash
uv run pytest tests/component/test_risk_quantifier.py tests/component/test_risk_posture.py tests/component/test_risk_history.py tests/component/test_risk_engagement.py -v
```

All tests must pass.
