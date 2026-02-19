# Model Selection: Multi-Model Task Routing

Models are configured in `.agents/models/` (TOML files). Invoke via:
```bash
uv run agents --model <model-name> [--file <prompt.md>] ["<instructions>"]
```

All CLI models run from the ai-workflow repo root.

## Decision Tree

```
UNDERSTANDING intent or FORMULATING questions?
  → Opus (current session)

DESIGNING something new under constraints (primary synthesis)?
  → gpt-5.3-codex-xhigh

AUDITING for completeness or divergence?
  → gpt-5.3-codex-high / high2

WRITING detailed algorithms or IMPL notes from direction?
  → gpt-5.3-codex-high / high2

DEBUGGING test failures or finding root causes?
  → gpt-5.3-codex-high / high2

WRITING source code from detailed specs?
  → gpt-5.3-codex-high2

SCANNING codebase for relevant locations or SUMMARIZING block fit?
  → glm

RUNNING commands (tests, shell operations)?
  → glm (or run pytest directly)

Simple lookup or classification?
  → Haiku
```

## Model Details

### Opus 4.6 (Current Session)
- **Strengths**: Intent interpretation, question formulation, alignment checking
- **Use for**: Directing workflow, integration stories, evaluating proposals, constraints
- **Should NOT**: Synthesize proposals (bias risk), do mechanical tasks

### gpt-5.3-codex-xhigh (Primary Proposer)
- **Strengths**: Highest reasoning effort, novel architectural synthesis
- **Invocation**: `uv run agents --model gpt-5.3-codex-xhigh --file <prompt.md>`
- **Use for**: Primary research synthesis (proposer role)
- **Does NOT**: Audit or implement

### gpt-5.3-codex-high / high2 (Interchangeable)
- Same capability, different quota pools
- **Strengths**: Constraint-aware design, systematic checklist evaluation,
  algorithm writing, IMPL notes, debug/RCA
- **Prompt format**: `--file <prompt.md>` (reads from file)
- **Use for**: Audits, algorithm refinement, IMPL notes, debug/RCA, planning

### gpt-5.3-codex-spark-xhigh (DEPRECATED)
- **Status**: Replaced by codex-high2 for code writing. Unreliable on 500+ line files.

### GLM
- **Strengths**: Command execution, test running, codebase scanning, relevance summarization
- **Prompt format**: Inline string `"<instructions>"`
- **Use for**: TODO scanning (section → code mapping), block fit summaries, test running
- **Summary role**: GLM summaries are not authoritative — they capture reasoning
  to reduce re-analysis by downstream models. Preserves context for blocks that
  may be refactored, moved, or removed.
- **Fallback**: Run pytest directly if GLM unreliable

### Haiku
- **Strengths**: Fastest, cheapest, simple classification
- **Use via**: Task tool with `model: "haiku"`

## Pipeline Patterns

### Implementation Pipeline
```
codex-high     → ALGORITHM block + IMPL notes (NO code)
codex-high2    → Source code from ALGORITHM + IMPL
(pytest)       → Tests
codex-high     → Debug/RCA if failures
codex-high2    → Constraint audit
```

### Research Pipeline
```
Opus           → Research prompt + context package
codex-xhigh   → Synthesize proposal
codex-high     → Audit proposal
Opus           → Evaluate, refine if needed
(repeat)
```

## Anti-Patterns

- **DO NOT use Opus for mechanical auditing** — Codex is better
- **DO NOT use codex-spark for complex files** — use codex-high2
- **DO NOT use codex-high for primary synthesis** — it audits, codex-xhigh synthesizes
- **DO NOT synthesize proposals yourself** — use codex-xhigh
- **DO NOT send inline instructions to Codex** — use `--file` with prompt file
