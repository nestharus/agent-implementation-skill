# R90 Task B: Implementation Surface Promotion + Step Taxonomy Restoration

## Context

Audit R90 found that ROAL frontier artifacts are hidden in a generic "Additional Inputs" bucket instead of being first-class implementation inputs, and that the five-class step taxonomy collapsed to positional EXPLORE/EDIT/VERIFY after R89 removed the semantic heuristics. This task restores both.

**Prerequisite**: Task A is complete and all tests pass.

Read these files first:
- `src/scripts/section_loop/prompts/context.py` — build_context, additional inputs (MODIFY)
- `src/scripts/section_loop/prompts/templates/strategic-implementation.md` — implementation template (MODIFY)
- `src/agents/implementation-strategist.md` — agent file (MODIFY, add risk awareness)
- `src/agents/microstrategy-writer.md` — agent file (MODIFY, add typed steps)
- `src/scripts/lib/risk/types.py` — StepClass enum (READ)
- `src/scripts/lib/risk/package_builder.py` — _materialize_steps, positional fallback (MODIFY)
- `src/scripts/lib/pipelines/implementation_pass.py` — _write_accepted_steps, .ref files (READ)
- `src/scripts/lib/core/path_registry.py` — PathRegistry (READ)

## What to Fix

### 1. Promote ROAL frontier artifacts into named implementation inputs (V3)

Currently `context.py` lines 174-200 puts ALL `.ref` files into a generic "Additional Inputs (from coordination)" heading. ROAL writes `.ref` files for accepted-steps, deferred, and reopen artifacts, but they're indistinguishable from coordination refs.

**Fix in `context.py`**: Split the `.ref` bucket into two groups:

```python
# Separate risk refs from coordination refs
risk_ref_prefixes = ("risk-accepted", "risk-deferred", "risk-reopen", "risk-advisory")
risk_refs = []
coordination_refs = []
for ref_file in ref_files:
    if any(ref_file.stem.startswith(prefix) for prefix in risk_ref_prefixes):
        risk_refs.append(ref_file)
    else:
        coordination_refs.append(ref_file)
```

Emit risk refs under a dedicated heading:
```python
if risk_refs:
    risk_inputs_block = (
        "\n\n## Risk Inputs (from ROAL)\n\n"
        "These artifacts were produced by the Risk-Optimization Adaptive Loop.\n"
        "The accepted frontier is your current local execution authority.\n"
        "Deferred steps are NOT in scope. Reopened steps are NOT locally solvable.\n"
    )
    for ref_file in risk_refs:
        referenced = ref_file.read_text(encoding="utf-8").strip()
        if Path(referenced).exists():
            risk_inputs_block += f"   - `{referenced}` (from {ref_file.stem})\n"
```

Keep the existing "Additional Inputs (from coordination)" for non-risk refs.

Thread the `risk_inputs_block` into the template via the context dict.

**Fix in `strategic-implementation.md`**: Add a section after "### Accuracy First" that explicitly names the risk boundary:

```markdown
### Risk Boundary

If a "Risk Inputs (from ROAL)" section appears above, it defines your
execution scope:

- **Accepted frontier**: these steps are the hard local authority. Execute
  only what they authorize.
- **Deferred steps**: these are NOT in your scope. Do not attempt them.
- **Reopened steps**: these are NOT locally solvable. Do not attempt them.
- **`dispatch_shape`**: this is follow-on topology metadata — it tells you
  the expected shape (chain/fanout/gate) but does NOT grant permission to
  widen your scope.

If no risk inputs are present, proceed normally per the integration
proposal.
```

Also update the "Accuracy First" section to replace "zero risk tolerance" with the converged language: "zero tolerance for fabricated understanding or bypassed safety gates; operational risk is managed proportionally by ROAL."

**Fix in `implementation-strategist.md`**: Add to the agent's inputs section that when ROAL artifacts are present, the accepted frontier bounds local authority. Add to the "What you do NOT do" section that the strategist does not widen scope beyond accepted frontier.

### 2. Restore step taxonomy via agent-authored typed steps (V6)

The step taxonomy has 5 classes (explore, stabilize, edit, coordinate, verify) with different thresholds, but after R89 only EXPLORE/EDIT/VERIFY are emitted by the positional fallback.

**The correct fix is NOT to reintroduce script-side semantic classification.** Instead, let the microstrategy writer author typed steps.

**Fix in `microstrategy-writer.md`**: The microstrategy writer already outputs step plans. Extend the output schema to include an optional `step_class` field:

Add to the agent file's output format documentation:

```markdown
Each step in your microstrategy may include an optional `step_class` to
communicate execution intent to ROAL:

- `explore` — refresh understanding, read artifacts, narrow unknowns
- `stabilize` — resolve blocking state (missing readiness, stale inputs)
- `edit` — implement approved changes
- `coordinate` — resolve cross-section seams or shared contracts
- `verify` — confirm alignment, run checks

Example step with typed class:
```json
{
  "summary": "Resolve shared contract with section-05 before editing",
  "step_class": "coordinate"
}
```

If you omit `step_class`, a positional default is used (first=explore,
last=verify, middle=edit). But for non-trivial strategies, explicit typing
helps ROAL apply appropriate risk thresholds.
```

**Fix in `package_builder.py`**: In `build_package_from_proposal()`, when reading microstrategy artifacts, look for typed `step_class` fields on each step. Update `_materialize_steps()` to accept an optional mapping of step index → step_class from the microstrategy:

```python
def _materialize_steps(
    *,
    step_summaries: list[str],
    proposal_state: dict,
    step_classes: dict[int, str] | None = None,  # NEW: from microstrategy
) -> list[PackageStep]:
```

In the loop, use the microstrategy-provided class if present, fall back to positional:

```python
if step_classes and index in step_classes:
    raw_class = step_classes[index]
    try:
        step_class = StepClass(raw_class)
    except ValueError:
        step_class = _positional_step_class(index, total)
else:
    step_class = _positional_step_class(index, total)
```

Extract the current positional logic into `_positional_step_class(index, total) -> StepClass`.

In `build_package_from_proposal()`, when reading the microstrategy, extract step classes:

```python
step_classes = {}
if microstrategy_steps:
    for i, step in enumerate(microstrategy_steps, start=1):
        if isinstance(step, dict) and "step_class" in step:
            step_classes[i] = step["step_class"]
```

This way `STABILIZE` and `COORDINATE` become reachable when the microstrategy writer explicitly types them, without any script-side keyword matching.

### 3. Tests

Update `tests/component/test_risk_package_builder.py`:
- Test that microstrategy-provided step_class is consumed
- Test that STABILIZE and COORDINATE are reachable via explicit step_class
- Test that positional fallback still works when no step_class provided
- Test that invalid step_class falls back to positional

Create or update tests for context building:
- Test that risk refs are separated from coordination refs
- Test that risk refs appear under "Risk Inputs (from ROAL)" heading
- Test that non-risk refs still appear under "Additional Inputs (from coordination)"
- Test that when no risk refs exist, no risk inputs block appears

## Important Rules

- Use `from __future__ import annotations` in every new file
- No script-side semantic heuristics — step_class comes from structured agent output only
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes: `uv run pytest tests/ -q --tb=short`
- All tests must pass

## Verification

```bash
uv run pytest tests/ -q --tb=short
```
