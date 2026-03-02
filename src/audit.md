# Audit: Concern-Based Problem Decomposition

### Terminology Contract

"Audit" in this pipeline means **alignment against stated problems and
constraints** — checking directional coherence between what the work claims
to solve and what it actually solves. It NEVER means feature-coverage
checking against a checklist. Plans describe problems and strategies, not
enumerable features. If you find yourself counting features, you are in the
wrong frame.

Use for large tasks needing structured decomposition by problem region.

## Core Algorithm

1. **Intake + scope.**
   - Capture user goal, success criteria, constraints, completion conditions.
   - If scope is underspecified for critical decisions, ask one concrete
     clarification before proceeding.

2. **Identify problem regions.**
   - Read the input to understand **what problems are being solved**.
   - Decompose by problem/concern boundaries — where does one problem
     end and a different problem begin?
   - Do NOT follow the input's document structure as the decomposition.
     The input may have headers, numbered items, or logical chunks, but
     those are presentation — the real structure is the problem space.
   - Each problem region becomes one unit of work.

3. **Prepare tmp directory.**
   - Create `.tmp/audit/<run_slug>/`.
   - Create `.tmp/audit/<run_slug>/synthesis.md` for final consolidation.

4. **Trace alignment per problem region.**
   - For each problem region:
     - Identify which parts of the input relate to this problem
     - Gather context from the codebase that touches this concern
     - Trace the alignment chain: **problem → proposal → TODO/microstrategy → code**
     - Record findings, friction points, and unresolved tensions
     - Write region file at `.tmp/audit/<run_slug>/<region-key>.md`
   - Use **task submission** for follow-up work — write structured
     task-request files for the dispatcher. Do NOT use direct
     agent-per-region delegation.

5. **Surface cross-concern friction.**
   - After individual regions are analyzed, look for interactions:
     - Do solutions to different problems contradict each other?
     - Are there shared constraints that span multiple regions?
     - Does solving one problem create or worsen another?
   - Record cross-concern friction in the synthesis file.

6. **Synthesize with traceability check.**
   - Consolidate findings across all problem regions:
     - Verify each original problem has a traceable path to a solution
     - Identify problems with broken or weak alignment chains
     - Surface unresolved tensions and scope expansion needs
     - Produce severity-ranked findings
   - Do NOT check that every part of the input is represented.
     Check for **same-problem traceability** — can each problem be traced
     through the alignment chain to where it is actually addressed?

7. **Return final result.**
   - Present top-level assessment: which problems are well-aligned,
     which have broken chains, and which create cross-concern friction.
   - Then include unresolved tensions, scope expansion risks, and
     concrete next actions.

## Region File Template

```markdown
### Problem Statement
<what problem this region addresses — in the user's terms>

### Relevant Input
<which parts of the original input relate to this problem>

### Alignment Chain
- Problem: ...
- Proposal/strategy that addresses it: ...
- Implementation artifacts (TODOs, microstrategies, code): ...
- Chain status: ALIGNED / WEAK / BROKEN

### Cross-Concern Friction
- Interaction with other problem regions: ...
- Shared constraints: ...

### Findings
- Finding: ...
- Evidence: ...

### Unresolved Tensions
- What remains open and why
```

## Exit Criteria

1. Every problem region has an alignment chain assessment.
2. Cross-concern friction has been surfaced and recorded.
3. Synthesis captures traceable paths from problems to solutions.
4. Unresolved tensions are documented with why they remain open.
