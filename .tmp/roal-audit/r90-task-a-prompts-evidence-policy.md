# R90 Task A: ROAL Prompt Path Completion, Evidence Collector, QA Policy

## Context

Audit R90 found that ROAL prompts still inline dispatch-local JSON that already has durable artifact form, consequence-note collection uses wrong filename patterns, reassessment evidence is invisible to the risk assessor, and the QA interceptor bypasses centralized model policy.

Read these files first:
- `src/scripts/lib/risk/loop.py` — prompt builders (MODIFY)
- `src/scripts/lib/risk/package_builder.py` — write_package (READ)
- `src/scripts/lib/repositories/note_repository.py` — canonical note filename pattern (READ)
- `src/scripts/lib/pipelines/implementation_pass.py` — reassessment inputs, modified-file-manifest (READ)
- `src/scripts/lib/core/path_registry.py` — PathRegistry (READ)
- `src/agents/risk-assessor.md` — what the agent expects to read (READ)
- `src/agents/execution-optimizer.md` — what the agent expects to read (READ)
- `src/scripts/qa_interceptor.py` — hardcoded model (MODIFY)
- `src/scripts/lib/core/model_policy.py` — ModelPolicy (MODIFY)
- `src/models.md` — model policy docs (MODIFY)
- `tests/conftest.py` — mock_dispatch (READ)

## What to Fix

### 1. Stop inlining package and assessment into ROAL prompts (V1, V2)

In `loop.py`, `build_risk_assessment_prompt()` (line ~309) uses `_inline_json_block("Risk Package", serialize_package(package))`. But the package has already been written as an artifact by `write_package(paths, package)` on line ~51 of `run_risk_loop()`.

**Fix**: Replace the inline package JSON block with a path reference to the already-written artifact:
```python
lines.extend([
    f"- Risk package: `{paths.risk_package(scope)}`",
])
```

The risk-assessor agent file says it reads the current package artifact from its provided path. So just provide the path.

Similarly, in `build_optimization_prompt()` (lines ~370-371), the assessment and package are both inlined via `_inline_json_block()`. But:
- The assessment has already been written to `paths.risk_assessment(scope)` (line ~84 of `run_risk_loop()`)
- The package is at `paths.risk_package(scope)`

**Fix**: Replace both inline blocks with path references:
```python
lines.extend([
    f"- Risk assessment: `{paths.risk_assessment(scope)}`",
    f"- Risk package: `{paths.risk_package(scope)}`",
])
```

The `scope` parameter must be threaded into `build_optimization_prompt()` — add it as a parameter.

After this change, `_inline_json_block()` should have no callers in the prompt builders. You can keep the helper (it may be used in tests) or remove it.

### 2. Fix consequence-note glob pattern (V5)

In `build_risk_assessment_prompt()`, line ~351:
```python
consequence_paths = list(paths.notes_dir().glob(f"*{scope}*"))
```

This uses `*section-03*` but canonical note filenames are `from-<source>-to-<target>.md` (see `note_repository.py:11-24`). A scope like `section-03` doesn't match `from-12-to-03.md`.

**Fix**: Use the canonical pattern from `read_incoming_notes()`:
```python
section_number = _scope_number(scope)
consequence_paths = sorted(paths.notes_dir().glob(f"from-*-to-{section_number}.md"))
```

Also add outgoing notes for context:
```python
outgoing_paths = sorted(paths.notes_dir().glob(f"from-{section_number}-to-*.md"))
```

Include both in the prompt as path lists.

### 3. Create canonical ROAL evidence collector (V4)

The deferred-step reassessment in `implementation_pass.py` waits for `modified-file-manifest` and `alignment-check-result`, but `build_risk_assessment_prompt()` never surfaces these artifact paths.

**Fix**: Add a helper function in `loop.py` that collects all section-scoped evidence ROAL should see:

```python
def _collect_roal_evidence(paths: PathRegistry, scope: str, section_number: str) -> list[tuple[str, Path]]:
    """Collect all section-scoped evidence artifacts for ROAL prompts.

    Returns list of (title, path) pairs for artifacts that exist.
    """
    evidence = []

    # Modified-file manifest (produced after accepted frontier executes)
    manifest_path = paths.input_refs_dir(section_number) / f"section-{section_number}-modified-file-manifest.json"
    if manifest_path.exists():
        evidence.append(("Modified-file manifest", manifest_path))

    # Alignment check result
    align_result = paths.artifacts / f"impl-align-{section_number}-output.md"
    if align_result.exists():
        evidence.append(("Alignment check result", align_result))

    # Reconciliation results
    for recon in sorted(paths.reconciliation_dir().glob(f"*{scope}*")):
        evidence.append(("Reconciliation result", recon))

    # Risk artifacts already produced for this section
    for risk_artifact_name in [f"{scope}-risk-accepted-steps.json", f"{scope}-risk-deferred.json"]:
        risk_path = paths.input_refs_dir(section_number) / risk_artifact_name
        if risk_path.exists():
            evidence.append(("Previous risk artifact", risk_path))

    return evidence
```

Use this helper in `build_risk_assessment_prompt()` to append evidence paths:
```python
evidence = _collect_roal_evidence(paths, scope, section_number)
if evidence:
    lines.append("")
    lines.append("## Reassessment Evidence")
    lines.append("")
    for title, path in evidence:
        lines.append(f"- {title}: `{path}`")
```

This way, when ROAL runs as a reassessment (after accepted frontier completes), the risk assessor can see what actually happened.

### 4. QA interceptor under model policy (V7)

`qa_interceptor.py` line ~277 hardcodes `"claude-opus"`.

**Fix**:
1. Add `qa_interceptor: str = "claude-opus"` to `ModelPolicy` in `model_policy.py`
2. In `qa_interceptor.py`, import and use `load_model_policy` + `resolve`:
```python
from lib.core.model_policy import load_model_policy, resolve
...
policy = load_model_policy(planspace)
model = resolve(policy, "qa_interceptor")
output = dispatch_agent(
    model,
    ...
)
```
3. Add `qa_interceptor` to the model policy documentation in `models.md`

### 5. Tests

Update `tests/component/test_risk_loop.py`:
- Update prompt-building tests: verify package/assessment are path references, not inline JSON
- Verify `_inline_json_block` is NOT called from prompt builders
- Test consequence-note glob uses canonical pattern
- Test evidence collector finds modified-file-manifest and alignment-check-result when present
- Test evidence collector returns empty list when no evidence artifacts exist

Update `tests/component/test_model_policy.py`:
- Verify `qa_interceptor` field exists with correct default

Update any QA interceptor tests if they exist:
- Verify model is resolved through policy, not hardcoded

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
