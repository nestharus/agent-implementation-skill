# Execution Philosophy Audit

You have four inputs:
- `philosophy.zip` — the user's design philosophy and agent analysis
- `codebase.zip` — the current implementation being audited
- `audit-history.md` — log of every previous audit round (what was found, what was implemented, what happened next)
- `patterns.md` — the established pattern catalog (how the system solves recurring classes of problems)

Unzip both archives. Read ALL files, the audit history, AND the pattern catalog before proceeding.

## The Governance Hierarchy

The system has three governance layers:

1. **Philosophy** (why) — the user's design principles and constraints
2. **Patterns** (how) — established templates for solving recurring problems
3. **Proposals** (what) — specific changes to the codebase

Each layer must align with the one above it:
- Patterns must embody the philosophy
- Proposals must follow established patterns
- A proposal that requires a pattern change must propose the pattern change FIRST

This ordering is enforced throughout the audit. You cannot propose code changes that violate patterns without first proposing the pattern change and justifying why the pattern should evolve.

## Phase 0: History Analysis

Read `audit-history.md` in full. This documents every previous audit round — what
problems were found, what proposals were implemented, and how the codebase evolved.

Before doing any new analysis, understand the trajectory:
- What has been tried before?
- What patterns emerge across rounds?
- Are there areas where repeated proposals haven't converged on a stable solution?
- Are there areas where early proposals held and remained stable?

You will use this understanding in every subsequent phase. Do NOT repeat proposals
that have already been tried and superseded. Do NOT flag violations in code that was
deliberately introduced by a previous round's proposal without examining WHY it was
introduced and whether the original problem it solved still needs solving.

Output: a brief trajectory assessment — what's converging, what's not, what tensions
remain unresolved.

## Phase 1: Intent Recovery

Read the philosophy files:
- `design-philosophy-notes.md` — the user's verbatim words
- `design-philosophy-analysis.md` — how four agents understood those words

For each distinct claim, requirement, and constraint the user expressed in the notes:
- Is it represented in the agent analysis?
- Did any agent misinterpret it?
- Are there gaps — things the user said that no agent accounted for?

Output a coverage matrix and list of gaps/misalignments.

## Phase 2: Problem Extraction

The philosophy document describes a SOLUTION, not a problem statement. Work backwards.

For each element of the philosophy, ask: what problem does this solve? What pain was the user experiencing that led to this approach?

Cross-reference with the codebase: what in the current code manifests these problems?

Do NOT introduce problems the user did not have. Every problem must trace back to something the user said or something the philosophy implies. An optimization or complexity argument is an excuse about not solving the task.

Output an explicit list of PROBLEMS with evidence trails (philosophy source + codebase manifestation).

## Phase 3: Generalization and Research

For each problem, identify the general class it belongs to. Research whether the user's philosophy addresses that general class correctly.

You can do external research here. Understand the underlying generalization of what the user is tackling. Be careful not to introduce additional constraints or rules the user did not specify.

Output: for each problem, its generalization and whether the philosophy addresses it.

## Phase 4: Pattern Alignment Audit

Read `patterns.md` in full. For each pattern in the catalog:

### 4a. Pattern-to-philosophy alignment
Does this pattern still embody the philosophy? Has the philosophy evolved in a way that makes a pattern obsolete or contradictory? Flag any patterns that are no longer aligned with current philosophy.

### 4b. Pattern completeness
Search the codebase for sites that should follow this pattern. Compare against the pattern's instance list. Are there new sites that were added since the pattern was last cataloged? Do they follow the template or are they islands?

### 4c. Pattern health
For each pattern, assess:
- Are all instances still consistent with the template?
- Have local variations crept into any instance?
- Are there new modules or capabilities that should be wired into this pattern but aren't?

### 4d. Missing patterns
Are there recurring problem classes in the codebase that have no pattern? Places where multiple modules solve the same class of problem but each does it differently — suggesting a pattern should be established?

Output: pattern violations, new islands, unhealthy patterns, and candidate new patterns.

## Phase 5: Codebase Violation Audit

Now audit the codebase against BOTH the philosophy AND the patterns. For each principle in the philosophy and each template in the pattern catalog, search the codebase for violations.

Key areas to examine in the codebase:
- `scripts/scan.sh` — Stage 3 coordinator
- `scripts/section_loop/` — Stages 4-5 orchestrator (decomposed package)
- `scripts/workflow.sh` — Schedule driver
- `scripts/db.sh` — SQLite coordination database
- `agents/*.md` — Agent definitions
- `implement.md` — Pipeline documentation
- `SKILL.md` — Skill entry point
- `templates/*.md` — Schedule templates
- `tools/` — Extraction tools
- `tests/` — Integration test suite
- `pyproject.toml` — Project configuration and test dependencies

