# Agent-Implementation-Skill

## Project Structure

```
├── governance/              # Project governance layer
│   ├── problems/index.md    # Why this code exists (PRB-XXXX)
│   ├── patterns/index.md    # How we solve recurring problems (PAT-XXXX)
│   ├── audit/prompt.md      # Audit process for external models
│   ├── audit/history.md     # Cumulative audit log (105 rounds)
│   ├── design/              # Design rationale documents
│   └── risk-register.md     # Landed-code risks and debt
├── philosophy/              # Values and principles
│   ├── design-philosophy-analysis.md
│   ├── design-philosophy-notes.md
│   ├── profiles/PHI-global.md
│   └── region-profile-map.md
├── system-synthesis.md      # Architecture + governance connections
├── src/                     # Deployed skill code
│   ├── <system>/agents/     # Agent definitions owned by each system (57 agents across 15 systems)
│   ├── scripts/             # Runtime orchestration scripts
│   ├── SKILL.md             # Skill entry point
│   ├── implement.md         # Implementation pipeline
│   └── ...                  # Other skill docs
├── evals/                   # Evaluation scenarios
└── tests/                   # Test suite
```

## Running Sub-Agents

Sub-agents are invoked via the `agents` binary (`~/.local/bin/agents`), not through Claude Code's built-in Agent tool. The binary is the CLI mode of [Oulipoly Agent Runner](https://github.com/nestharus/agent-runner).

### CLI Syntax

```bash
agents [OPTIONS] [AGENT] [PROMPT...]
```

| Option | Description |
|--------|-------------|
| `-m, --model <MODEL>` | Execute a model directly (no agent file) |
| `-a, --agent-file <AGENT_FILE>` | Path to an agent `.md` file (any location) |
| `-f, --file <FILE>` | Read prompt from file |
| `-p, --project <PROJECT>` | Working directory for the subprocess |
| `--models-dir <MODELS_DIR>` | Override models directory |
| `--agents-dir <AGENTS_DIR>` | Override agents directory |

**Prompt resolution priority:** `--file` > positional arguments > stdin

### Common Invocation Patterns

```bash
# Model + prompt file (pipeline standard)
agents --model gpt-high --file prompt.md

# Agent file + model override
agents --agent-file src/staleness/agents/alignment-judge.md --model claude-opus --file prompt.md

# Named agent (resolved from agents directory)
agents code-reviewer "Review this function"

# Pipe prompt from stdin
cat spec.md | agents --model glm

# Set working directory for the subprocess
agents --model gpt-high -p /path/to/repo --file prompt.md
```

### Agent File Format

Agent definitions live in `src/<system>/agents/*.md`. Each is a markdown file with YAML frontmatter:

```markdown
---
description: 'One-line description of what this agent does'
model: claude-opus
output_format: ''
---

System prompt / reasoning method goes here.
```

The `model` field sets the default model. It can be overridden with `--model` at invocation time.

### Model Configuration

Model configs are TOML files in `~/.config/oulipoly-agent-runner/models/` (one per model). The filename minus `.toml` is the model name used with `--model`.

Single provider:
```toml
command = "claude"
args = ["-p", "--model", "haiku"]
prompt_mode = "stdin"
```

Multiple providers (load balanced):
```toml
prompt_mode = "arg"

[[providers]]
command = "codex"
args = ["exec", "-m", "gpt-5.3-codex"]

[[providers]]
command = "codex2"
args = ["exec", "-m", "gpt-5.3-codex"]
```

Load balancing is automatic: round-robin with error avoidance (providers with 3+ errors in the last 30 minutes are deprioritized). State is stored in SQLite at `~/.local/share/oulipoly-agent-runner/state.db`.

### Available Models

See `~/.config/oulipoly-agent-runner/models/` for the full list. Model selection guidance is in `src/models.md`.

---

## Cleanup Backlog

