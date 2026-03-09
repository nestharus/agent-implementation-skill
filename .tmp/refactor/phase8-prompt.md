# Refactoring Phase 8: Reconciliation Detectors + Intent Services + Prompt Writers

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (29 total, all wired)
### Test counts: 1017 passed

## Phase 8 Tasks

### Task 1: Extract AnchorOverlapDetector + ContractConflictDetector from reconciliation.py

Read `src/scripts/section_loop/reconciliation.py`. Find `_detect_anchor_overlaps` and `_detect_contract_conflicts` (or similar detection functions).

Create `src/scripts/lib/reconciliation_detectors.py`:
- `detect_anchor_overlaps(states: dict[str, dict]) -> list[dict]`
- `detect_contract_conflicts(states: dict[str, dict]) -> list[dict]`
- Any other pure detection functions from reconciliation.py (section candidate consolidation, seam aggregation)

These are pure analysis functions (no I/O, no dispatch). Wire into `reconciliation.py`.

Write tests at `tests/component/test_reconciliation_detectors.py`.

### Task 2: Extract ReconciliationResultRepository from reconciliation.py

Read `src/scripts/section_loop/reconciliation.py`. Find the code that writes reconciliation results, scope deltas, and substrate triggers to JSON files.

Create `src/scripts/lib/reconciliation_result_repository.py`:
- `write_result(planspace, section_number, result: dict)`
- `write_scope_delta(planspace, scope_delta: dict)`
- `write_substrate_trigger(planspace, trigger: dict)`
- `load_result(planspace, section_number) -> dict | None`
- `was_section_affected(planspace, section_number) -> bool`

Wire into `reconciliation.py`.

Write tests at `tests/component/test_reconciliation_result_repository.py`.

### Task 3: Extract IntentSurfaceService from intent/expansion.py

Read `src/scripts/section_loop/intent/expansion.py`. Find the surface expansion logic — the code that generates intent packs and tool surface documents for sections.

Create `src/scripts/lib/intent_surface.py`:
- Move the per-section intent surface generation/expansion logic
- Keep the dispatch calls but structure them cleanly

Wire into `intent/expansion.py`.

Write tests at `tests/component/test_intent_surface.py`.

### Task 4: Extract PhilosophyBootstrap from intent/bootstrap.py

Read `src/scripts/section_loop/intent/bootstrap.py`. Find the global philosophy bootstrap logic — the code that discovers philosophy sources and generates the global philosophy artifact.

Create `src/scripts/lib/philosophy_bootstrap.py`:
- Move `_walk_md_bounded` utility
- Move the philosophy source discovery and distillation logic
- Move the source manifest/map generation

Wire into `intent/bootstrap.py`.

Write tests at `tests/component/test_philosophy_bootstrap.py`.

### Task 5: Extract PromptWriterHelpers from prompts/writers.py

Read `src/scripts/section_loop/prompts/writers.py`. Identify reusable prompt construction helpers that could be shared across multiple prompt writers.

Create `src/scripts/lib/prompt_helpers.py`:
- Move any pure prompt formatting functions (e.g., building context blocks, file listings, etc.)
- Move any utility functions that format data for prompts

Wire into `prompts/writers.py`.

Write tests at `tests/component/test_prompt_helpers.py`.

## Process for each extraction

1. Read the source file(s) thoroughly
2. Identify the concern boundary
3. Create the new module in `src/scripts/lib/`
4. Write component tests in `tests/component/`
5. Update the original file to import from the new module
6. Update any other files that imported from the original
7. Run `uv run pytest tests/ -q --tb=short` — must pass at each step

## Rules

- Do NOT change any behavior
- Test after EVERY change
- Write component tests for every new module
- Do not create abstractions that are more complex than the code they replace
- If a module's code is too tightly coupled to dispatch calls (LLM agent invocations) to extract cleanly, note it in your results and skip to the next task

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase8-results.md`.
