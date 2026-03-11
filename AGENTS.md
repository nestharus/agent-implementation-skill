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
│   ├── agents/              # Agent contract definitions (46 agents)
│   ├── scripts/             # Runtime orchestration scripts
│   ├── SKILL.md             # Skill entry point
│   ├── implement.md         # Implementation pipeline
│   └── ...                  # Other skill docs
├── evals/                   # Evaluation scenarios
└── tests/                   # Test suite
```

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
zip -r ~/work/tmp/execution-philosophy/codebase.zip src/ evals/ governance/ philosophy/ system-synthesis.md -x '*__pycache__*'
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

- **Tests / pyproject.toml absent from zip** — settled in R46/R47: audit bundle = deployed layout. Not a violation.
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
