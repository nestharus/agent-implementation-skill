# Phase 11 Results

## Completed Extractions

- `src/scripts/lib/scan_related_files.py`
  - Moved `list_section_files`
  - Moved `apply_related_files_update`
  - Moved `_sha256_file`
  - Moved related-files validation flow as `validate_existing_related_files`
- `src/scripts/lib/scan_feedback_router.py`
  - Moved `_is_valid_updater_signal`
  - Moved `_validate_feedback_schema`
  - Moved `_extract_section_number`
  - Moved `_append_to_log`
  - Moved `_route_scope_deltas`
- `src/scripts/lib/substrate_prompt_builder.py`
  - Moved `write_shard_prompt`
  - Moved `write_pruner_prompt`
  - Moved `write_seeder_prompt`
- `src/scripts/lib/coordination_planner.py`
  - Moved `_parse_coordination_plan`
  - Moved `write_coordination_plan_prompt`
- `src/scripts/lib/coordination_problem_resolver.py`
  - Moved `build_file_to_sections`
  - Moved `_collect_outstanding_problems`
  - Moved `_detect_recurrence_patterns`

## Wiring

- Kept compatibility shims at:
  - `src/scripts/substrate/prompts.py`
  - `src/scripts/section_loop/coordination/planning.py`
  - `src/scripts/section_loop/coordination/problems.py`
- Updated internal callers to use the new lib modules where appropriate:
  - scan CLI/deep scan/exploration/feedback
  - substrate runner
  - coordination runner/main/package exports

## Tests Added

- `tests/component/test_scan_related_files.py`
- `tests/component/test_scan_feedback_router.py`
- `tests/component/test_substrate_prompt_builder.py`
- `tests/component/test_coordination_planner.py`
- `tests/component/test_coordination_problem_resolver.py`

## Verification

- Ran `uv run pytest tests/ -q --tb=short` after each extraction step
- Final full run: `1088 passed in 40.91s`

## Notes

- Behavior was kept stable by leaving the old module paths in place as thin re-export layers where existing tests and imports depended on them.
