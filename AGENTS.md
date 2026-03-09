# Agent-Implementation-Skill

## Project Structure

```
├── governance/              # Project governance layer
│   ├── problems/index.md    # Why this code exists (PRB-XXXX)
│   ├── patterns/index.md    # How we solve recurring problems (PAT-XXXX)
│   ├── audit/prompt.md      # Audit process for external models
│   ├── audit/history.md     # Cumulative audit log (104 rounds)
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