**Use your Phase 0 history analysis here.** For each violation you find, check the
audit history to understand whether this code was introduced by a previous round.
If it was, you must account for the problem it was solving — don't just flag the
symptom, understand the full context.

Output: violations with evidence (noting whether each is a philosophy violation, a pattern violation, or both), plus newly discovered problems merged with Phase 2 list.

## Phase 6: Proposal Generation

Proposals come in two tiers. Tier 1 (pattern changes) must be resolved before Tier 2 (code changes) can be finalized, because code proposals depend on the pattern state.

### Tier 1: Pattern Proposals

For each pattern issue identified in Phase 4, propose one of:
- **New pattern** — a template that should be established for a currently un-templated problem class. Specify the template, canonical instance, known instances, and why it's needed.
- **Pattern update** — an existing pattern whose template should evolve. Specify what changes and why. Reference what went wrong or what new capability the current template doesn't cover.
- **Pattern deprecation** — a pattern that should be removed because the problem class no longer exists or the philosophy no longer supports it.

Pattern proposals must:
- Trace to philosophy — every pattern must embody a philosophical principle
- Be grounded in codebase evidence — the problem class must actually recur
- Not create islands — the proposed template must be followable by all affected sites

### Tier 2: Code Proposals

For each problem in the merged list, write a concrete proposal to solve it.

Constraints on code proposals:
- Must respect the user's philosophy
- **Must follow established patterns** — if a code proposal requires a pattern that doesn't exist, the pattern proposal (Tier 1) must come first
- **Must not introduce islands** — if the proposal handles a problem class that has an established pattern, the proposal must use that pattern
- Can diverge from the user's specific proposal IF the alternative solves the same problems
- Must address the scaling concern: brute force leads to countless cycles; strategy collapses cycles
- Must consider how strategic agents address friction between isolated islands of lower layers
- Must consider model selection: some models follow directions, others think strategically
- Must consider that agent file definitions carry the method of thinking

**Use your Phase 0 history analysis here.** If the history shows that a similar
proposal was tried before and led to problems, your proposal must account for that.
Explain how your proposal avoids the same outcome. If you can't, acknowledge the
tension explicitly rather than proposing something that will be undone next round.

## Phase 7: Cross-Proposal Validation

Look over ALL proposals (both tiers) together:
- Do they conflict with each other?
- Do they collectively cover all problems?
- Do they align with the user's philosophy when viewed as a whole?
- Does the combination respect scaling, convergence, and token-efficiency constraints?
- Does the total set address the meta-concern: strategy over brute-force, big-picture understanding over isolated wave-solving?
- **Do all Tier 2 proposals conform to the pattern state that would exist AFTER Tier 1 proposals are applied?**

**Convergence assessment:** Based on the audit history and your current proposals,
is the codebase approaching a steady state? Are your proposals moving toward
convergence or introducing new instability? Be honest.

If conflicts or gaps exist, revise proposals.

## Phase 8: Pattern Catalog Maintenance

Based on your audit findings and accepted proposals, produce the updated `patterns.md` content. This includes:

- **Updated instance lists** — new sites discovered in Phase 4b
- **Resolved islands** — islands that your proposals will eliminate
- **New patterns** — from accepted Tier 1 proposals
- **Updated templates** — from accepted Tier 1 proposals
- **Deprecated patterns** — removed entries
- **Updated health assessments** — reflecting current codebase state

Output the complete updated `patterns.md` as a separate artifact, clearly marked. The audit consumer will replace the existing `patterns.md` with this output after implementing the round's proposals.

## Output Format

Write your complete audit to stdout. Structure it as:

```
# Execution Philosophy Audit Results

## Phase 0: History Analysis
[trajectory assessment from audit-history.md]

## Phase 1: Intent Recovery
[coverage matrix, gaps, misalignments]

## Phase 2: Problems
[numbered problem list with evidence]

## Phase 3: Generalizations
[problem generalizations and research findings]

## Phase 4: Pattern Alignment
[pattern violations, new islands, unhealthy patterns, candidate new patterns]

## Phase 5: Violations
[codebase violations with evidence — philosophy and/or pattern violations]

## Phase 6: Proposals

### Tier 1: Pattern Proposals
[pattern changes — new / update / deprecate]

### Tier 2: Code Proposals
[one proposal per problem, conforming to post-Tier-1 pattern state]

## Phase 7: Integration
[cross-proposal validation, conflicts, convergence assessment]

## Phase 8: Updated patterns.md
[complete updated pattern catalog — ready to replace existing file]

## Summary
- Problems found: N
- Pattern violations found: N
- Codebase violations found: N
- Pattern proposals: N (new: X, update: Y, deprecate: Z)
- Code proposals: N
- Islands identified: N (justified: X, to resolve: Y)
- Conflicts resolved: N
- Overall assessment: [converging / cycling / fundamental issues]
```
