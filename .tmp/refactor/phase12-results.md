# Phase 12 Results

## Completed

- Extracted `AlignmentService` into `src/scripts/lib/alignment_service.py`.
  `collect_modified_files()` now lives in the lib module, and alignment verdict-to-problem mapping moved into `extract_problems()`. Dispatch/adjudication retry flow stayed in `src/scripts/section_loop/alignment.py`.

- Extracted `DispatchHelpers` into `src/scripts/lib/dispatch_helpers.py`.
  `summarize_output()`, `write_model_choice_signal()`, and `check_agent_signals()` moved intact and are now imported by `src/scripts/section_loop/dispatch.py`.

- Extracted task-ingestion parsing helpers into `src/scripts/lib/task_ingestion.py`.
  Safe flow-signal consumption, legacy task extraction, and first section-scope detection moved into the lib module. Dispatch and queue-submission orchestration stayed in `src/scripts/section_loop/task_ingestion.py`.

- Extracted reusable log-extract utilities into `src/scripts/lib/log_extract_utils.py`.
  Moved `parse_timestamp()`, `prompt_signature()`, `infer_section()`, and `summarize_text()`, then rewired `src/scripts/log_extract/utils.py` to import them.

- Extracted scan dispatch configuration/routing helpers into `src/scripts/lib/scan_dispatch.py`.
  Scan model policy loading, scan agent-path resolution, and `agents` command construction moved out of `src/scripts/scan/dispatch.py`. Subprocess execution and stdout/stderr capture stayed in the scan stage module.

## Explicit Skips

- `src/scripts/log_extract/correlator.py` stayed in place.
  Its scoring thresholds and model-family compatibility rules are pipeline-specific correlation policy, not general shared helpers.

- `src/scripts/log_extract/timeline.py` stayed in place.
  Its functions operate directly on `TimelineEvent` streams and remain specific to timeline assembly/filtering behavior.

- `src/scripts/log_extract/formatters.py` stayed in place.
  Its functions are output-policy code for timeline rendering rather than generic shared utilities.

## Tests

- Added component coverage:
  `tests/component/test_alignment_service.py`
  `tests/component/test_dispatch_helpers.py`
  `tests/component/test_task_ingestion.py`
  `tests/component/test_log_extract_utils.py`
  `tests/component/test_scan_dispatch.py`

- Verification:
  `uv run pytest tests/ -q --tb=short`
  Result: `1111 passed`