**`docs/cleanup-backlog.md`** — Tracked structural issues discovered during reorganization. 153 items (most DONE). Methodology §1-16 covers: dead code, naming, god functions, structural placement, type safety gaps, missing domain concepts, architectural layers, system health, coupling, cohesion, concern decomposition, contract surface, DI gaps, optional fields / discriminated unions, magic strings / enums, duplicate code. Check this file before starting any cleanup work.

---

## Governance Documents

These are first-class project artifacts under source control:

| Document | Purpose | Maintained by |
|----------|---------|---------------|
| `governance/problems/index.md` | Problem archive — why code exists | Audit process + manual |
| `governance/patterns/index.md` | Pattern catalog — how we solve things | Audit process |
| `governance/risk-register.md` | Landed-code risks | Post-impl assessment |
| `philosophy/` | Design values and principles | User + audit process |
| `system-synthesis.md` | Architecture + governance connections | Audit process + manual |

### Working with Governance Docs

- **Before proposing code changes**: Check `governance/patterns/index.md` for applicable patterns
- **Before creating new artifacts**: Check `governance/problems/index.md` for the problem being solved
- **Pattern violations**: Must propose a pattern delta (Tier 1) before the code change (Tier 2)
- **New problem surfaces**: Add to problem archive with provenance

---

## Audit Cycle: Handling New Responses

When the user says **"New response up"** or similar, execute the full Round N cycle below.

### Step 1: Read Inputs

Read in parallel:

- `~/work/tmp/execution-philosophy/response.md` — the new audit response
- `governance/audit/history.md` — full cycle history for cycle detection

### Step 2: Assess Violations

For each violation in the response, check audit history:

- **Cycle** — the exact fix was already tried in a prior round and reverted or dismissed. Do NOT reimplement. Note the round numbers.
- **False positive** — the "violation" describes behavior that is intentional by design. Dismiss with rationale.
- **New surface** — a real violation at a code location not previously addressed. **Implement it.**

Document your cycle/dismiss decisions clearly before writing any code.

### Step 3: Implement

For each non-dismissed violation, make the minimal code change that resolves it.

Consult `governance/patterns/index.md` for applicable patterns. Key patterns:

- **PAT-0001 (Corruption Preservation)**: malformed JSON → `rename_malformed()` + fail-closed
- **PAT-0002 (Prompt Safety)**: all prompts through `write_validated_prompt()`
- **PAT-0003 (Path Registry)**: all paths from `PathRegistry`
- **PAT-0005 (Policy-Driven Models)**: no hardcoded model strings
- **PAT-0008 (Fail-Closed)**: on uncertainty, default to conservative behavior

### Step 4: Run Tests

```bash
cd /home/nes/projects/agent-implementation-skill
uv run python -m pytest -x -q
```

Fix any failures before proceeding.

### Step 5: Commit and Push

```bash
cd /home/nes/projects/agent-implementation-skill
git add -A
git commit -m "feat(audit): Round N — <summary>"
git push
```

Note the commit SHA for the audit history entry.

### Step 6: Update Audit History

Edit `governance/audit/history.md`:

1. **Per-round index table** — add row: `| N | <sha7> | <test count> | <violation count> | <summary> |`
2. **Active Concern Threads** — update threads this round progressed
3. **Round Details** — append `#### Round N — Commit <sha>` section

Also sync a copy to the external working directory:
```bash
cp governance/audit/history.md ~/work/tmp/execution-philosophy/audit-history.md
```

### Step 7: Update Governance Artifacts

If the round's changes affect governance:
- **New patterns discovered** → update `governance/patterns/index.md`
- **Problems resolved or evolved** → update `governance/problems/index.md`
- **Risk register entries** → update `governance/risk-register.md`
- **Synthesis changes** → update `system-synthesis.md`

### Step 8: Rezip Codebase

Delete the old zip and recreate:

```bash
cd /home/nes/projects/agent-implementation-skill
rm -f ~/work/tmp/execution-philosophy/codebase.zip
zip -r ~/work/tmp/execution-philosophy/codebase.zip src/ evals/ tests/ governance/ philosophy/ system-synthesis.md -x '*__pycache__*' -x 'src/.venv/*' -x '*:Zone.Identifier'
```

