# Task 5: ROAL Integration — Hook into Existing Pipelines

## Context

You are implementing the Risk-Optimization Adaptive Loop (ROAL). This is Task 5 of 6 — integration with existing pipeline modules.

**Prerequisite**: Tasks 1-4 have been completed. The `src/scripts/lib/risk/` subpackage is fully implemented with types, serialization, quantifier, posture, history, engagement, package_builder, threshold, and loop modules.

Read these files first:
- `src/scripts/lib/risk/types.py` — all ROAL data types
- `src/scripts/lib/risk/loop.py` — ROAL loop orchestrator
- `src/scripts/lib/risk/engagement.py` — engagement mode determination
- `src/scripts/lib/risk/package_builder.py` — package building
- `src/scripts/lib/pipelines/implementation_pass.py` — implementation pass (MODIFY)
- `src/scripts/lib/pipelines/proposal_pass.py` — proposal pass (MODIFY)
- `src/scripts/lib/pipelines/coordination_loop.py` — coordination loop (MODIFY)
- `src/scripts/lib/intent/intent_triage.py` — intent triage (MODIFY)
- `src/scripts/lib/repositories/strategic_state.py` — strategic state (MODIFY)
- `src/scripts/lib/core/path_registry.py` — PathRegistry
- `src/scripts/lib/services/readiness_resolver.py` — readiness service
- `src/scripts/lib/dispatch/agent_executor.py` — agent dispatch
- `src/scripts/section_loop/dispatch.py` — dispatch_agent function
- `tests/conftest.py` — see mock_dispatch fixture pattern

## What to Modify

### 1. `src/scripts/lib/pipelines/implementation_pass.py`

This is the primary ROAL integration point. Insert ROAL between readiness check and implementation dispatch.

Add a `_run_risk_review` helper function:

```python
def _run_risk_review(
    planspace: Path,
    sec_num: str,
    section: Section,
    dispatch_fn: Callable,
) -> RiskPlan | None:
    """Run ROAL risk review for a section before implementation.

    Returns the risk plan, or None if ROAL is skipped (engagement mode = SKIP).
    """
```

In `run_implementation_pass`, before calling `run_section` for each section:
1. Import from `lib.risk.engagement` and `lib.risk.loop`
2. Determine engagement mode using `determine_engagement`
3. If FULL or LIGHT: build package from proposal, run risk loop, get plan
4. If plan has no accepted steps (all rejected): skip implementation for this section, log why
5. If plan has accepted steps: proceed with implementation as before
6. After implementation completes: append risk history entry with actual outcome

The risk review should be wrapped in a try/except so that ROAL failures don't block implementation — if ROAL fails, fall back to standard execution with a warning log.

### 2. `src/scripts/lib/pipelines/proposal_pass.py`

Read this file first. Add optional ROAL check before proposal finalization.

After proposal is generated but before marking execution-ready:
1. If ROAL engagement is FULL: assess whether exploration depth is sufficient
2. If risk assessment shows high brute_force_regression or silent_drift: recommend additional exploration
3. This is advisory — it enriches the proposal pass log but does not block

Add a helper:
```python
def _risk_check_proposal(
    planspace: Path,
    sec_num: str,
    dispatch_fn: Callable,
) -> dict | None:
    """Optional risk pre-check on a proposal before finalization.

    Returns a summary dict with risk_mode, dominant_risks, and recommendation,
    or None if ROAL is skipped.
    """
```

### 3. `src/scripts/lib/intent/intent_triage.py`

Read this file first. Extend the intent triage output to include ROAL hints.

The intent triage already produces intent mode, confidence, and cycle budgets. Add:
- `risk_mode`: derived from triage confidence and section complexity
- `risk_confidence`: mirrors triage confidence
- `risk_budget_hint`: bounded allowance for extra mitigation cycles (default: 0 for high confidence, 2 for medium, 4 for low)
- `posture_floor`: None by default, set if history warrants minimum posture

These fields should be added to the triage output dict. They are hints that downstream ROAL consumers can use.

### 4. `src/scripts/lib/repositories/strategic_state.py`

Read this file first. Extend the strategic state snapshot to include a risk posture summary.

Add to `build_strategic_state`:
- Read risk assessments from `PathRegistry.risk_dir()` for each section
- Add to snapshot:
  - `risk_posture`: dict mapping section number to current posture (P0-P4)
  - `dominant_risks_by_section`: dict mapping section number to list of dominant risk types
  - `blocked_by_risk`: list of sections where all steps are rejected

These additions should be graceful — if no risk artifacts exist, these fields are empty dicts/lists.

### 5. Update `tests/conftest.py`

Add the risk-assessor and execution-optimizer dispatch mock targets to `mock_dispatch`:

```python
monkeypatch.setattr("lib.risk.loop.dispatch_agent", mock)  # if loop imports dispatch_agent
```

Actually, since `loop.py` takes `dispatch_fn` as a parameter, no mock patching is needed for it. But if any integration module imports dispatch_agent and passes it to the loop, those import sites need mocking.

Check what new dispatch sites are introduced and add them to mock_dispatch.

### 6. Tests

Create `tests/component/test_risk_integration.py`:
- Test `_run_risk_review` with mocked dispatch returns valid plan
- Test `_run_risk_review` with SKIP engagement returns None
- Test `_run_risk_review` failure falls back gracefully (no exception)
- Test `_risk_check_proposal` with mocked dispatch
- Test strategic state includes risk fields when risk artifacts exist
- Test strategic state works normally when no risk artifacts exist

Update `tests/component/test_implementation_pass.py` (if it exists):
- Verify existing tests still pass
- Add test that ROAL is invoked when engagement conditions are met

Update `tests/component/test_strategic_state.py` (if it exists):
- Verify existing tests still pass with new risk fields

## Important Rules

- Use `from __future__ import annotations` in every new file
- Import from `lib.risk.*` for ROAL modules
- ROAL integration must be ADDITIVE — do not change existing function signatures
- ROAL failures must not break existing pipelines — wrap in try/except with fallback
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes to verify nothing is broken

## Verification

```bash
uv run pytest tests/ -q --tb=short
```

All existing tests must still pass. New tests must pass too.
