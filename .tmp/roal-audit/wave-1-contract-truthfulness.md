# Wave 1: Contract Truthfulness — Fix ROAL Split-Brain Surfaces

## Context

An audit of the ROAL (Risk-Optimization Adaptive Loop) implementation found contract violations where the risk subsystem diverges from established codebase conventions. This wave fixes 3 categories: script-side semantic heuristics, prompt/context handling, and model policy.

Read these files first:
- `src/scripts/lib/risk/package_builder.py` — step class inference (MODIFY)
- `src/scripts/lib/risk/loop.py` — prompt building (MODIFY)
- `src/scripts/lib/dispatch/context_sidecar.py` — VALID_CATEGORIES and _resolve_codemap (READ, understand the pattern)
- `src/scripts/prompt_safety.py` — validate_dynamic_content / write_validated_prompt (READ, understand the contract)
- `src/agents/risk-assessor.md` — context frontmatter (MODIFY)
- `src/agents/execution-optimizer.md` — context frontmatter (MODIFY)
- `src/scripts/lib/core/model_policy.py` — ModelPolicy dataclass (MODIFY)
- `src/SKILL.md` — lines 114-122 about zero risk (MODIFY)
- `src/implement.md` — lines 134-139 about zero risk, and Stage 5 around lines 1074-1136 (MODIFY)
- `src/models.md` — model inventory (MODIFY)
- `evals/harness.py` — eval harness (READ)
- `evals/scenarios/` — existing scenarios (READ, understand the pattern)

## What to Fix

### 1. Remove script-side semantic heuristics from package_builder.py (Violation V4)

The function `_infer_step_class()` at lines 252-266 classifies step meaning from summary keywords like "coordinate", "verify", "align", "refresh". This is script-side semantic interpretation — the exact pattern the codebase history repeatedly removed.

**Fix**: Replace `_infer_step_class()` with a position-based default:
- If only 1 step: `StepClass.EDIT`
- If multiple steps: first step = `EXPLORE`, last step = `VERIFY`, middle steps = `EDIT`
- Remove all keyword matching from this function

The step class is a default. The Risk Agent (which reads the step summaries) can reclassify during assessment. Scripts must not interpret meaning from free text.

Update `_materialize_steps()` accordingly. Update any tests in `tests/component/test_risk_package_builder.py` that test `_infer_step_class` keyword behavior.

### 2. Fix ROAL prompt/context handling (Violations V5, V6, V7, V8)

#### 2a. Switch from inline content to path-based artifact references

In `loop.py`, `build_risk_assessment_prompt()` (lines 247-315) and `build_optimization_prompt()` (lines 318-356) inline large artifact bodies into the prompt. The established contract is that prompts provide artifact paths for agents to read.

**Fix `build_risk_assessment_prompt()`**: Instead of reading and inlining artifact content, provide a path manifest:

```python
## Artifact Paths

Read these artifacts for context:

- Section spec: `{path}`
- Proposal excerpt: `{path}`
- Alignment excerpt: `{path}`
- Problem frame: `{path}`
- Microstrategy: `{path}`
- Proposal state: `{path}`
- Readiness: `{path}`
- Tool registry: `{path}`
- Codemap: `{path}`
- Risk history: `{path}`
- Monitor signals directory: `{path}`
- Consequence notes: `{path1}`, `{path2}`, ...
- Impact artifacts: `{path1}`, `{path2}`, ...
```

Only the risk package itself (which is specific to this dispatch) should be inlined as JSON.

**Fix `build_optimization_prompt()`**: Same approach — inline only the assessment and package (dispatch-specific), provide paths for tool registry, risk history, and risk parameters.

Keep the `_artifact_block` and `_json_block` helper functions but convert them to emit path references instead of content. Only `_inline_json_block` for dispatch-specific payloads.

#### 2b. Use shared prompt-safety validation

`loop.py` uses `_write_prompt()` (line 545-548) which writes directly without safety validation.

**Fix**: Replace `_write_prompt()` with calls to `write_validated_prompt()` from `prompt_safety.py`. If validation fails, the loop should fall back to P4/reopen (fail-closed), not continue.

Import and use:
```python
from prompt_safety import write_validated_prompt
```

Remove the `_write_prompt()` helper entirely.

#### 2c. Fix agent file context frontmatter

`risk-assessor.md` declares `context: risk_package, risk_history, codemap, tool_registry`.
`execution-optimizer.md` declares `context: risk_assessment, risk_history, tool_registry, risk_parameters`.

None of these are valid in `VALID_CATEGORIES` in `context_sidecar.py`. The sidecar silently drops them.

