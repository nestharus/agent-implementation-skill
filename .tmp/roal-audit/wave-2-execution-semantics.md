# Wave 2: Execution Semantics — Make ROAL Mechanically Executable and Fail-Closed

## Context

Wave 1 fixed contract truthfulness (prompt handling, agent frontmatter, semantic heuristics, doctrine). This wave makes ROAL outputs actually drive execution and makes it fail-closed.

**Prerequisite**: Wave 1 is complete and all tests pass.

Read these files first:
- `src/scripts/lib/risk/types.py` — RiskPlan, StepMitigation, StepDecision (READ)
- `src/scripts/lib/risk/loop.py` — run_risk_loop, run_lightweight_risk_check (READ)
- `src/scripts/lib/pipelines/implementation_pass.py` — _run_risk_review, main loop (MODIFY)
- `src/scripts/lib/pipelines/proposal_pass.py` — _risk_check_proposal (MODIFY)
- `src/scripts/lib/core/path_registry.py` — PathRegistry (READ)
- `src/scripts/lib/core/artifact_io.py` — write_json (READ)
- `src/scripts/lib/dispatch/agent_executor.py` — dispatch_agent signature (READ)
- `src/scripts/lib/services/signal_reader.py` — if it exists, for signal patterns (READ)
- `src/scripts/lib/pipelines/coordination_loop.py` — for understanding routing patterns (READ)
- `src/scripts/lib/repositories/strategic_state.py` — build_strategic_state (READ)
- `tests/conftest.py` — mock_dispatch pattern (READ)

## What to Fix

### 1. Make RiskPlan mechanically executable (Violation V2)

Currently `implementation_pass.py` lines 296-321 only checks whether `accepted_frontier` is empty or not, then runs the whole section unchanged if non-empty. The rich `RiskPlan` contract (step decisions, deferred steps, reopened steps, `wait_for`, `route_to`, `dispatch_shape`) is ignored.

**Fix**: After ROAL returns a plan, translate it into runtime obligations:

#### 1a. Accepted frontier drives partial execution

When `risk_plan.accepted_frontier` is non-empty, write a `{scope}-risk-accepted-steps.json` artifact to the section's artifact directory containing:
```json
{
  "accepted_steps": ["explore-01", "edit-02"],
  "posture": "P2",
  "mitigations": ["alignment check after edit", "monitor on multi-file work"]
}
```

This artifact is available for downstream consumers (implementation-strategist reads it as part of its context). The implementation pass itself does not need to interpret posture — it just gates execution.

#### 1b. Deferred steps become parked work

When `risk_plan.deferred_steps` is non-empty, write a `{scope}-risk-deferred.json` artifact:
```json
{
  "deferred_steps": ["verify-03"],
  "wait_for": ["edit-02 output"],
  "reassessment_inputs": ["modified-file-manifest", "alignment-check-result"]
}
```

This tells the next risk loop iteration what to reassess. The implementation pass logs this but does not block — deferred steps simply don't run yet.

#### 1c. Reopen steps emit structured routing

When `risk_plan.reopen_steps` is non-empty, write a `{scope}-blocker.json` artifact (the existing blocker signal format) and route upward:
```json
{
  "blocker_type": "risk_reopen",
  "source": "roal",
  "steps": ["coordinate-04"],
  "route_to": "coordination",
  "reason": "cross-section incoherence requires reconciliation before local execution"
}
```

The implementation pass should skip the section when reopen steps exist AND no accepted steps exist. If some steps are accepted and others reopened, proceed with accepted frontier only.

#### 1d. Dispatch shape passthrough

`StepMitigation.dispatch_shape` (dict with chain/fanout/gate primitives) should be written into the accepted-steps artifact so downstream consumers can use it. The implementation pass does not interpret it — the implementation-strategist agent reads it.

### 2. Make proposal-pass ROAL actionable (Violation V1)

Currently `proposal_pass.py` lines 268-279 only log the risk pre-check result.

**Fix**: When the risk pre-check returns high risk on `brute_force_regression` or `silent_drift`:
- Write a `{scope}-risk-advisory.json` artifact with the risk summary
- If dominant risks include `brute_force_regression` with severity >= 3 or `silent_drift` with severity >= 3, write a blocker signal recommending additional exploration before implementation
- This is still advisory — it does not block proposal finalization, but it creates a structured artifact that downstream consumers can read

Keep this simple. The existing log line stays. Just add the artifact write.

### 3. Make ROAL fail-closed (Violation V9)

Currently `implementation_pass.py` lines 162-167 catch all exceptions from `_run_risk_review()` and continue with standard implementation. This means the safety layer disappears under pressure.

**Fix**: Change `_run_risk_review()` error handling:
- If ROAL review fails (exception), do NOT continue with standard implementation
- Instead, write a `{scope}-blocker.json` with `blocker_type: "risk_review_failure"` and `reason: str(exc)`
- Skip the section's implementation
- Log the failure clearly

The only case where None is returned (meaning "proceed without risk plan") is when engagement mode is SKIP. All other paths must produce a valid plan or block.

Update the calling code in `run_implementation_pass()`:
```python
risk_plan = _run_risk_review(planspace, sec_num, section, dispatch_agent)
if risk_plan is None:
    # Engagement mode = SKIP, proceed normally
    pass
elif not risk_plan.accepted_frontier:
    # All steps rejected — skip section
    log(...)
    continue
elif risk_plan.reopen_steps and not risk_plan.accepted_frontier:
    # Structural reopen — skip section, emit blocker
    ...
    continue
else:
    # Write accepted-steps artifact for downstream
    _write_accepted_steps(planspace, sec_num, risk_plan)
```

### 4. Post-execution history enrichment

Currently `_append_risk_history()` only records "success" or "warning" as outcomes.

**Fix**: Enrich history outcomes:
- When steps are deferred: record `"deferred"` outcome for those steps
- When steps are reopened: record `"reopened"` outcome
- When implementation fails after risk plan: record `"failure"` outcome
- Keep existing "success"/"warning" for completed accepted steps

This provides richer history for future risk assessments.

### 5. Tests

Update `tests/component/test_implementation_pass.py` (or create if absent):
- Test that ROAL failure blocks section (fail-closed, not fail-open)
- Test that accepted frontier writes accepted-steps artifact
- Test that deferred steps write deferred artifact
- Test that reopen steps write blocker artifact
- Test that engagement SKIP proceeds without risk artifacts

Update `tests/component/test_risk_integration.py`:
- Test proposal-pass risk advisory artifact is written for high-risk proposals
- Test proposal-pass proceeds without blocking even on high risk

Update existing ROAL tests to match new behavior:
- Any test that expects `_run_risk_review` to return None on exception must be updated to expect blocker behavior

## Important Rules

- Use `from __future__ import annotations` in every new file
- ROAL outputs map onto existing primitives (blocker signals, artifacts, logging) — do NOT invent new runtime constructs
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes: `uv run pytest tests/ -q --tb=short`
- All tests must pass

## Verification

```bash
uv run pytest tests/ -q --tb=short
```
