# Research: Multi-Phase Problem Refinement

Transform raw ideas into validated proposals through exploration, alignment,
proposal generation, and iterative review.

## The Lifecycle

```
Phase A: Exploration     — understand the problem landscape
Phase B: Alignment       — create the intent standard
Phase C: Proposal        — generate concrete architecture from alignment
Phase D: Review + Refine — audit and iterate until convergence
```

We do NOT jump to solutions. Understand the problem first (A-B), generate
solutions (C), validate them (D).

## Phase A: Exploration

Broad research to understand the problem space.

1. General research with broad models (Gemini, web search, etc.)
2. Gather landscape understanding — what exists, what's been tried, what failed
3. Identify dimensions along which the problem varies
4. Surface questions you don't know how to answer yet

**Produces**: Raw notes, landscape understanding, open questions.
NOT a design. NOT a solution.

**Skip when**: Problem is well-understood, constraints documented, or change is small.

## Phase B: Alignment Document

Create the intent standard against which all proposals are measured.

### What an alignment document IS
- **Values** (not instructions) — "comprehension speed dominates everything"
- **Anti-patterns as hard constraints** — "gradients as decoration are lazy"
- **Calibration examples** — quality floor, not prescriptions
- **Search processes** — HOW to discover solutions, not just WHAT to build
- **Explicit unknowns** — prevents silent assumptions downstream

### How to create it
1. User provides raw notes/intuition
2. Opus proposes structure
3. User pushes back — reveals deeper constraints
4. Opus rebuilds with deeper understanding
5. Iterate until shared understanding is rich enough
6. Synthesize into the alignment document
7. User audits for completeness

**Lives at**: `.tasks/plans/<project>/.research/<topic-slug>/alignment.md`

**Skip when**: Project already has alignment doc or design baseline.

## Phase C: Proposal Generation

Generate concrete architecture from the alignment document.

### Step 0: Establish Research Context
1. Read existing constraints/design principles
2. Read the current implementation
3. Identify implicit constraints
4. Present findings to user for confirmation

### Step 1: Create Research Directory
```
mkdir -p ".tasks/plans/<project>/.research/<topic-slug>/"
```

### Step 2: Prepare Context Package
Collect constraint documents: alignment doc, design principles, current
state, relevant code, prior responses, tradeoff docs.

### Step 3: Write Research Prompt (`prompt.md`)
```markdown
# Research: <Title>

## What I Need From You
## Important Framing
## The Core Problem
## Alignment Document
## Existing Capabilities
## Constraints
## Questions
## What Success Looks Like
```

### Step 4: Send to Research Agent
```bash
uv run agents --model gpt-codex-xhigh --file "$research_dir/prompt.md"
```
Save response as `response.md`.

## Phase D: Review + Refine

### Step 5: Audit the Response
1. Quick Opus analysis — identify 5-10 divergence signals
2. Write `audit-prompt.md` with divergence signals
3. Run audit: `uv run agents --model gpt-codex-high --file audit-prompt.md`
4. Save as `audit-results.md`

### Step 6: Evaluate Audit Results
- **CRITICAL divergences** → Write refinement prompt (Step 7)
- **MINOR only** → Proceed to Step 8
- **None** → Proceed to Step 8

### Step 7: Write Refinement Prompt
```markdown
# Research Refinement: <Topic>
## Why This Refinement Exists
## What the Original Prompt Actually Asked
## What the Previous Response Got Right
## What the Previous Response Got Wrong
## Redirected Questions
```
Send to codex-xhigh. Save as `response2.md`. Audit again.
Repeat until convergence (typically 2-3 rounds).

### Step 8: Final Validation
1. Drift check — does final proposal still solve original problem?
2. Constraint check — run final audit against ALL constraints
3. Tradeoff check — what are we giving up? Acceptable?
4. Actionability check — concrete enough to implement?

Present to user for approval. Do NOT proceed without approval.

### Step 9: Record and Update Memory

## Model Roles

| Task | Model |
|------|-------|
| Direction + intent + alignment doc | Opus (current session) |
| Deep architectural synthesis | gpt-codex-xhigh |
| Constraint alignment checking | gpt-codex-high / high2 |
| Quick fact-checking | Haiku |

## Anti-Patterns

- **DO NOT skip Phase A-B** — jumping to proposals without exploration produces wrong solutions
- **DO NOT synthesize the proposal yourself** — Opus formulates questions, codex-xhigh synthesizes
- **DO NOT accept first response** — always audit against alignment document
- **DO NOT add requirements during audit** — audit checks existing constraints only
- **DO NOT retry the same prompt** — write a refinement prompt explaining WHY it diverged
- **DO NOT send synthesis prompts to codex-high** — codex-high audits; codex-xhigh synthesizes