**Fix**: Update agent file frontmatter to use only categories that exist in `VALID_CATEGORIES`, OR remove the `context:` block entirely since ROAL prompts are built explicitly by `loop.py` (not by the sidecar). Since the prompts are custom-built, removing `context:` is the correct fix — it avoids a false contract claim.

#### 2d. Use canonical codemap with corrections

`build_risk_assessment_prompt()` reads raw `codemap.md` (line 286-288) instead of using `_resolve_codemap()` which bundles corrections.

**Fix**: Use the same codemap resolution approach as `context_sidecar.py:89-106`. Since we're now providing paths (per 2a), provide the codemap path and also the corrections path if it exists:

```
- Codemap: `artifacts/codemap.md`
- Codemap corrections: `artifacts/signals/codemap-corrections.json` (authoritative overrides)
```

### 3. Add risk policy keys to ModelPolicy (Violation V10 partial)

`model_policy.py` has no explicit risk keys.

**Fix**: Add two new fields to `ModelPolicy`:
```python
risk_assessor: str = "gpt-5.4-high"
execution_optimizer: str = "gpt-5.4-high"
```

Update `loop.py`'s `_risk_assessor_model()` and any equivalent optimizer model lookup to use `resolve(policy, "risk_assessor")` / `resolve(policy, "execution_optimizer")` instead of any hardcoded model names.

### 4. Update doctrine documents (Violation V10)

#### 4a. SKILL.md (lines 114-122)

The current text says "zero risk tolerance" and "no meaningful risk exists" for pipeline bypasses. This conflates two distinct claims:
1. Zero tolerance for fabricated understanding or bypassed safety gates (CORRECT, keep)
2. Zero operational risk in all execution (INCORRECT, risk-below-threshold is the actual model)

**Fix**: Reword to distinguish:
- Keep the absolute zero tolerance for fabricated understanding, bypassed safeguards, and pipeline shortcuts
- Add that operational execution uses proportional guardrails: the ROAL parallel loop scales effort to actual risk, keeping residual risk below threshold rather than eliminating all risk

Do NOT add an agent inventory or list of risk agents. The existing convention is that `agents/` directory IS the inventory.

#### 4b. implement.md (lines 134-139, ~1074-1136)

Same zero-risk language fix as SKILL.md. Also add the risk loop to Stage 5 description:
- Between readiness check and implementation dispatch, ROAL assesses risk and produces an execution plan
- Only accepted-frontier steps proceed; deferred steps wait; reopened steps route upward
- Risk review failure blocks descent (fail-closed)

Keep this brief — a paragraph or two, not a full ROAL specification.

#### 4c. models.md

Add `risk_assessor` and `execution_optimizer` to the model policy documentation. Describe them as:
- risk_assessor: diagnostic agent assessing execution risk before descent
- execution_optimizer: translates risk assessment into minimum effective execution posture

### 5. Add live eval scenarios for risk agents (Violation V10 partial)

Read `evals/harness.py` and existing scenarios (e.g., `evals/scenarios/reexplorer.py`) to understand the pattern.

Create `evals/scenarios/risk_assessor.py`:
- Scenario with a proposal package, verify the risk agent produces valid RiskAssessment JSON
- Scenario with high-risk indicators (multiple sections, stale inputs), verify assessment flags dominant risks

Create `evals/scenarios/execution_optimizer.py`:
- Scenario with a risk assessment, verify optimizer produces valid RiskPlan JSON
- Scenario with high residual risk, verify optimizer recommends P3+ or reopen

Follow the exact pattern of existing scenarios. These are bounded live-eval scenarios dispatching real agents.

### 6. Tests

Update `tests/component/test_risk_package_builder.py`:
- Update tests for `_infer_step_class` removal — test that position-based defaults work
- Verify single-step → EDIT, multi-step → EXPLORE/EDIT.../VERIFY

Update `tests/component/test_risk_loop.py`:
- Update prompt-building tests to verify path-manifest format instead of inline content
- Add test that prompt safety validation is called (mock `write_validated_prompt`)
- Add test that safety violation produces fallback P4 plan

Update `tests/component/test_risk_integration.py` or relevant test files:
- Verify ModelPolicy has risk_assessor and execution_optimizer fields

## Important Rules

- Use `from __future__ import annotations` in every new file
- No backwards compatibility layers
- No placeholder stubs
- Run the FULL test suite after changes: `uv run pytest tests/ -q --tb=short`
- All tests must pass

## Verification

```bash
uv run pytest tests/ -q --tb=short
```
