# Phase 17 Results

Final status: complete.

Extracted modules:
- `src/scripts/lib/impact_triage.py`
- `src/scripts/lib/problem_frame_gate.py`
- `src/scripts/lib/intent_bootstrap.py`
- `src/scripts/lib/reconciliation_phase.py`
- `src/scripts/lib/global_alignment_recheck.py`
- `src/scripts/lib/coordination_loop.py`

Wiring updates:
- `src/scripts/section_loop/section_engine/runner.py` now delegates the impact triage, problem-frame gate, and intent bootstrap phases to `lib/` services.
- `src/scripts/section_loop/main.py` now delegates reconciliation, Phase 2 global alignment recheck, and the coordination loop to `lib/` services.
- `tests/conftest.py` was updated to patch the new dispatch import sites used by the extracted runner services.

New component tests:
- `tests/component/test_impact_triage.py`
- `tests/component/test_problem_frame_gate.py`
- `tests/component/test_intent_bootstrap.py`
- `tests/component/test_reconciliation_phase.py`
- `tests/component/test_global_alignment_recheck.py`
- `tests/component/test_coordination_loop.py`

Verification:
- `uv run pytest tests/ -q --tb=short`
- Result: `1208 passed`

Final line counts:
- `src/scripts/section_loop/section_engine/runner.py`: `428`
- `src/scripts/section_loop/main.py`: `221`
