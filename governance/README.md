# Governance

Project governance layer. Persistent, queryable records that make per-run artifacts cumulative across runs.

## Structure

```
governance/
├── problems/          # Why this code exists (PRB-XXXX records)
│   └── index.md
├── patterns/          # How we solve recurring problems (PAT-XXXX records)
│   └── index.md
├── audit/             # Audit process and history
│   ├── prompt.md      # The audit prompt sent to external models
│   └── history.md     # Cumulative log of all audit rounds
├── design/            # Design rationale for governance decisions
│   └── governance-gaps.md
├── risk-register.md   # Landed-code risks and accepted debt
└── README.md          # This file
```

## Related Files

| File | Location | Purpose |
|------|----------|---------|
| Philosophy | `philosophy/` | Values and principles governing the project |
| System synthesis | `system-synthesis.md` | Architecture + governance connections |

## Hierarchy

1. **Problems** — why this code exists
2. **Philosophy** — what values govern it
3. **Patterns** — how those values are operationalized
4. **System synthesis** — how it all connects
5. **Proposals** — changes designed under these constraints
6. **Implementation** — execution under bounded risk
7. **Post-implementation assessment** — what risks landed
8. **Stabilization** — reduce risks, re-align until stable

## Audit Process

The audit process maintains this governance layer. See `audit/prompt.md` for the full audit protocol. Each audit round:
1. Consults the problem archive before minting "new" problems
2. Consults the pattern archive before recommending code changes
3. Proposes pattern deltas before code deltas when the issue is template drift
4. Updates governance documents with findings

### Running an Audit

1. Bundle: `governance/`, `philosophy/`, `src/`, `evals/`
2. Send to external model with `governance/audit/prompt.md`
3. Process response per AGENTS.md audit cycle
4. Update `governance/audit/history.md` with round results
