# Refactoring Phase 15: Decompose substrate/runner.py and scan/deep_scan.py

You are orchestrating the decomposition of two large agent-dispatch files.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize. In particular, `src/scripts/section_loop/` files may be modified by another agent — do NOT touch files inside `section_loop/`.

## Completed So Far

### lib/ modules (66 total, all wired)
### Test counts: 1142 passed

## Phase 15 Tasks

### Task 1: Extract SubstrateModelPolicy from substrate/runner.py

Read `src/scripts/substrate/runner.py` lines 37-131. This handles model policy reading, trigger signal reading, and trigger threshold reading — all configuration/policy concerns.

Create `src/scripts/lib/substrate_policy.py`:
- Move `_DEFAULT_MODELS`, `_DEFAULT_TRIGGER_THRESHOLD` constants
- `read_substrate_model_policy(artifacts_dir) -> dict[str, str]` (from `_read_model_policy`)
- `read_trigger_signals(artifacts_dir) -> list[str]` (from `_read_trigger_signals`)
- `read_trigger_threshold(artifacts_dir) -> int` (from `_read_trigger_threshold`)

Wire into `substrate/runner.py`.

Write tests at `tests/component/test_substrate_policy.py`.

### Task 2: Extract SubstrateDispatch from substrate/runner.py

Read `src/scripts/substrate/runner.py` lines 135-211. This is `_dispatch_agent` — a substrate-specific agent dispatch wrapper.

Create `src/scripts/lib/substrate_dispatch.py`:
- `dispatch_substrate_agent(model, prompt_path, output_path, codespace, agent_file) -> bool`
  Contains the subprocess dispatch, timeout handling, and output writing.

Wire into `substrate/runner.py`.

Write tests at `tests/component/test_substrate_dispatch.py`.

### Task 3: Extract SubstrateHelpers from substrate/runner.py

Read `src/scripts/substrate/runner.py` lines 213-316. These are pure helpers:
- `_read_project_mode(artifacts_dir)` — reads project mode from signals
- `_list_section_files(sections_dir)` — lists section markdown files
- `_section_number(path)` — extracts section number from filename
- `_count_existing_related(section_path)` — counts related file entries
- `_write_status(artifacts_dir, **kwargs)` — writes substrate status JSON

Create `src/scripts/lib/substrate_helpers.py`:
- Move all 5 functions as public functions

Wire into `substrate/runner.py`.

Write tests at `tests/component/test_substrate_helpers.py`.

### Task 4: Extract TierRanking from scan/deep_scan.py

Read `src/scripts/scan/deep_scan.py` lines 31-321. This contains `validate_tier_file` and `_run_tier_ranking` — the tier-ranking dispatch logic.

Create `src/scripts/lib/tier_ranking.py`:
- `validate_tier_file(tier_file) -> bool`
- `run_tier_ranking(section_file, section_name, related_files, codespace, artifacts_dir, scan_log_dir, model_policy) -> Path | None`

Wire into `scan/deep_scan.py`.

Write tests at `tests/component/test_tier_ranking.py`.

### Task 5: Extract DeepScanFileAnalyzer from scan/deep_scan.py

Read `src/scripts/scan/deep_scan.py` lines 355-508. This is `_analyze_file` — the per-file deep analysis dispatch with caching, prompt construction, and feedback routing.

Create `src/scripts/lib/deep_scan_analyzer.py`:
- `analyze_file(section_file, section_name, source_file, codespace, codemap_path, corrections_path, scan_log_dir, file_card_cache, model_policy) -> bool`
  Contains the full single-file analysis: cache check, prompt writing, dispatch, feedback extraction.
- Move `_safe_name(source_file)` as `safe_name(source_file)` (used by analyze_file)

Wire into `scan/deep_scan.py`.

Write tests at `tests/component/test_deep_scan_analyzer.py`.

### Task 6: Extract ScanSectionIterator from scan/deep_scan.py

Read `src/scripts/scan/deep_scan.py` lines 510-604. This is `_scan_sections` — the per-section iteration that combines tier ranking + per-file analysis.

Create `src/scripts/lib/scan_section_iterator.py`:
- `scan_sections(section_files, codemap_path, codespace, artifacts_dir, scan_log_dir, file_card_cache, corrections_path, model_policy, already_scanned) -> bool`

Wire into `scan/deep_scan.py`.

Write tests at `tests/component/test_scan_section_iterator.py`.

### Task 7: Extract UpdateMatch from scan/deep_scan.py

Read `src/scripts/scan/deep_scan.py` lines 73-186. This is `_safe_name`, `update_match`, and `deep_scan_related_files` — annotation helpers.

Since `_safe_name` goes to Task 5, extract only:

Create `src/scripts/lib/scan_match_updater.py`:
- `update_match(section_file, source_file, details_file) -> bool`
- `deep_scan_related_files(section_file) -> list[str]`

Wire into `scan/deep_scan.py`.

Write tests at `tests/component/test_scan_match_updater.py`.

## Process for each extraction

1. Read the relevant code
2. Extract into a new lib/ module
3. Write component tests
4. Replace inline code with function call
5. Run `uv run pytest tests/ -q --tb=short` — must pass

## Rules

- Do NOT change any behavior
- Do NOT touch files inside `section_loop/` (another agent is working there)
- Test after EVERY extraction
- substrate/runner.py and scan/deep_scan.py should become thin orchestrators

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase15-results.md` including final line counts.
