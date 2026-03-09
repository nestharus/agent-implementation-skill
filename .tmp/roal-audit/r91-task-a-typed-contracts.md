# R91 Task A: Typed Contracts — Intent→ROAL Handoff + ROAL Input Index

## Context

Audit R91 found that the intent→ROAL handoff has script-side strategic classification leaking back in, ROAL artifacts are misclassified by filename-prefix logic in the prompt builder, and stale ROAL artifacts are never cleaned up. This task fixes all three.

Read these files first:
- `src/scripts/lib/intent/intent_triage.py` — _augment_risk_hints (MODIFY)
- `src/agents/intent-triager.md` — agent file output contract (MODIFY)
- `src/scripts/lib/risk/engagement.py` — determine_engagement, `light` hint handling (MODIFY)
- `src/scripts/lib/risk/types.py` — IntentRiskHint, RiskMode (READ)
- `src/scripts/lib/pipelines/implementation_pass.py` — _write_accepted_steps, _write_deferred_steps, _write_reopen_blocker, _load_risk_hints (MODIFY)
- `src/scripts/lib/pipelines/proposal_pass.py` — proposal advisory writing (MODIFY)
- `src/scripts/section_loop/prompts/context.py` — ref classification logic (MODIFY)
- `src/scripts/lib/core/path_registry.py` — PathRegistry (READ)
- `src/scripts/lib/repositories/note_repository.py` — read_incoming_notes pattern (READ)
- `evals/scenarios/intent_triager.py` — eval scenario (MODIFY if needed)
- `tests/conftest.py` — mock_dispatch (READ)

## What to Fix

### 1. Make intent→ROAL handoff a real typed contract (V1, V2)

Currently `_augment_risk_hints()` in `intent_triage.py` (lines 267-311) computes `risk_mode`, `risk_confidence`, and `risk_budget_hint` from local heuristics (complexity scoring based on file count, note count, solve count, intent mode). This is script-side strategic classification.

The `intent-triager.md` agent file does NOT include these fields in its output contract. So the prompt asks nothing about risk, the agent outputs nothing about risk, and the script fabricates risk classification.

**Fix**:

**Step 1**: Update `agents/intent-triager.md` to include risk fields in the output schema:
```json
{
  "intent_mode": "full | lightweight | cached",
  "confidence": "high | medium | low",
  "risk_mode": "skip | light | full",
  "risk_budget_hint": 0
}
```

Add a brief section explaining:
- `risk_mode`: your assessment of how much ROAL scrutiny this section needs based on the section's problem structure, complexity, and history
- `risk_budget_hint`: extra iteration budget (0 for simple, 2-4 for complex/uncertain)

