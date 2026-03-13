# Model Selection: Multi-Model Task Routing

Models are selected via `model-policy.json` in the planspace and resolved
through `dispatch.service.model_policy.resolve()`. CLI invocation:
```bash
agents --model <model-name> --file <prompt.md>
```

## Decision Tree

```
UNDERSTANDING intent or FORMULATING questions?
  → Opus (current session)

DESIGNING something new under constraints (primary synthesis)?
  → gpt-high (escalate to gpt-xhigh on recurrence or stall)

ALIGNMENT CHECKING for coherence or divergence?
  → Opus (default for alignment-judge), GPT-high as fallback

WRITING detailed algorithms or IMPL notes from direction?
  → gpt-high

DEBUGGING test failures or finding root causes?
  → gpt-high

WRITING source code from detailed specs?
  → gpt-high

SCANNING codebase for relevant locations or SUMMARIZING block fit?
  → glm

RUNNING commands (tests, shell operations)?
  → glm (or run pytest directly)

Simple lookup or classification?
  → GLM
```

## Model Details

### Opus (Current Session)
- **Strengths**: Intent interpretation, question formulation, alignment checking
- **Use for**: Directing workflow, integration stories, evaluating proposals, constraints
- **Should NOT**: Synthesize proposals (bias risk), do mechanical tasks

### gpt-xhigh (Escalation Proposer — Strategic Synthesis)
- **Strengths**: Highest reasoning effort, novel architectural synthesis
- **Invocation**: `agents --model gpt-xhigh --file <prompt.md>`
- **Use for**: Escalation-tier research synthesis when default proposer
  (policy.proposal = GPT-high) hits recurrence or stall
- **Does NOT**: Audit or implement

### gpt-high
- **Strengths**: Constraint-aware design, systematic constraint evaluation
  (not feature coverage), algorithm writing, IMPL notes, debug/RCA
- **Prompt format**: `--file <prompt.md>` (reads from file)
- **Use for**: Constraint alignment reviews, algorithm refinement, IMPL notes, debug/RCA, planning

### GLM
- **Strengths**: Command execution, test running, codebase scanning, relevance summarization
- **Prompt format**: `--file <prompt.md>` (pipeline contract requires `--file`; the binary also accepts inline text, but that is not the pipeline-standard invocation)
- **Use for**: TODO scanning (section → code mapping), block fit summaries, test running
- **Summary role**: GLM summaries are not authoritative — they capture reasoning
  to reduce re-analysis by downstream models. Preserves context for blocks that
  may be refactored, moved, or removed.
- **Fallback**: Run pytest directly if GLM unreliable

## Stage 3.5 Model Policy Keys

The Shared Integration Substrate (SIS) stage uses three model policy keys,
configurable in `model-policy.json`:

| Key | Default | Why |
|-----|---------|-----|
| `substrate_shard` | `gpt-high` | Per-section dependency exploration — structured extraction, high controllability needed |
| `substrate_pruner` | `gpt-xhigh` | Strategic cross-section convergence analysis — highest reasoning for graph exploration with pruning |
| `substrate_seeder` | `gpt-high` | Anchor creation from seed plan — follows precise instructions, no novel reasoning |

The pruner is the only SIS agent that requires xhigh reasoning — it must
identify convergence patterns, resolve contradictions, and make strategic
deferral decisions across all shards simultaneously.

## ROAL Model Policy Keys

The ROAL execution-risk loop uses two explicit model policy keys,
configurable in `model-policy.json`:

| Key | Default | Why |
|-----|---------|-----|
| `risk_assessor` | `gpt-high` | Diagnostic agent assessing execution risk before descent |
| `execution_optimizer` | `gpt-high` | Translates the risk assessment into the minimum effective execution posture |

## QA Interceptor Model Policy Key

The QA dispatch interceptor uses one explicit model policy key,
configurable in `model-policy.json`:

| Key | Default | Why |
|-----|---------|-----|
| `qa_interceptor` | `claude-opus` | Contract-compliance review between the submitting agent, target agent, and task payload |

### Research-First Intent Layer

| Key | Default | Why |
|-----|---------|-----|
| `research_plan` | `claude-opus` | Plans research by decomposing blocking questions into bounded tickets |
| `research_domain_ticket` | `gpt-high` | Executes web/code research via Firecrawl search + scrape |
| `research_synthesis` | `gpt-high` | Merges ticket results into dossier + surfaces + proposal addendum |
| `research_verify` | `glm` | Verifies citation integrity on dossier claims |

## Pipeline Patterns

### Implementation Pipeline
```
GPT-high       → ALGORITHM block + IMPL notes (NO code)
GPT-high       → Source code from ALGORITHM + IMPL
(pytest)       → Tests
GPT-high       → Debug/RCA if failures
GPT-high       → Constraint alignment check
```

### Research Pipeline
```
Opus           → Research prompt + context package
GPT-xhigh   → Synthesize proposal
GPT-high     → Divergence review
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
| GPT-xhigh | Highest reasoning, moderate controllability. Needs clear problem framing. |
| GPT-high | Good reasoning, high controllability. Best for structured constraint evaluation (not feature coverage). |
| GLM | Low reasoning, highest controllability. Follows instructions precisely. |

**Escalation rule**: Only escalate when a lower model has demonstrably
failed on the same task (e.g., 2+ alignment failures at GPT-high before
escalating to GPT-xhigh). Don't pre-escalate — it wastes reasoning
budget and can reduce instruction adherence.

## Model-Choice Signal

When section-loop selects a model for a dispatch, it writes:
```json
// signals/model-choice-{section}-{step}.json
{
  "section": "03",
  "step": "integration-proposal",
  "model": "gpt-high",
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

Strategic agents (Opus, GPT-xhigh) should include at the end of
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
- **Never pre-escalate**: Don't use GPT-xhigh on first attempt "just in case"
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
- A high-reasoning model (Opus, GPT-xhigh) with a methodological agent
  file produces strategic analysis that adapts to novel situations.
- A high-controllability model (GLM) with a methodological agent file
  produces precise, instruction-following execution of that method.
- A reasoning model WITHOUT an agent file relies on the dynamic dispatch
  prompt alone — appropriate for one-shot tasks with clear context.

Match the model to the method's demands: complex judgment needs reasoning;
mechanical classification needs controllability.

## Anti-Patterns

- **DO NOT use Opus for mechanical review** — GPT is better
- **DO NOT pre-escalate to GPT-xhigh** — GPT-high is the default proposer; escalate to xhigh only on recurrence or stall
- **DO NOT synthesize proposals yourself** — use GPT-high (or GPT-xhigh on escalation)
- **DO NOT send inline instructions to GPT** — use `--file` with prompt file
- **DO NOT pre-escalate models** — start with the default and escalate on failure
- **DO NOT use reasoning models for extraction** — GLM follows instructions more reliably for reads/scans
