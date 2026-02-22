# Evaluate Proposal: Alignment Review

### Terminology Contract

"Evaluation" means **alignment review** — checking whether a proposal's
mechanisms faithfully operationalize the stated intent and constraints. It
NEVER means feature-coverage auditing. Proposals describe strategies, not
feature lists. Evaluation checks coherence between layers (problem →
proposal → mechanisms), not completeness against a checklist.

Review a proposal against the project's alignment document, design constraints,
and implementation feasibility. This is the critical gate between research and
implementation — catching misalignment here prevents wasted implementation work.

**You are interpreting intent and identifying what the strategy does that may not
align with that intent.** The alignment document (or design baseline) IS the
evaluation standard.

## Step 0: Gather Evaluation Context

Read these documents (search for them if paths not provided):

1. **The proposal** being evaluated
2. **Alignment document** — the intent standard
3. **Design baseline** — if exists: `constraints/`, `TRADEOFFS.md`, `patterns/`
4. **Design principles / long-term goals**
5. **Current state assessment**
6. **Original research prompt**
7. **Prior audit results**

## Step 1: Intent Alignment Check

For each major element of the proposal:

### 1a: Problem Drift
- Does this still solve the ORIGINAL problem, or has it drifted?
- Are we solving a harder problem than necessary?
- Are we solving an easier problem that misses the point?

### 1b: Mechanism Alignment
- Do the proposed mechanisms match the project's philosophy?
- Is the proposal routing when the project extracts? Or vice versa?
- Does it introduce abstractions where the project prefers simplicity?

### 1c: Authority Alignment
- Who/what has authority in the proposal? (LLM? Tests? Markers? Human?)
- Does that match the project's authority model?

## Step 2: Constraint Validation

Check EVERY design principle. Not just the obvious ones.

For each principle:
1. Read the principle
2. Ask: "Does the proposal violate this?"
3. If yes, document with specific quotes from both documents
4. If tension but not violation, note as MINOR

## Step 3: Implicit Constraint Discovery

- Pattern consistency — does it break established patterns?
- Toolbox clarity — will agents know what to do?
- Unstated assumptions about runtime, model capabilities, codebase?

## Step 4: Tradeoff Analysis

- What are we giving up? What are we gaining? Worth the cost?
- Worst case if wrong? How reversible? Blast radius?

## Step 5: Actionability Assessment

- Data structures defined or just named?
- Can an implementor read this and know what to build?
- Hand-waves hiding real complexity?

## Step 6: Three-Tier Verdict

Classify every major element:

### Accept
Mechanisms that faithfully operationalize the alignment document's intent.

### Reject
Mechanisms that actively violate the alignment document's intent or produce
the exact failure modes it warns against. Must be redesigned.

### Push Back
Mechanisms that aren't wrong per se, but are underspecified, underweighted,
or too rigid relative to the alignment document.

For each finding:
1. **Name** the specific mechanism
2. **Cite** the specific alignment principle or constraint
3. **Explain** the gap (intent vs implementation)
4. **Describe** the failure mode if left uncorrected

## Step 7: Write Evaluation Report

Write to `<research_dir>/evaluation-report.md`:

```markdown
# Proposal Evaluation: <title>

## Summary Verdict
<ACCEPT / ACCEPT WITH MODIFICATIONS / NEEDS REFINEMENT / REJECT>

## Accepted Elements
## Rejected Elements
## Push Back Elements
## Constraint Compliance
## Tradeoff Assessment
## Actionability
## Open Questions for Human
```

## Step 8: Present to User

1. Summary verdict (1 line)
2. Top 3 accepted elements
3. Top 3 rejected elements
4. Top 3 push-back elements
5. Key tradeoffs
6. Open questions requiring human decision

**Do NOT proceed to implementation until the user approves.**

## Anti-Patterns

- **DO NOT rubber-stamp** — every proposal has issues. Find them.
- **DO NOT reject on style** — evaluate substance, not formatting.
- **DO NOT add your own requirements** — evaluate against existing constraints.
- **DO NOT evaluate in isolation** — read the alignment doc, constraints, codebase.
- **DO NOT skip the actionability check** — a brilliant proposal that can't be
  implemented is worse than a mediocre one that can.