---

## Agentic Eval QA Process

End-to-end behavioral testing of the multi-agent workflow system. Evals pre-seed
realistic workspace state, trigger real workflows with QA interception, and validate
outcomes with structural checks + an LLM judge.

### Running Evals

```bash
cd /home/nes/projects/agent-implementation-skill

# List all scenarios
uv run agentic-evals --list

# Run one scenario (cheap → moderate → expensive)
uv run agentic-evals --scenario <id> --keep-failed

# Run by cost tier
uv run agentic-evals --max-cost-tier cheap --keep-failed

# Run all
uv run agentic-evals --keep-all
```

Scenarios live in `evals/agentic/fixtures/*/scenario.yaml`. Each has a cost tier
(cheap=180s, moderate=600s, expensive=1500s timeout).

### Eval Flow

1. **Seed** — isolated temp planspace/codespace from fixture files, `parameters.json`
   with `qa_mode: true` auto-injected
2. **Trigger** — run real workflow entry points (readiness gate, dispatcher, scan,
   philosophy bootstrap) via trigger adapters
3. **Collect** — gather output files, JSON, DB rows
4. **Structural checks** — file existence, JSON validity, DB row counts, heading checks
5. **Semantic judge** — GLM judge via `agents --model glm --agent-file agents/eval-judge.md`
   compares outputs against answer key

### Investigating Failures

When evals expose behavioral gaps:

1. **Observe and report** — document the exact failure from the eval report
2. **Dispatch investigation agents** — use `agents --model gpt-high --file <prompt.md>`
   to investigate root causes in the production code
3. **Build design prompt** — combine investigation findings into a design request with
   exact file paths, root causes, and constraints
4. **Get design response** — pass the design prompt to an external model for solution design
5. **Implement fixes** — apply the design to production code
6. **Re-run eval** — verify the fix by re-running the specific scenario

Investigation prompts and findings go in `.tmp/agentic-evals/`.

### Important Rules

- **QA mode is mandatory** — all eval runs use `qa_mode: true` so dispatched agents
  are intercepted, not live
- **Observe, report, stop** — when monitoring an eval: do NOT manually write artifacts
  to unblock a stuck pipeline, do NOT send resume signals, do NOT pre-seed files that
  agents should have produced
- The ONLY thing that should be in the test is the spec. Nothing else.
- **Real agent calls** — evals exercise real LLM calls via the `agents` binary; cost
  and latency are real

### Current Wave 1 Results (7 scenarios)

| Scenario | Tier | Result | Notes |
|----------|------|--------|-------|
| readiness-triggers-research-planner | cheap | PASS | |
| research-planner-routes-value-choice-upward | cheap | PASS | |
| research-branch-stale-after-input-change | cheap | PASS | |
| scan-quick-greenfield-related-files | moderate | PASS | |
| scan-brownfield-related-files-revalidation | moderate | PASS | Fixed: fail-closed signal handling + deterministic stale detection |
| philosophy-bootstrap-from-user-source | moderate | PASS | Fixed: principle extraction scoped to ## Principles headings |
| research-flow-synthesizes-dossier | expensive | PENDING | Not yet run at full timeout |

### Files

| Path | Purpose |
|------|---------|
| `evals/agentic/` | Harness package (11 modules) |
| `evals/agentic/fixtures/` | Scenario fixtures (YAML + seed files) |
| `agents/eval-judge.md` | GLM judge agent definition |
| `.tmp/agentic-evals/` | Investigation prompts and findings |
| `~/work/tmp/execution-philosophy/combined-design-prompt.md` | Current design request for fixes |

---

### Cycle Detection Reference

Common dismissal patterns from history:

- **Tests / pyproject.toml absent from zip** — tests/ included in bundle as of R118. pyproject.toml ships under src/.
- **Model names in `models.md`** — `gpt-5.4-high` and `gpt-5.4-xhigh` are current names.
- **Any violation in a "Settled Concerns" section** — already guarded by tests; re-raising is a cycle.

