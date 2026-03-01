# Model Selection: Multi-Model Task Routing

Models are configured in `.agents/models/` (TOML files). Invoke via:
```bash
uv run agents --model <model-name> --file <prompt.md>
```

All CLI models run from the ai-workflow repo root.

## Decision Tree

```
UNDERSTANDING intent or FORMULATING questions?
  → Opus (current session)

DESIGNING something new under constraints (primary synthesis)?
  → gpt-codex-xhigh

ALIGNMENT CHECKING for coherence or divergence?
  → Opus (default for alignment-judge), codex-high as fallback

WRITING detailed algorithms or IMPL notes from direction?
  → gpt-codex-high / high2

DEBUGGING test failures or finding root causes?
  → gpt-codex-high / high2

WRITING source code from detailed specs?
  → gpt-codex-high

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

### gpt-codex-xhigh (Primary Proposer)
- **Strengths**: Highest reasoning effort, novel architectural synthesis
- **Invocation**: `uv run agents --model gpt-codex-xhigh --file <prompt.md>`
- **Use for**: Primary research synthesis (proposer role)
- **Does NOT**: Audit or implement

### gpt-codex-high / high2 (Interchangeable)
- Same capability, different quota pools
- **Strengths**: Constraint-aware design, systematic constraint evaluation
  (not feature coverage), algorithm writing, IMPL notes, debug/RCA
- **Prompt format**: `--file <prompt.md>` (reads from file)
- **Use for**: Constraint alignment reviews, algorithm refinement, IMPL notes, debug/RCA, planning

### GLM
- **Strengths**: Command execution, test running, codebase scanning, relevance summarization
- **Prompt format**: `--file <prompt.md>` (preferred; inline `"<instructions>"` also accepted)
- **Use for**: TODO scanning (section → code mapping), block fit summaries, test running
- **Summary role**: GLM summaries are not authoritative — they capture reasoning
  to reduce re-analysis by downstream models. Preserves context for blocks that
  may be refactored, moved, or removed.
- **Fallback**: Run pytest directly if GLM unreliable

### Haiku
- **Strengths**: Fastest, cheapest, simple classification
- **Invocation**: `uv run agents --model haiku --file <prompt.md>`

## Stage 3.5 Model Policy Keys

The Shared Integration Substrate (SIS) stage uses three model policy keys,
configurable in `model-policy.json`:

| Key | Default | Why |
|-----|---------|-----|
| `substrate_shard` | `gpt-codex-high` | Per-section dependency exploration — structured extraction, high controllability needed |
| `substrate_pruner` | `gpt-codex-xhigh` | Strategic cross-section convergence analysis — highest reasoning for graph exploration with pruning |
| `substrate_seeder` | `gpt-codex-high` | Anchor creation from seed plan — follows precise instructions, no novel reasoning |

The pruner is the only SIS agent that requires xhigh reasoning — it must
identify convergence patterns, resolve contradictions, and make strategic
deferral decisions across all shards simultaneously.

## Pipeline Patterns

### Implementation Pipeline
```
codex-high     → ALGORITHM block + IMPL notes (NO code)
codex-high2    → Source code from ALGORITHM + IMPL
(pytest)       → Tests
codex-high     → Debug/RCA if failures
codex-high2    → Constraint alignment check
```

### Research Pipeline
```
Opus           → Research prompt + context package
codex-xhigh   → Synthesize proposal
codex-high     → Divergence review
Opus           → Evaluate, refine if needed
(repeat)
```

## Controllability Constraints

Model selection is not just about capability — it's about controllability.
Higher-reasoning models can degrade instruction-following when reasoning
chains get long. Match models to tasks where their reasoning style helps
rather than hurts.

| Model | Controllability Profile |
|-------|------------------------|
| Opus | High reasoning + high controllability. Best for directing and judging. |
| codex-xhigh | Highest reasoning, moderate controllability. Needs clear problem framing. |
| codex-high | Good reasoning, high controllability. Best for structured constraint evaluation (not feature coverage). |
| GLM | Low reasoning, highest controllability. Follows instructions precisely. |
| Haiku | Minimal reasoning, highest controllability. Classification only. |

**Escalation rule**: Only escalate when a lower model has demonstrably
failed on the same task (e.g., 2+ alignment failures at codex-high before
escalating to codex-xhigh). Don't pre-escalate — it wastes reasoning
budget and can reduce instruction adherence.

## Model-Choice Signal

When section-loop selects a model for a dispatch, it writes:
```json
// signals/model-choice-{section}-{step}.json
{
  "section": "03",
  "step": "integration-proposal",
  "model": "gpt-codex-high",
  "reason": "first attempt, default model",
  "escalated_from": null
}
```

This makes model decisions auditable. QA monitor can detect:
- Premature escalation (xhigh on first attempt)
- Stuck-at-low (3+ failures without escalation)
- Model mismatch (reasoning model used for mechanical task)

## Model Justification Protocol

Every strategic agent dispatch should produce a brief justification
alongside its primary output. This is not overhead — it's traceability
for model selection decisions.

### Required Fields in Agent Output

Strategic agents (Opus, codex-xhigh) should include at the end of
their response:

```
## Model Justification
- **Why this model**: <1-2 lines explaining why this model tier is appropriate>
- **Escalation trigger**: <what would force escalation to a higher tier>
```

### Escalation Policy

- **Default**: Start with the model specified in the model policy
- **Escalate on recurrence**: If a section signals recurrence (2+ attempts),
  escalate the next dispatch to a higher tier
- **Never pre-escalate**: Don't use codex-xhigh on first attempt "just in case"
- **Justify downgrades**: If using a cheaper model than policy suggests,
  explain why (e.g., "classification task, GLM sufficient")

### QA Monitor Integration

The QA monitor can detect:
- Premature escalation (xhigh on first attempt without justification)
- Stuck-at-low (3+ failures without escalation)
- Missing justification (strategic agent output without model justification block)

## Terminology

"Audit" in this repo means **constraint alignment audit** — checking
directional coherence between adjacent layers ("is it solving the right
problem?"). It does NOT mean feature coverage audit ("are all features
done?"). Feature-coverage framing is an invalid frame that the
alignment-judge agent explicitly rejects.

## Agent Definitions and Model Selection

Agent definition files (`agents/*.md`) encode a reusable **reasoning
method** — how to think about a class of problems. They do not contain
runtime paths or specific task context. Model selection determines the
**controllability and capability** applied to that method.

The combination of agent definition + model determines strategic behavior:
- A high-reasoning model (Opus, codex-xhigh) with a methodological agent
  file produces strategic analysis that adapts to novel situations.
- A high-controllability model (GLM) with a methodological agent file
  produces precise, instruction-following execution of that method.
- A reasoning model WITHOUT an agent file relies on the dynamic dispatch
  prompt alone — appropriate for one-shot tasks with clear context.

Match the model to the method's demands: complex judgment needs reasoning;
mechanical classification needs controllability.

## Anti-Patterns

- **DO NOT use Opus for mechanical review** — Codex is better
- **DO NOT use codex-high for primary synthesis** — it reviews alignment, codex-xhigh synthesizes
- **DO NOT synthesize proposals yourself** — use codex-xhigh
- **DO NOT send inline instructions to Codex** — use `--file` with prompt file
- **DO NOT pre-escalate models** — start with the default and escalate on failure
- **DO NOT use reasoning models for extraction** — GLM follows instructions more reliably for reads/scans