**Step 2**: Update `_augment_risk_hints()` to consume the agent's output instead of recomputing:
- If the triage result contains `risk_mode`, use it directly
- If the triage result contains `risk_budget_hint`, use it directly
- `risk_confidence` should mirror `confidence` from triage (no recomputation needed)
- Remove the complexity_score computation and the risk_mode heuristic logic
- Keep `posture_floor` as a separate script-owned mechanical computation from history (this is correctly script-owned since it's a pure function of append-only history)

The function should become essentially:
```python
def _augment_risk_hints(
    triage: dict,
    section_number: str,
    planspace: Path,
    **_kwargs: object,
) -> dict:
    result = dict(triage)
    # risk_mode and risk_budget_hint come from the agent's output
    # Only set defaults if the agent didn't provide them
    result.setdefault("risk_mode", "full")
    result.setdefault("risk_budget_hint", 0)
    result.setdefault("risk_confidence", result.get("confidence", "low"))
    # posture_floor is script-owned (mechanical history computation)
    result["posture_floor"] = _derive_posture_floor(section_number, planspace)
    return result
```

**Step 3**: Update `determine_engagement()` to honor `light` as a first-class hint (V3):
```python
if normalized_hint == RiskMode.LIGHT.value:
    return RiskMode.FULL if skip_floor_hit else RiskMode.LIGHT
```

Currently `light` falls through to the computed logic. It should be respected as an authoritative preference (with safety floor override).

### 2. Replace ROAL filename inference with typed ROAL input index (V4, V5)

Currently `context.py` classifies ROAL refs by checking if `.ref` file stems start with `risk-accepted`, `risk-deferred`, etc. But actual artifact names are `section-07-risk-accepted-steps.json`, so stems of the `.ref` files are like `section-07-risk-accepted-steps` which does NOT start with `risk-accepted`. This means ALL ROAL refs fall into the generic "Additional Inputs" bucket.

**Fix**:

**Step 1**: Create a ROAL input index writer. Add a helper in `implementation_pass.py`:

```python
def _write_roal_input_index(
    planspace: Path,
    sec_num: str,
    entries: list[dict],
) -> Path:
    """Write a typed ROAL input index for a section.

    Each entry: {"kind": "accepted_frontier"|"deferred"|"reopen"|"proposal_advisory",
                 "path": str, "produced_by": str}
    """
    paths = PathRegistry(planspace)
    index_path = paths.input_refs_dir(sec_num) / f"section-{sec_num}-roal-input-index.json"
    write_json(index_path, entries)
    return index_path
```

**Step 2**: After writing ROAL artifacts in `run_implementation_pass()`, write (or refresh) the index:
- When accepted steps are written: add `{"kind": "accepted_frontier", "path": str(accepted_artifact)}`
- When deferred steps are written: add `{"kind": "deferred", "path": str(deferred_artifact)}`
- When reopen blocker is written: add `{"kind": "reopen", "path": str(blocker_path)}`
- On reassessment: refresh the entire index with updated paths
- This atomically replaces the old index, cleaning up stale entries

Do the same in `proposal_pass.py` for advisory artifacts.

**Step 3**: Update `context.py` to read the ROAL input index instead of prefix-matching:
```python
# Read ROAL input index if it exists
roal_index_path = inputs_dir / f"section-{sec}-roal-input-index.json"
roal_index = read_json(roal_index_path)
if isinstance(roal_index, list):
    for entry in roal_index:
        if isinstance(entry, dict) and "path" in entry:
            ref_path = Path(entry["path"])
            if ref_path.exists():
                kind = entry.get("kind", "unknown")
                risk_lines.append(
                    f"   - `{ref_path}` ({kind})"
                )
```

Remove the filename-prefix classification logic entirely. Non-ROAL refs (coordination, bridge notes, etc.) continue to be read from `.ref` files as before.

**Step 4**: Clean up stale ROAL `.ref` files. When writing the ROAL input index, also remove any old ROAL-related `.ref` files that are no longer in the index. The `.ref` files for ROAL artifacts were written by `_write_section_input_artifact()` — they should be cleaned up when the index is refreshed.

### 3. Tests

Update `tests/component/test_risk_engagement.py`:
- Test that `light` hint is honored as first-class (returns LIGHT when no safety floor hit)
- Test that `light` hint with safety floor returns FULL

Update intent triage tests:
- Test that `_augment_risk_hints` passes through agent-provided risk_mode
- Test that `_augment_risk_hints` defaults to "full" when agent omits risk_mode
- Test that complexity_score heuristic is no longer used

Update implementation pass tests:
- Test that ROAL input index is written with correct entries
- Test that index is refreshed on reassessment
- Test that stale entries are removed on refresh

Update context/prompt tests:
- Test that ROAL input index is consumed for risk inputs block
- Test that generic refs still go to coordination block
- Test that missing index falls back gracefully (no risk inputs block)

## Important Rules

- Use `from __future__ import annotations` in every new file
- No script-side strategic classification — agent owns risk_mode, script owns posture_floor
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes: `uv run pytest tests/ -q --tb=short`
- All tests must pass

## Verification

```bash
uv run pytest tests/ -q --tb=short
```
