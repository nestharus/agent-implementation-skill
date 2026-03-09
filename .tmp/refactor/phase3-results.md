## Phase 3 Results

Refactoring Phase 3 completed without changing the public `section_loop` call sites.

### Extracted modules

- `src/scripts/lib/model_policy.py`
  - Added `ModelPolicy` as a mapping-compatible dataclass.
  - Added `load_model_policy(planspace)` and `resolve(policy, key)`.
  - Kept `section_loop.dispatch.read_model_policy()` as a compatibility wrapper.

- `src/scripts/lib/signal_reader.py`
  - Moved structured signal parsing and fail-closed signal classification out of `section_loop.dispatch`.
  - Kept existing `read_agent_signal()` and `read_signal_tuple()` import paths working via `dispatch.py`.

- `src/scripts/lib/dispatch_metadata.py`
  - Centralized `.meta.json` path construction plus read/write helpers.
  - `section_loop.dispatch` now writes metadata through the new helper.
  - `task_dispatcher._read_dispatch_meta()` remains as a compatibility wrapper over the shared reader.

- `src/scripts/lib/context_sidecar.py`
  - Moved frontmatter parsing, category resolution, and sidecar materialization out of `section_loop.context_assembly`.
  - `section_loop.context_assembly` now re-exports the existing public and test-touched names.

- `src/scripts/lib/prompt_template.py`
  - Centralized prompt wrapper constraints, template loading, and simple template rendering.
  - `section_loop.agent_templates` and `section_loop.prompts.renderer` now act as thin compatibility facades.

### Tests added

- `tests/component/test_model_policy.py`
- `tests/component/test_signal_reader.py`
- `tests/component/test_dispatch_metadata.py`
- `tests/component/test_context_sidecar.py`
- `tests/component/test_prompt_template.py`

### Verification

- Ran `uv run pytest tests/ -q --tb=short`
- Result: `932 passed`