### Files

| Path | Purpose |
|------|---------|
| `~/work/tmp/execution-philosophy/response.md` | Current round's audit response |
| `~/work/tmp/execution-philosophy/codebase.zip` | Audit bundle (single zip with everything) |
| `governance/audit/prompt.md` | Audit prompt for external models (in repo) |
| `governance/audit/history.md` | Authoritative audit history (in repo) |
| `governance/patterns/index.md` | Pattern catalog (in repo) |
| `governance/problems/index.md` | Problem archive (in repo) |

---

## Implementation & Bug-Fix Workflow

All code changes — bug fixes, features, refactors — follow the same pipeline.
The only difference between a bug fix and a feature is that bugs start with RCA.

### Bug Fixes

| Step | Model | Role |
|------|-------|------|
| 1. RCA | `gpt-high` | Investigate root cause. Read code, trace failure, produce report. Do NOT propose fixes. |
| 2. Proposal | `gpt-high` | Propose a fix based on RCA findings. Write a design proposal. Do NOT implement. |
| 3. Alignment | `claude-opus` | Check proposal against governance (patterns, philosophy, problems). Directional coherence, not coverage. Identify contradictions and gaps. Returns ALIGNED, MISALIGNED, or NEEDS_REVISION. |
| 4. Iterate | — | If alignment returns NEEDS_REVISION or MISALIGNED: revise proposal, re-run alignment. **Loop until ALIGNED.** Do NOT proceed to risk assessment until alignment passes. |
| 5. Risk assessment | 2x `claude-opus` | **Only after alignment passes.** Launch two sub-agents **in parallel**: audit risk + alignment risk. Can we reliably research this change? Can we reliably check direction? If either is too high, decompose into smaller pieces. |
| 6. Research | `gpt-high` | **Only after risk assessment passes.** For each piece, research hookpoints in the codebase. Understand what exists. Avoid building parallel systems. |
| 7. Implement | `gpt-high` | **Only after research completes.** Launch implementation sub-agents for each piece. |

### Features / Refactors

Same pipeline, skip step 1 (RCA). Same model assignments apply.

### Principles

- **Separate agents for separate concerns** — RCA agents don't propose. Proposal
  agents don't implement. Alignment agents don't fix. Each agent has one job.
- **Risk drives decomposition** — Don't decompose because "it's complex." Decompose
  because audit risk or alignment risk exceeds what one agent can handle reliably.
- **Alignment is directional** — "Is this going the right way?" not "Did we cover
  everything?" Alignment is sparse, checking concerns where there's risk of mismatch.
- **Research before implementation** — Every piece gets codebase research to find
  hookpoints and avoid building parallel systems. This is the most common failure mode.
- **Iterate until it passes** — Don't implement a proposal that hasn't passed
  alignment and risk assessment. The cost of iterating on a proposal is far less
  than the cost of implementing the wrong thing.

---

## Open Design: Blocker Resolution Phase

**`~/work/tmp/execution-philosophy/blocker-resolution-design.md`** — Design document for the blocker resolution phase and post-implementation verification/testing system. QA runs 8-9 revealed that sections blocked at the readiness gate have no mechanism to create plans to resolve the missing items. The resolution phase extends coordination to run between reconciliation and implementation. Settled: resolution reuses coordination infrastructure, doesn't bypass the readiness gate, risk-assesses plans before implementation. Open problem: blockers are free-text with no stable identifiers, making cross-section cycle detection unreliable.

The same design doc specifies verification and testing as new task types woven into existing loops (PAT-0016). Four new agents across two new system namespaces: `src/verification/` (structural-verifier, integration-verifier) and `src/testing/` (behavioral-tester, test-rca). Findings feed back through existing channels (`impl_problems`, `BlockerProblem`, coordination). See `system-synthesis.md` Verification & Testing region for details.
