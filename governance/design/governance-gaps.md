# Project Governance Gaps — Design

## Core move

Add a thin **governance state layer** above the existing runtime, not a replacement for it.

The current system already has the right execution primitives: planspace vs. codespace, problem frames, proposal/readiness/reconciliation, intent packs, bounded research, ROAL, consequence propagation, and a concern-based audit model. What is missing is a persistent, queryable governance layer that makes those outputs cumulative across runs.

The design below adds that layer in a way that matches the existing operating model:

- **codespace** holds the authoritative project governance documents
- **planspace** holds runtime mirrors, indexes, and per-run governance packets
- **audit / proposal / implementation** update the governance layer incrementally
- **post-implementation stabilization** becomes a first-class loop

## Governance hierarchy

1. **Problem archive** — why this code exists
2. **Philosophy profiles** — what values govern each problem region
3. **Pattern archive** — how those values are repeatedly operationalized
4. **System synthesis** — how code, problems, patterns, and philosophy connect
5. **Proposal / research / reconciliation** — change design under those constraints
6. **Implementation + ROAL** — execution under bounded risk
7. **Post-implementation assessment** — what new risks landed in the code
8. **Stabilization / refactoring** — reduce or remove those risks, then re-align

## Authoritative artifacts (codespace)

### 1) `governance/problems/`

Persistent problem records.

Files:
- `governance/problems/index.md`
- `governance/problems/PRB-XXXX-<slug>.md`

Each problem record should contain:
- stable `problem_id`
- status: `active | latent | resolved | obsolete | superseded`
- statement of the problem in project terms
- motivation / why it matters
- provenance: `user-authored | doc-derived | code-inferred | audit-inferred`
- confidence / authority level
- governing regions / sections
- governing philosophy profile(s)
- manifestation points (proposal fields, sections, modules, helpers, risk artifacts)
- solution surfaces already present
- related pattern IDs
- related proposals / audit rounds / decisions
- obsolescence criteria
- history of changes

This is the answer to “why does this code exist?”

### 2) `governance/patterns/`

Living catalog of recurring solution templates.

Files:
- `governance/patterns/index.md`
- `governance/patterns/PAT-XXXX-<slug>.md`

Each pattern record should contain:
- stable `pattern_id`
- problem classes it addresses
- philosophy principle(s) it embodies
- applicability conditions
- required invariants
- canonical implementation shape
- preferred helper/module surfaces
- known instances
- exceptions and migration notes
- conformance checks
- change policy: when deviation is allowed and when it blocks

This is the answer to “how do we solve this kind of problem here?”

### 3) `philosophy/` (extend existing folder)

Keep the existing global philosophy docs, but add **profiles** rather than forcing one philosophy doc per file or per transient section.

Files:
- existing `philosophy/design-philosophy-analysis.md`
- existing `philosophy/design-philosophy-notes.md`
- `philosophy/profiles/PHI-global.md`
- `philosophy/profiles/PHI-<profile>.md` (only where values materially diverge)
- `philosophy/region-profile-map.md`

A profile should define:
- values and priority ordering
- preferred failure mode
- acceptable risk posture
- anti-patterns
- pattern implications
- which regions / problem classes it governs

This is the answer to “what should this area optimize for, and how should it feel?”

### 4) `system-synthesis.md`

A new connective document.

Keep `codebase_system_design.md` as the architecture description of what exists. Add `system-synthesis.md` as the governance-facing overlay that answers:
- which problem records each region exists to solve
- which philosophy profile governs that region
- which patterns are supposed to apply
- which tensions and open incoherences remain

The synthesis doc should be region-oriented, not file-catalog oriented.

### 5) `governance/risk-register.md`

Persistent post-implementation risk ledger.

This is **not** ROAL’s pre-dispatch posture data. It records landed-code risks and accepted debt, such as:
- coupling / cohesion concerns
- security surface growth
- scalability or operability concerns
- pattern drift
- coherence friction with neighboring regions
- refactor obligations

## Runtime artifacts (planspace)

These should be mirrors and working packets, not independent truth sources.

- `artifacts/governance/problem-index.json`
- `artifacts/governance/pattern-index.json`
- `artifacts/governance/profile-index.json`
- `artifacts/governance/section-N-governance-packet.json`
- `artifacts/governance/section-N-post-implementation-assessment.json`
- `artifacts/governance/synthesis-delta.md`
- `artifacts/governance/bootstrap-report.md` (only during onboarding)

