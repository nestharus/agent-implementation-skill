# Phase 4 Results

## Scope

- Wired `PathRegistry` through remaining `section_loop` and non-`section_loop` consumers that were still constructing `planspace / "artifacts" / ...` paths directly.
- Expanded `src/scripts/lib/path_registry.py` to cover the artifact families used by the current codebase, including corrected existing accessors that had drifted from the on-disk layout (`codemap.md`, `signals/codemap-corrections.json`, section excerpt/problem-frame paths).
- Extracted section input hashing into `src/scripts/lib/section_input_hasher.py` and updated `section_loop/pipeline_control.py` to delegate to it while preserving the `_section_inputs_hash` compatibility alias.

## Verification

- `uv run pytest tests/ -q --tb=short`
- Final pass: `962 passed`

## Notes

- A follow-up grep for direct `planspace / "artifacts"` constructions across the targeted script set now only hits documentation strings in `readiness.py`, `reconciliation.py`, and `reconciliation_queue.py`.
- Added component coverage for the new hasher module in `tests/component/test_section_input_hasher.py`.
