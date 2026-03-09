# Wave 3: Adaptive Refinement — Wire Up ROAL Feedback Loop

## Context

Waves 1-2 fixed contract truthfulness and execution semantics. This wave wires up the adaptive behavior that currently exists as dead code: LIGHT engagement mode, history adjustment, posture hysteresis, and risk hint consumption.

**Prerequisite**: Waves 1-2 are complete and all tests pass.

Read these files first:
- `src/scripts/lib/risk/engagement.py` — determine_engagement (MODIFY)
- `src/scripts/lib/risk/posture.py` — select_posture, can_relax_posture, apply_one_step_rule (READ, then CONSUME)
- `src/scripts/lib/risk/history.py` — append/read, compute_history_adjustment, pattern_signature (READ, then CONSUME)
- `src/scripts/lib/risk/quantifier.py` — compute_raw_risk, risk_to_posture, is_step_acceptable (READ, then CONSUME)
- `src/scripts/lib/risk/loop.py` — run_risk_loop, run_lightweight_risk_check (MODIFY)
- `src/scripts/lib/risk/threshold.py` — enforce_thresholds (READ)
- `src/scripts/lib/risk/types.py` — all types (READ)
- `src/scripts/lib/pipelines/implementation_pass.py` — _run_risk_review, _append_risk_history (MODIFY)
- `src/scripts/lib/intent/intent_triage.py` — risk_mode, risk_budget_hint, posture_floor output (READ)

## What to Fix

### 1. Make LIGHT engagement mode reachable (Violation V3)

Currently `engagement.py` only returns SKIP or FULL. The `RiskMode.LIGHT` enum value exists but is unreachable.

**Fix**: Add LIGHT as a middle tier in `determine_engagement()`:

```python
def determine_engagement(
    step_count: int,
    file_count: int,
    has_shared_seams: bool,
    has_consequence_notes: bool,
    has_stale_inputs: bool,
    has_recent_failures: bool,
    has_tool_changes: bool,
    triage_confidence: str,
    freshness_changed: bool,
    risk_mode_hint: str = "",    # NEW: from intent triage
) -> RiskMode:
```

Logic:
- SKIP: single step, single file, no seams/notes/stale/failures/tool changes, high confidence, no freshness change (existing logic)
- FULL: any of: has_shared_seams, has_consequence_notes, has_stale_inputs, has_recent_failures, file_count > 3, step_count > 3, triage confidence "low"
- LIGHT: everything else (moderate complexity without high-risk signals)

Also respect `risk_mode_hint` from intent triage:
- If hint is "full", return FULL regardless of other signals
- If hint is "skip", still apply the safety floor (don't skip if stale/failures/seams)
- If hint is "light" or empty, use computed logic

Update callers in `implementation_pass.py` to pass `risk_mode_hint` from the triage signal.

### 2. Consume risk hints from intent triage (Violation V3)

`intent_triage.py` emits `risk_mode`, `risk_confidence`, `risk_budget_hint`, and `posture_floor` but nothing downstream consumes them.

**Fix in `implementation_pass.py`'s `_run_risk_review()`**:

Read the triage signal and pass relevant hints:
- `risk_mode` → pass as `risk_mode_hint` to `determine_engagement()`
- `posture_floor` → pass to `run_risk_loop()` (see below)
- `risk_budget_hint` → pass as `max_iterations` override to `run_risk_loop()` (add to default 5, capped at 9)

### 3. Wire up history adjustment in the risk loop (Violation V3)

`history.py` has `compute_history_adjustment()` and `pattern_signature()` but they are unused in the runtime.

**Fix in `loop.py`'s `run_risk_loop()`**:

After the Risk Agent returns an assessment:
1. Compute `pattern_signature()` for the current package
2. Call `compute_history_adjustment()` with the history entries matching that signature
3. Apply the adjustment to the assessment's `package_raw_risk` (bounded by `history_adjustment_bound` from parameters)
4. Use the adjusted risk for posture selection

This means history of similar packages influences the risk score: repeated failures on similar work increase risk, repeated successes decrease it.

### 4. Wire up posture hysteresis in the risk loop (Violation V3)

`posture.py` has `select_posture()`, `can_relax_posture()`, and `apply_one_step_rule()` but they are unused.

**Fix in `loop.py`**:

After the Tool Agent returns a plan:
1. For each step decision, if the step has a posture:
   - Look up the previous posture from history (if any)
   - Apply `apply_one_step_rule()` to prevent jumps > 1 posture level
   - Apply `can_relax_posture()` to enforce cooldown and asymmetric evidence
2. If `posture_floor` was provided from intent triage, ensure no step goes below that floor

This prevents oscillation: the system can tighten quickly (one failure → tighter) but relaxes slowly (multiple successes required).

### 5. Enrich history outcomes (Violation V3)

Currently `_append_risk_history()` in `implementation_pass.py` only appends after successful implementation with "success" or "warning".

**Fix**: Append history entries for ALL outcomes:
- `"success"` — implementation completed, files modified
- `"warning"` — implementation completed but no files modified
- `"deferred"` — steps were deferred (append for each deferred step)
- `"reopened"` — steps were reopened/blocked (append for each reopened step)
- `"failure"` — implementation failed (exception during run_section)
- `"over_guarded"` — step was rejected but retrospectively could have been safe (computed on next successful similar pattern)
- `"risk_review_failure"` — ROAL itself failed (from Wave 2's fail-closed path)

This enriches the history signal for future risk assessments and posture selection.

### 6. Trigger reassessment on expected inputs (Violation V3)

`RiskPlan.expected_reassessment_inputs` exists but is never checked.

**Fix in `implementation_pass.py`**: After implementation completes for accepted-frontier steps:
1. Read the deferred artifact (`{scope}-risk-deferred.json`) if it exists
2. Check if `reassessment_inputs` are now available (e.g., "modified-file-manifest" exists, "alignment-check-result" exists)
3. If reassessment inputs are satisfied and deferred steps exist, re-run ROAL for the deferred steps only
4. This is bounded: at most one reassessment per implementation pass per section

Implement this as a simple check-and-rerun, not a recursive loop.

### 7. Tests

Update `tests/component/test_risk_engagement.py`:
- Test LIGHT is reachable with moderate complexity
- Test risk_mode_hint="full" forces FULL
- Test risk_mode_hint="skip" still respects safety floor
- Test boundary between LIGHT and FULL

Create or update `tests/component/test_risk_adaptive.py`:
- Test history adjustment modifies assessment risk score
- Test posture hysteresis prevents >1 step jumps
- Test cooldown prevents immediate relaxation
- Test posture_floor enforcement

Update `tests/component/test_risk_loop.py`:
- Test that history adjustment is applied after assessment
- Test that posture hysteresis is applied after plan

Update `tests/integration/test_risk_loop_integration.py`:
- Test full adaptive cycle: high risk → P3 → success → next run risk lower → P2 (not P1)
- Test reassessment triggers when deferred inputs become available

Update `tests/component/test_implementation_pass.py`:
- Test enriched history outcomes (deferred, reopened, failure entries)

## Important Rules

- Use `from __future__ import annotations` in every new file
- The adaptive behavior must use existing helper functions in posture.py, history.py, quantifier.py — do NOT reimplement
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes: `uv run pytest tests/ -q --tb=short`
- All tests must pass

## Verification

```bash
uv run pytest tests/ -q --tb=short
```
