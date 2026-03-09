# Task 6: ROAL End-to-End Tests and Verification

## Context

You are implementing the Risk-Optimization Adaptive Loop (ROAL). This is Task 6 of 6 — comprehensive test coverage and final verification.

**Prerequisite**: Tasks 1-5 have been completed. The full ROAL implementation exists in `src/scripts/lib/risk/` with integration hooks in the pipeline modules.

Read these files first:
- `src/scripts/lib/risk/` — all risk modules (types, serialization, quantifier, posture, history, engagement, package_builder, threshold, loop)
- `tests/conftest.py` — shared test fixtures
- `tests/component/test_risk_*.py` — existing risk tests from prior tasks
- `tests/integration/test_section_engine.py` — example integration test style
- `tests/integration/test_cross_section.py` — example integration test style

## What to Create

### 1. `tests/integration/test_risk_loop_integration.py`

End-to-end integration tests for the ROAL loop with realistic planspace fixtures.

Tests:
- **test_full_risk_loop_single_step**: Create a planspace with a proposal, build a single-step package, run the full risk loop with mocked agent dispatch (Risk Agent returns low-risk assessment, Tool Agent returns P0 accept). Verify the plan has one accepted step.

- **test_full_risk_loop_multi_step_with_defer**: Create a multi-step package where step 2 depends on step 1. Mock Risk Agent to return high risk on step 2. Verify step 1 is accepted, step 2 is deferred.

- **test_risk_loop_fallback_on_parse_failure**: Mock dispatch to return unparseable output. Verify the loop falls back to P4/reopen rather than crashing.

- **test_risk_loop_respects_threshold_enforcement**: Mock Tool Agent to accept a step with residual_risk above threshold. Verify threshold enforcement downgrades it to reject_defer.

- **test_risk_history_accumulates**: Run two risk loops sequentially. Verify risk-history.jsonl has entries from both runs.

- **test_lightweight_risk_check**: Run lightweight check on a simple package. Verify it returns a plan without dispatching the Tool Agent.

### 2. `tests/integration/test_risk_engagement.py`

Integration tests for engagement mode in realistic scenarios.

Tests:
- **test_trivial_single_file_edit_skips_roal**: Simulate a single-file, single-step, high-confidence scenario. Verify engagement returns SKIP.

- **test_multi_section_triggers_full**: Simulate a package touching multiple sections. Verify engagement returns FULL.

- **test_stale_inputs_trigger_full**: Simulate stale freshness tokens. Verify engagement returns FULL.

- **test_monitor_signals_trigger_full**: Simulate recent LOOP_DETECTED signals. Verify engagement returns FULL.

### 3. `tests/integration/test_risk_posture_convergence.py`

Tests for oscillation prevention and convergence behavior.

Tests:
- **test_posture_moves_one_step_at_a_time**: Starting at P3, with risk dropping to P1 range, verify only moves to P2 (not P1).

- **test_cooldown_prevents_immediate_relaxation**: After a failure, verify posture cannot relax for cooldown_iterations.

- **test_asymmetric_evidence**: One failure tightens immediately. Three successes needed to relax.

- **test_convergence_when_risk_below_threshold**: Verify loop terminates when residual risk is below threshold and further optimization yields no savings.

### 4. Comprehensive Component Test Coverage

Review all existing `tests/component/test_risk_*.py` files. Add any missing edge cases:

- **Serialization round-trips**: Every dataclass type should have a serialize→deserialize round-trip test
- **Quantifier edge cases**: all-zero risk vector, all-max risk vector, single dominant risk
- **History edge cases**: empty history file, corrupted JSONL line (skip gracefully), very large history
- **Package builder edge cases**: proposal without microstrategy, proposal with microstrategy, empty proposal

### 5. Full Suite Verification

Run the complete test suite and document results:

```bash
uv run pytest tests/ -q --tb=short 2>&1 | tail -20
```

Document:
- Total tests passed
- Any failures or errors
- Any skips

Write results to `.tmp/roal/test-results.md`.

## Important Rules

- Use `from __future__ import annotations` in every test file
- Use the existing `planspace` and `codespace` fixtures from conftest.py
- Mock `dispatch_agent` using the pattern in conftest.py's `mock_dispatch`
- Tests must be deterministic — no randomness, no timing dependencies
- Each test class should be independent and not depend on other test classes
- Use `tmp_path` for any file I/O

## Verification

```bash
uv run pytest tests/ -q --tb=short
```

ALL tests must pass — both existing and new ROAL tests.