The governance packet for a section should contain:
- matched problem IDs
- governing philosophy profile(s)
- applicable patterns
- known exceptions
- risk rubric weights for that region
- unresolved governance questions

This packet is what proposal, implementation, alignment, ROAL, and post-implementation assessment consume.

## How the existing system should be extended

### Reuse, don’t replace

The design should build directly on what is already there:
- extend current traceability artifacts instead of inventing a parallel trace system
- extend current prompt-context assembly with a governance packet
- extend current freshness / section-input hashing so governance drift is visible
- extend the audit process into archive maintenance
- extend ROAL with a downstream landed-code assessment, not a new replacement loop

Concretely, the current traceability layer is the best seed:
- `artifacts/traceability.json`
- `artifacts/trace/section-N.json`
- `artifacts/trace-map/section-N.json`

Those should gain `problem_ids`, `pattern_ids`, and `profile_ids`, then feed promotions back into the authoritative codespace archives.

## Process changes

### A. Governance resolution at section setup

After problem-frame validation, before proposal writing:
1. build / refresh the section governance packet
2. try to match the section to existing problem records
3. identify governing philosophy profile(s)
4. identify applicable patterns
5. if no problem record matches, create a **candidate problem record**
6. if no profile is clearly applicable, escalate only if the value choice matters materially

This uses the existing section bootstrap slot, not a new top-level phase.

### B. Proposal stage becomes archive-aware

Integration proposals should reference:
- the problem IDs they claim to solve
- the patterns they intend to follow
- any pattern deviations they need

If a proposal requires breaking an established pattern, it must emit a **pattern delta** first:
- adopt exception
- replace pattern
- split pattern
- retire pattern

For structural work, the runtime should block code changes until the pattern delta is resolved. For low-risk local work, this can remain advisory.

### C. Reconciliation dedupes governance too

The existing reconciliation phase should normalize not only shared anchors and contracts, but also:
- duplicate problem discoveries across sections
- competing pattern interpretations
- region-boundary disagreements
- root-reframing implications

The output is not just a better proposal set; it is a cleaner governance graph.

### D. Implementation consumes a governance packet

Proposal, microstrategy, implementation, alignment, and ROAL prompts should all receive the same governance packet by file path.

This is the explicit philosophy-to-implementation bridge:
- problem frame says what the section is solving now
- governance packet says why it exists in the project, what values govern it, and what templates are expected

### E. Add a post-implementation assessment stage

After implementation and consequence propagation, run a bounded **post-implementation assessment**.

It should inspect the landed change through these lenses:
- structural coupling / cohesion
- security surface
- scalability / performance / bottleneck risk
- coherence with neighboring regions
- pattern conformance / drift
- operability / observability / maintenance burden

Its output should be one of:
- `accept`
- `accept_with_debt` → write to risk register
- `reopen_problem`
- `refactor_required`

### F. Refactoring becomes the stabilization loop

When post-implementation assessment returns `refactor_required`, the work re-enters the normal proposal / reconciliation / implementation machinery as a bounded stabilization problem.

That makes refactoring a pipeline stage rather than a heroic side process.

## What the audit process should do now

Audit becomes the maintenance mechanism for governance state.

For every audit round:
1. consult the problem archive before minting “new” problems
2. merge duplicate discoveries into existing records where appropriate
3. consult the pattern archive before recommending code changes
4. propose pattern deltas before code deltas when the real issue is template drift
5. refresh synthesis deltas
6. write risk-register candidates when the issue is landed-code debt rather than immediate correctness

### Hardcoded “Established Conclusions” should split into two classes

Keep a very small hardcoded set of true substrate invariants in the audit prompt:
- scripts dispatch, agents decide
- fail-closed structured parsing
- file-path-based prompts
- task submission, not direct spawning
- bounded typed substrate

Move project-level and runtime-evolved patterns out of prompt prose and into the pattern archive / digest.

## How this solves the 8 stated problems

### 1. No problem traceability
Solved by the problem archive, traceability enrichment, and governance packets. Code can be traced back to persistent problem IDs instead of rediscovered from logs.

