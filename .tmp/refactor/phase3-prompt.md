# Refactoring Phase 3: Domain Types + Tier 4-5 Components

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### Tier 1 Components (created + wired):
- `src/scripts/lib/artifact_io.py` — JSON read/write ✓ WIRED
- `src/scripts/lib/hash_service.py` — Canonical hashing ✓ WIRED
- `src/scripts/lib/path_registry.py` — Path construction (created, NOT wired)
- `src/scripts/lib/verdict_parsers.py` — Alignment verdict parsing ✓ WIRED
- `src/scripts/lib/database_client.py` — db.sh wrapper ✓ WIRED
- `src/scripts/lib/mailbox_service.py` — Mailbox lifecycle ✓ WIRED
- `src/scripts/lib/monitor_service.py` — Monitor lifecycle ✓ WIRED

## Phase 3 Tasks

### Task 1: Extract ModelPolicy domain type (Tier 3)

Currently model policy is a raw `dict[str, str]` read from `model-policy.json` with `.get()` chains everywhere. Read these files to understand:

```bash
grep -rn "read_model_policy\|model.policy\|policy\[" src/scripts/ --include="*.py" | head -30
```

Look at how `read_model_policy` works in `src/scripts/section_loop/dispatch.py` and how callers use the returned dict.

Create `src/scripts/lib/model_policy.py`:
- A `ModelPolicy` dataclass with all known policy keys as fields with defaults
- A `load_model_policy(planspace: Path) -> ModelPolicy` function
- A `resolve(policy: ModelPolicy, key: str) -> str` function for lookup

Write tests at `tests/component/test_model_policy.py`.

### Task 2: Extract SignalReader (Tier 4)

Read `src/scripts/section_loop/dispatch.py` to find the `read_agent_signal` / signal reading logic. Extract into `src/scripts/lib/signal_reader.py`.

Write tests at `tests/component/test_signal_reader.py`.

### Task 3: Extract DispatchMetadataService (Tier 4)

Read `src/scripts/section_loop/dispatch.py` and `src/scripts/task_dispatcher.py` for `.meta.json` sidecar reading/writing. Extract into `src/scripts/lib/dispatch_metadata.py`.

Write tests at `tests/component/test_dispatch_metadata.py`.

### Task 4: Extract ContextSidecarService (Tier 5)

Read `src/scripts/section_loop/context_assembly.py`. This module handles agent context JSON file materialization. Extract pure resolution logic into `src/scripts/lib/context_sidecar.py`.

Write tests at `tests/component/test_context_sidecar.py`.

### Task 5: Extract PromptTemplateService (Tier 5)

Read `src/scripts/section_loop/agent_templates.py` and `src/scripts/section_loop/prompts/renderer.py`. Extract template loading/rendering into `src/scripts/lib/prompt_template.py`.

Write tests at `tests/component/test_prompt_template.py`.

## Process for each extraction

1. Read the source file(s) thoroughly
2. Identify the concern boundary — what data and operations belong together
3. Create the new module in `src/scripts/lib/`
4. Write component tests in `tests/component/`
5. Update the original file to import from the new module
6. Run `uv run pytest tests/ -q --tb=short` — must pass at each step

## Rules

- Do NOT change any behavior
- Test after EVERY change
- Write component tests for every new module
- Do not create abstractions that are more complex than the code they replace

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase3-results.md`.
