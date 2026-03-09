# R91 Task B: Bounded Frontier-Execution Loop

## Context

Audit R91 found that reassessed deferred work is discovered but not consumed — the runtime can find that previously deferred steps are now safe, but it does not dispatch another implementation slice for them. This task turns reassessment into a bounded multi-slice frontier-execution loop.

**Prerequisite**: Task A is complete and all tests pass.

Read these files first:
- `src/scripts/lib/pipelines/implementation_pass.py` — run_implementation_pass, _maybe_reassess_deferred_steps, _write_accepted_steps, _write_deferred_steps (MODIFY)
- `src/scripts/lib/risk/loop.py` — run_risk_loop (READ)
- `src/scripts/lib/risk/types.py` — RiskPlan, RiskPackage (READ)
- `src/scripts/section_loop/section_engine/runner.py` — run_section (READ, understand the dispatch interface)
- `src/scripts/lib/core/path_registry.py` — PathRegistry (READ)

## What to Fix

### 1. Convert reassessment into bounded frontier-execution loop (V6)

Currently in `run_implementation_pass()` (lines ~835-876), after initial implementation completes:
1. `_maybe_reassess_deferred_steps()` can return a new `RiskPlan` with accepted steps
2. But the code only rewrites accepted/deferred artifacts and logs the result
3. It does NOT dispatch another implementation slice for newly accepted deferred steps
4. The section is recorded as aligned/implemented regardless

**Fix**: After initial implementation + reassessment, if reassessment produces newly accepted steps, dispatch a fresh bounded implementation slice:

```python
# After initial implementation succeeds for the initial accepted frontier...
frontier_iteration = 0
max_frontier_iterations = 3  # bounded
current_risk_plan = risk_plan

while frontier_iteration < max_frontier_iterations:
    frontier_iteration += 1

    # Write modified-file manifest
    manifest_path = _write_modified_file_manifest(planspace, sec_num, all_modified_files)

    # Try to reassess deferred steps
    reassessed_plan = _maybe_reassess_deferred_steps(
        planspace, sec_num, dispatch_agent, current_risk_plan,
    )
    if reassessed_plan is None:
        break  # no reassessment possible
    if not reassessed_plan.accepted_frontier:
        break  # nothing newly accepted

    # Write updated ROAL artifacts
    _write_accepted_steps(planspace, sec_num, reassessed_plan)
    if reassessed_plan.deferred_steps:
        _write_deferred_steps(planspace, sec_num, reassessed_plan)
    if reassessed_plan.reopen_steps:
        _write_reopen_blocker(planspace, sec_num, reassessed_plan)

    # Refresh ROAL input index
    _refresh_roal_input_index(planspace, sec_num, reassessed_plan)

    log(
        f"Section {sec_num}: dispatching deferred frontier slice "
        f"(iteration {frontier_iteration}, "
        f"accepted={len(reassessed_plan.accepted_frontier)})",
    )

    # Dispatch a fresh implementation slice for newly accepted work
    deferred_modified = run_section(
        planspace,
        codespace,
        section,
        parent,
        all_sections=list(sections_by_num.values()),
        pass_mode="implementation",
    )

    if deferred_modified is None:
        log(f"Section {sec_num}: deferred frontier slice returned None")
        _append_risk_history(
            planspace, sec_num, reassessed_plan, None,
            implementation_failed=True,
        )
        break

    # Accumulate modified files
    if deferred_modified:
        all_modified_files.extend(deferred_modified)

    _append_risk_history(planspace, sec_num, reassessed_plan, list(deferred_modified or []))
    current_risk_plan = reassessed_plan

    # Check stop conditions
    if not reassessed_plan.deferred_steps:
        break  # no more deferred steps
    if reassessed_plan.reopen_steps:
        break  # structural reopen, stop local iteration

    # Check alignment change
    if _check_and_clear_alignment_changed(planspace):
        log("Alignment changed during deferred frontier execution — restarting")
        raise ImplementationPassRestart
```

**Important constraints**:
- Each slice is a fresh bounded dispatch (same `run_section` call as the initial implementation)
- Maximum 3 frontier iterations (use a constant, not configurable)
- Stop on: no reassessment, no newly accepted steps, no remaining deferred steps, reopen outcome, alignment change, or iteration cap
- Do NOT create new workflow primitives or queue semantics — this stays inside the thick-script orchestration model
- Append risk history for each slice independently

### 2. Update section result finalization

Currently after initial implementation, the section is marked as implemented. With the frontier loop, update the finalization to reflect the cumulative result:
- All accumulated modified files should be included
- If any frontier iteration failed, mark appropriately
- The section result should reflect the final state of deferred/reopened steps

### 3. Tests

Update `tests/component/test_implementation_pass.py`:
- Test that reassessed accepted frontier triggers another run_section dispatch
- Test bounded iteration: mock 4 consecutive reassessments, verify only 3 iterations run
- Test stop condition: no newly accepted steps → loop terminates
- Test stop condition: reopen outcome → loop terminates
- Test stop condition: no deferred steps remaining → loop terminates
- Test that all frontier slices contribute to risk history
- Test that modified files accumulate across frontier iterations
- Test that alignment change during frontier execution raises ImplementationPassRestart

Update `tests/integration/test_risk_loop_integration.py`:
- Test end-to-end: initial ROAL accepts step 1, defers step 2 → step 1 executes → reassessment accepts step 2 → step 2 executes → section done

## Important Rules

- Use `from __future__ import annotations` in every new file
- Keep each implementation slice bounded and short-lived
- No new workflow primitives or queue semantics
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes: `uv run pytest tests/ -q --tb=short`
- All tests must pass

## Verification

```bash
uv run pytest tests/ -q --tb=short
```