### 2. No pattern governance
Solved by the pattern archive, pattern delta workflow, and archive-aware audit phase. Developers gain a canonical answer to “how do we do X here?”

### 3. Philosophy-to-implementation bridge
Solved by philosophy profiles + region mapping + pattern linkage + governance packets in prompts. Philosophy stops being purely implicit.

### 4. No implementation risk assessment
Solved by adding post-implementation assessment after landed code, not just pre-dispatch ROAL.

### 5. Non-uniform risk profiles
Solved by weighting risk assessment with:
- problem class
- philosophy profile
- pattern criticality
- blast radius / reversibility

Risk is contextual rather than uniform.

### 6. No project onboarding pathway
Solved by a governance bootstrap workflow with provenance states and different entry paths for greenfield, brownfield, PRD-first, and partial-governance projects.

### 7. Continuous refactoring isn’t a pipeline stage
Solved by making stabilization/refactoring an explicit stage triggered by post-implementation assessment.

### 8. No synthesis document
Solved by `system-synthesis.md`, which connects architecture, problems, philosophy, patterns, and tensions at region level.

## Bootstrapping by project state

### Greenfield
Start with:
- user-authored philosophy
- seed problem archive
- empty pattern archive (except maybe a few foundational runtime patterns)
- synthesis skeleton

Patterns emerge after implementation, not before.

### Existing codebase with no governance docs
Run a discovery-first bootstrap:
- infer regions from code structure
- infer recurring patterns from repeated implementations
- infer candidate problems from handling logic, failure modes, comments, tests, and historical fixes
- ask the user for philosophy, priorities, and corrections to inferred motivations

Mark inferred records as provisional until confirmed.

### PRD / external spec
Use the existing research / evaluation / baseline flow to extract:
- explicit problems
- constraints
- tradeoffs

Then overlay user philosophy and resolve conflicts between the spec’s implicit values and the user’s actual values.

### Partial governance
Run a gap assessment, not a rewrite.

Classify each artifact as:
- authoritative and healthy
- authoritative but stale
- provisional / inferred
- missing

Then fill the minimum missing layer in order:
1. philosophy
2. problem archive
3. synthesis
4. pattern archive
5. risk register

## What requires the user vs. what is discoverable

### Requires the user
- philosophy and priority ordering
- confirmation of inferred problem motivation when it is not explicit
- approval of broad pattern changes that alter tradeoffs
- acceptance of meaningful architectural or security debt
- decisions about retiring obsolete-but-still-used code

### Discoverable by the system
- manifestation points in code and planspace
- recurring implementation templates
- region boundaries and coupling
- current pattern conformance / drift
- landed-code risk surfaces
- whether a problem has already been seen before

## Design risks and how to manage them

### Governance bloat
Mitigation: only promote a problem or pattern when it is cross-run, cross-section, safety-critical, or likely to recur. Do not record every local convention.

### Stale documents
Mitigation: governance packets participate in canonical freshness / hashing. If philosophy, problem, or pattern inputs change, proposals should reopen the same way research and intent artifacts already do.

### Duplicate truth
Mitigation: codespace documents are authoritative; planspace artifacts are generated mirrors.

### False certainty from inferred records
Mitigation: every record carries provenance and confidence. Inferred motivation never silently becomes authoritative.

### Slowing implementation too much
Mitigation: make governance gates risk-scaled. Small local work can receive advisory governance checks; structural, cross-section, or safety-sensitive work gets fail-closed governance gates.

## Practical rollout order

1. Extend current traceability artifacts with `problem_ids`, `pattern_ids`, `profile_ids`.
2. Add codespace problem archive and pattern archive with generated indexes.
3. Add `region-profile-map.md` and section governance packets.
4. Thread governance packets into prompt context and freshness hashing.
5. Add post-implementation assessment + risk register.
6. Add `system-synthesis.md` and governance bootstrap report.
7. Move audit prompt’s non-universal established conclusions into the pattern archive digest.

## Bottom line

The right answer is not “more docs.”

The right answer is a **governance memory layer** that turns the system’s existing per-run artifacts into cumulative project knowledge:
- persistent problem memory
- living pattern memory
- explicit philosophy-to-region mapping
- synthesis of why / how / where
- a post-implementation stabilization loop

That gives the current runtime something it does not yet have: the ability to remember not just what it did, but why it keeps doing it.
