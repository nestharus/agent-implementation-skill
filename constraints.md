# Discover Constraints: Surface What's Implicit

When introducing anything new — a proposal, a design change, a new module —
there are always constraints that aren't written down. This skill surfaces
them, validates against existing principles, and ensures nothing silently
violates the project's philosophy.

**Key insight**: Violating one principle usually means violating several,
because they reinforce each other.

## Step 1: Gather Existing Constraints

Read all constraint/principle documents:
1. Design principles — `LONG_TERM_GOALS.md` or equivalent
2. Constraint stores — any explicit constraint tracking files
3. Memory files — accumulated knowledge about project rules
4. CLAUDE.md / project instructions — operational constraints

Build a numbered list of ALL explicit constraints.

## Step 2: Read the Change Being Introduced

For each major element, note:
- New data structures, modules, parsing approaches
- New authority relationships, dependencies

## Step 3: Principle-by-Principle Validation

For EACH design principle:
```
Principle: <text>
Change element: <what's being introduced>
Violates? YES / NO / TENSION
If yes: <specific mechanism of violation>
```

Do NOT skip principles that seem unrelated. The most dangerous violations
come from principles you didn't think were relevant.

## Step 4: Implicit Constraint Discovery

### 4a: Codebase Patterns
How do existing modules handle similar problems? What conventions exist?

### 4b: Language/Runtime Constraints
Does the change assume a specific language? (Check `core/language.py`)

### 4c: Authority Boundaries
Does the change shift authority without acknowledging it?

### 4d: Reversibility
Can this change be undone? Does it commit to hard-to-change schemas?

### 4e: Toolbox Clarity
Will agents know what to do with this new element?

## Step 5: Classify and Prioritize

```
CONSTRAINT: <description>
TYPE: Explicit / Implicit / Emergent (pattern-based)
SEVERITY: BLOCKING / IMPORTANT / ADVISORY
SOURCE: <evidence>
AFFECTED: <which parts of the change>
```

## Step 6: Discuss with Human

Present:
1. Blocking constraints — must resolve before proceeding
2. New implicit constraints — "Should we document these?"
3. Tensions — not violations, but things to be aware of
4. Pattern breaks — established conventions the change deviates from

Ask: "Are there constraints I'm missing? Are any of these wrong?"

## Step 7: Update Documentation

For confirmed constraints:
1. Add to appropriate constraint document
2. Update memory files
3. Note impact on other modules
4. Propose addition to LONG_TERM_GOALS.md if new principle

## When to Use

- Before implementing a proposal
- When a research response diverges
- When you notice a pattern ("we always do X")
- When something feels wrong
- After memory corrections
- When the user says "that's not what I meant"

## Anti-Patterns

- **DO NOT invent constraints** — discover from evidence
- **DO NOT assume constraints are obvious** — if not written, it needs to be
- **DO NOT resolve constraints yourself** — present to user for decision
- **DO NOT skip the principle-by-principle check**
- **DO NOT treat "the code does it this way" as absolute** — code may be wrong,
  but it IS evidence of a pattern
