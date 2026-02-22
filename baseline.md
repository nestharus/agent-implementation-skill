# Create Design Baseline: Atomize Proposal into Alignment References

Transform an approved proposal into a structured `design/` folder containing
atomized documents that serve as alignment references throughout implementation.

**Key insight**: A proposal is too large to audit against directly. By
decomposing it into atomic constraints and tradeoffs, every piece
of implemented code can be checked against the specific principle it should follow.

## Prerequisites

- An approved proposal (passed evaluation)
- The alignment document
- Access to Codex-high/high2 for alignment check

## Step 1: Read the Proposal and Alignment Document

Identify:
1. **Fundamental principles** — WHY decisions were made (→ `constraints/`)
2. **Priority ordering** — WHAT gets sacrificed for what (→ `TRADEOFFS.md`)
3. **System description** — WHAT the system is (→ `overview/`)
4. **Module responsibilities** — WHERE things live (→ `routing/`)

**Note**: Patterns (`patterns/`) are NOT created during baseline extraction.
Patterns are recurring algorithms discovered post-implementation — you don't
know which patterns emerge until the code exists. Pattern discovery is a
separate activity after implementation is complete.

## Step 2: Create the Design Directory

```bash
mkdir -p "$design_dir"/{constraints,overview,routing}
```

## Step 3: Write TRADEOFFS.md

The priority ordering and decision framework. FIRST document anyone reads.

Contents: primary objective, lexicographic priority list, key tradeoffs
(mechanism not just preference), decision authority (human vs system),
explicit non-goals.

## Step 4: Write Constraint Documents

Each constraint is a fundamental principle. Number them `00_`, `01_`, etc.

Key properties:
- No class names, no module paths, no implementation details
- Self-contained — readable without other constraints
- Corollaries derived from the principle, not independent rules
- Principle explains WHY, corollaries explain WHAT FOLLOWS

## Step 5: Write Overview Documents

System-level architecture descriptions (DO include module paths/class names):
- `00_SYSTEM_OVERVIEW.md` — Package inventory + data flow
- `01_PIPELINE_ARCHITECTURE.md` — Core processing pipeline
- `02_EXTERNAL_BOUNDARIES.md` — Inputs, outputs, external dependencies

## Step 6: Write Routing Documents

Per-package routing summaries (classification, surface APIs, dependencies,
consumers). Plus `INDEX.md` and `DEPENDENCIES.md`.

## Step 7: Write README.md

Directory layout, what goes where, reading order, authoritative sources.

## Step 8: Verify Coverage Alignment

Codex-high2 checks that every proposal section is represented in at least
one design document. Fix gaps. Re-check until clean.

```bash
uv run agents --model gpt-5.3-codex-high2 --file "<audit-prompt-path>"
```

## Anti-Patterns

- **DO NOT copy the proposal** — decompose it into atomic principles
- **DO NOT include implementation details in constraints** — constraints survive refactors
- **DO NOT create one giant document** — the point is atomization
- **DO NOT skip the verification** — proposals have interconnections easy to lose
- **DO NOT treat this as one-time** — baseline evolves with the project
