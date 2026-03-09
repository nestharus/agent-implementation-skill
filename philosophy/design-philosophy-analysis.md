# Design Philosophy Analysis (2026-02-21, updated 2026-02-28, 2026-03-07)

Four agents analyzed the verbatim notes in `design-philosophy-notes.md`. Each focused on one aspect.

---

## 1. Recursive Problem-Proposal Structure

The algorithm is a single recursive pattern applied at every scale:

**Nodes**: Problem, Proposal, TODO block, Implementation code. These exist at every level.

**Edges**:
- Decomposes-into: Problem → Proposal → [new Problems] → [new Proposals] → ... → TODO blocks → Code
- Aligns-with: Code aligns-with TODO blocks aligns-with Proposal aligns-with Problem aligns-with [upper Problem]

**Operations at each level** (same four, always):
1. **Explore** — understand the territory (heuristic, not exhaustive)
2. **Recognize problems** — identify what must be solved (not features to implement)
3. **Propose** — write strategies addressing recognized problems
4. **Align** — check proposal addresses problems; check problems are within scope of layer above

**Going deeper**: A proposal creates new problems. Each sub-problem starts the cycle again at a lower level. Recurse until problems are small enough for TODO blocks, then code.

**Going back up** (three triggers):
1. **Scope expansion** — solution requires solving problems not encompassed by the layer above → must reframe at root
2. **Incompleteness** — agent can only partially solve → signals new problems upward with WHY
3. **Alignment failure** — proposal doesn't align with its problems or with the layer above

**Key insight**: This is not a pipeline with phases. It is a single recursive operation applied fractally from architecture down to individual functions.

---

## 2. Exploration and Heuristics

**Stage 1 (Map Building)**:
- Input: Raw codebase structure
- Method: Heuristic selection — recognize folder structures, select folders to investigate, select files within, summarize
- Output: Skeleton map — what packages contain, project shape, routing information
- Explicitly NOT: Complete understanding, file-by-file analysis, exhaustive scanning

**Stage 2+ (Problem-Directed Exploration)**:
- Input: A specific problem + the skeleton map
- Method: Use map to theorize locations, targeted reading in those locations
- Output: Relevant files identified, local understanding of the problem region

**Heuristics concretely**:
1. Folder selection: "This is named `orchestration/`, probably workflow coordination"
2. File selection: "`promotion_loop.py` sounds like the core state machine"
3. Depth: "I understand this package handles promotion — enough to route back here later"
4. Relevance: "My problem involves gap detection, map says `gap_detection.py` exists"

**Critical properties**:
- Exploration never stops — it happens at every level of recursion
- The map is not static — deeper work refines relevant areas while leaving others coarse
- Purpose is routing, not cataloging
- Cost of occasionally routing wrong << cost of exhaustive scanning

---

## 3. Upward Signaling and Scope

**Resolution paths**:
1. **Solved locally**: Solution stays within scope granted by parent layer → done
2. **Partially solved**: Agent reports what was solved + new problems + WHY → problems join parent layer's set
3. **Bubbles to top**: No matching code (greenfield), no foothold in existing system → new concern at root

**Decision rule**: Scope containment. Can this be solved without exceeding scope from above? Yes → local. No → signal up.

**What travels upward**: Problem statements (not solution requests), explanations of WHY, context about what was attempted. Agent does NOT say "I need X." It says "here is a problem I cannot solve within my scope."

**Scope expansion**: MUST happen at the root. Cannot expand scope at intermediate layer without updating root. Alignment must hold all the way up.

**Brownfield vs greenfield**:
- Brownfield: Exploration found matching code → work in context of existing application
- Greenfield: Exploration found nothing → pure research, bubbles to top, adds new sections
- Hybrid: Brownfield work discovers greenfield gaps → needs external research, signals upward

---

## 4. Alignment vs Audit

**Audit** (rejected): Feature coverage check against a static spec. "Is everything present?" Impossible here because:
- Plans describe problems, not features
- Features are emergent (generated continuously)
- No static feature list exists
- Tool creation continuously changes what's possible

**Alignment** (correct): Directional consistency check between adjacent layers. "Does this layer serve the layer above it?"

**Alignment checks at each boundary**:
| Boundary | Question |
|----------|----------|
| TODO blocks ↔ Section proposal | Do microstrategies address the section's problem? |
| Code ↔ TODO blocks | Does implementation fulfill the microstrategy? |
| Section proposal ↔ Global proposal | Does this section's strategy serve the global problem? |

**Why alignment works and audit doesn't**:
- Alignment is relational and directional (adjacent layers)
- Audit is enumerative and flat (implementation vs checklist)
- Alignment handles incompleteness (new problems to propagate)
- Audit treats incompleteness as failure (features not covered)
- Alignment is present-tense ("right now, does this serve the layer above?")
- Audit is past-tense ("does this match a specification written before?")

**Tool-creation feedback loop**: Bottom layer creates tools → tools enable new proposals at higher layers → proposals change → alignment re-checked at each boundary. Static audit would be immediately obsolete.

**Core distinction**: Audit asks "is it done?" Alignment asks "is it coherent?" The system is never done.

---

## 5. Context Optimization (2026-02-28)

**The dual nature of context**: Every token of context is simultaneously
information and noise. Context that helps an agent understand its role
and make decisions is valuable. Context that describes parts of the
system the agent will never interact with is actively harmful — it
creates opportunities for role confusion, scope creep, and decision
degradation.

**Context rot**: When agents receive the entire system description, they
accumulate irrelevant details that compound across the conversation. A
philosophy source classifier reading about coordination databases will
subtly interpret its task differently than one that only knows about
philosophy files. The effect is not dramatic — it's gradual degradation
of decision quality that is invisible until the agent produces wrong
output.

**The pragmatism trap**: "Include everything so nothing is missing" feels
safe but creates the opposite problem. Missing context is recoverable
(agent can signal NEED_DECISION). Context rot is not recoverable (agent
silently makes wrong decisions). The correct trade-off favors minimal
context with recovery mechanisms over maximal context with no escape
hatch.

**What agents need**:
1. Their method of thinking (agent file)
2. Their specific task (prompt with file paths)
3. Their priorities and decision-making framework
4. Knowledge of what they can launch or interact with
5. Nothing else

---

## 6. Agent Lifecycle and Persistence (2026-02-28)

**Long-running agents degrade**: Agent reliability is inversely
proportional to context length. As context grows, agents compact
(summarize history), which means they lose specifics and operate on
increasingly abstract understanding. This manifests as:
- Implementing only part of a task (lost sight of the full scope)
- Re-deriving decisions from scratch (don't trust compacted conclusions)
- Drifting from the original task (context rot from accumulated noise)

**Strategists are most vulnerable**: Strategist agents must hold complex
understanding across multiple files, decisions, and dependencies. When
compacted, this understanding collapses into generic summaries that
don't support specific strategic decisions. The fresh strategist that
resumes doesn't trust the summary — it re-reads files and re-derives
conclusions, wasting all the original work.

**Solution: short-lived agents on persisted state**: Instead of one
long-running strategist, use a sequence of short-lived agents that each:
1. Read the structured decision history (what was decided and why)
2. Make the next decision
3. Persist their decision with evidence
4. Exit

Each agent starts fresh with targeted context. The history is structured
enough that the next agent can act on it without re-derivation. The
key insight: agents don't need to trust their own previous conclusions
if the conclusions are persisted as structured artifacts with evidence
trails.

**The roadmap pattern**: A structured file showing:
- What decisions were made (with reasons and evidence)
- What remains to be decided
- What the current strategic understanding is
- What the next action should be

A fresh agent reads this and acts on the next item. It doesn't need to
understand the entire history — just enough to make one decision.

---

## 7. Task Submission Over Agent Spawning (2026-02-28)

**Direct agent spawning creates invisible trees**: When an agent
dispatches sub-agents directly, the execution tree becomes invisible
to monitoring. The spawning agent stays alive accumulating context
while sub-agents run. Sub-agents may be spawned without proper
constraints (no agent file).

**Tasks decouple submission from execution**: Agents should declare
what needs to happen next, not execute it themselves:
1. Agent writes a structured task to a queue
2. Agent signals completion and exits
3. Script-level dispatcher reads the queue
4. Dispatcher launches the next agent with proper constraints
5. Next agent starts fresh with targeted context

This pattern:
- Prevents long-running agents (each agent does one thing and exits)
- Makes execution visible (script controls all launches)
- Enforces agent files (script validates before dispatch)
- Enables monitoring (script can track all active agents)

**The UI/orchestrator special case**: The UI agent that receives user
commands must be thin — it kickstarts processes and listens for
messages, but never accumulates implementation context. It submits
tasks to the queue rather than building prompts and dispatching
agents directly.

---

## 8. Agent Safety and Behavioral Constraints (2026-02-28)

**Every agent must have an agent file**: Agent files define the
reasoning method, constraints, output contract, and anti-patterns.
An agent dispatched without an agent file is "rogue" — it interprets
the task arbitrarily, uses any method, and produces any format. This
is a system safety invariant:
- Scripts enforce that every dispatch includes an agent file
- No agent may run without behavioral constraints
- The dispatch function rejects calls without agent files

**Dynamic agents need templates, not full alignment**: Dynamically
created agents (per-section specialists, intent layer agents) cannot
go through full philosophy alignment — the cost is prohibitive. But
they also cannot be unconstrained. Templates provide the middle ground:

- **System Constraints section**: immutable rules that all agents must
  follow (structured output, no sub-agent spawning, NEED_DECISION
  signaling, file-path-only operation)
- **Method of Thinking section**: dynamic content filled from the
  proposal/context
- **Output section**: dynamic format specified by the template

The template enforces safety while allowing flexibility in the
task-specific reasoning. This is analogous to how agent files work
for static agents — but for agents created at runtime.

**What makes a good template**: It constrains behavior without
constraining strategy. The agent can reason flexibly about its
problem while being structurally unable to violate system invariants.
A template that is too rigid defeats the purpose of agent-driven
reasoning. A template that is too loose allows rogue behavior.

---

## 9. Our System vs The Target System (2026-02-28)

Two distinct systems exist and the philosophy applies differently to
each:

**Our system** (the pipeline): `section_loop/*.py`, `scan/*.py`,
`substrate/*.py`, `agents/*.md`, `scripts/*.sh`. This is Python +
Bash + Markdown. We control it, we know the language, we define the
schemas. Language-specific enforcement (AST, linting, required
parameters, schema validation) is legitimate engineering on our own
code.

**The target system** (what the pipeline operates on): Any codebase
in any language. This is where agents must reason flexibly without
hardcoded language assumptions. No AST, no regex parsing, no
language-specific heuristics.

**Where the boundary matters**:
- Agents strategizing about exploration, understanding, and
  modification of code → target system → flexibility required,
  no language assumptions
- Scripts enforcing dispatch contracts, signal schemas, agent file
  requirements → our system → mechanical enforcement is correct
- Agents operating within our pipeline (producing signals, following
  output contracts) → our system → can be schema-validated

**The confusion this resolves**: The philosophy says "no hardcoded
language assumptions" and "agents reason, scripts dispatch." Without
the system distinction, this gets misapplied to our own engineering.
We end up afraid to write a required Python parameter because it
"hardcodes a language assumption." That's wrong — it's us enforcing
our own API.

---

## 10. Testing Philosophy (2026-02-28)

**Minimal high-signal tests**: Quality over quantity. Every test must
verify a meaningful behavioral contract, not merely assert that code
exists in a particular form. The goal is confidence that the system
behaves correctly, not a high test count.

**What is legal**:
- **Component tests**: Tests on underlying systems with clear
  contracts — mail delivery, agent dispatch mechanics, signal
  reading, coordination database operations, hash computation.
  These are the building blocks. They have defined inputs and
  outputs. Component tests verify those contracts.
- **Integration tests with mocked agent boundary**: The existing
  pattern of mocking `dispatch_agent()` and running everything
  else real. These are valid when testing orchestration logic
  (how the script responds to different agent outputs).
- **Schema/structure linting**: Verifying that agent files contain
  required sections, that signal JSON matches expected schemas,
  that dispatch calls include agent files. This is enforcement
  on our own system's structural contracts.

**What is not legal**:
- **Absence-of-previous-behavior tests**: A test that says "this
  pattern used to exist and now it shouldn't" is coupled to
  historical accidents, not system correctness. If the behavior
  is wrong, enforce the right behavior — don't test for the
  absence of the wrong one.
- **Tightly coupled mocked tests**: Tests that mock internal
  functions to test other internal functions in isolation. These
  test implementation details, not behavior.
- **Quantity-driven testing**: Adding tests to increase a count.
  600-700 tests for a system this size suggests many are
  low-signal or redundant.

**The integration test problem**: Our system has essentially one
entrypoint that fans out everywhere. An integration test from that
entrypoint exercises the whole pipeline, which means it's testing
everything and nothing specific. Component tests on the building
blocks are more valuable because they verify specific contracts.

---

## 11. E2E Tests With Live LLMs (2026-02-28)

**Short-lived agents make E2E testing possible**: When agents were
long-running, their behavior spanned too much context to be
predictable or testable. Now that each agent is a single bounded
interaction (one agent file + one prompt → one decision), the
behavior is testable.

**Set scenarios with expected decisions**: Write specific situations
where a particular model, given a particular agent file and prompt,
should make a particular decision. These are manual tests that
verify the agent file + prompt combination produces the designed
behavior.

**The judgment problem**: If the LLM can't figure out its own
behavior, how can another LLM judge whether it was right? The
answer is: we don't ask an LLM to judge. We define scenarios with
mechanically verifiable expected outcomes — structured JSON signals,
specific verdict values, presence/absence of specific artifacts.
The test checks the output format and decision, not the reasoning
quality.

**What these tests verify**: That our agent files actually constrain
behavior as intended. That the method of thinking we encoded
produces the class of decisions we designed for. That model selection
(strategic vs directive) matches the task requirements. These are
the things that unit tests and mocked integration tests fundamentally
cannot verify.

---

## 12. Sections Are Concerns, Not File Bundles (2026-02-28)

**The anti-pattern**: Treating sections as collections of related files.
A section gets a file list from the codemap, and work is organized
around modifying those files. This is backwards. The file list is a
starting hypothesis produced by exploration — it can grow, shrink, or
change entirely as understanding deepens.

**The correct framing**: A section represents a **problem region** or
**concern**. The concern might touch many files or few. The concern
might require creating new files that weren't in the original list. The
concern might discover that some listed files are irrelevant. The
section's identity comes from the problem it addresses, not from the
files it happens to involve.

**Why this matters for the pipeline**: If sections are file bundles, then
cross-section coordination is about file overlap detection. If sections
are concerns, then cross-section coordination is about problem
interaction — one concern's solution may create friction with another
concern's constraints regardless of whether they share files.

---

## 13. Friction Between Isolated Islands (2026-02-28)

**The problem**: When concerns are processed in isolation (each section
handles its own problem independently), solutions can create friction
at boundaries. Section A defines an interface contract. Section B
assumes a different contract for the same interface. Neither section
knows about the other's decision until coordination discovers the
conflict.

**Strategic handling**: Friction between isolated islands should be
handled explicitly and visibly. The coordination phase does not just
detect conflicts — it routes targeted cross-region work to resolve
them. This work should be:
- **Visible**: submitted as explicit tasks, not hidden inside recursive
  agent trees
- **Scoped**: each resolution is a bounded problem, not a full re-run
  of both sections
- **Artifact-driven**: the resolution produces structured decisions
  that both sections can reference, not ad hoc fixes that may drift

**Connection to task submission**: This is one of the strongest
arguments for task submission over direct dispatch. When a strategist
detects cross-island friction, submitting it as a task makes it
observable, trackable, and resolvable under script control. Direct
dispatch would hide this work inside the strategist's execution tree.

---

## 14. Migration Contract Drift (2026-02-28)

**The pattern**: When a system migrates from one execution model to
another (e.g., direct dispatch → task submission, long-running
supervisor → short-lived agents), the migration creates a period where
both models coexist. During this period, new agent files encode the
target model while existing runtime surfaces still encode the old model.
The result is **split-brain**: the agent's method says one thing, its
runtime task says another.

**Why this is worse than either model alone**: An agent operating
entirely under the old model is at least consistent — it dispatches
sub-agents as instructed and the behavior, while not ideal, is
predictable. An agent with split instructions faces an impossible
choice: follow its method (submit tasks) or follow its prompt (dispatch
agents). The conflict degrades decision quality unpredictably.

**The fix**: Migrations must be **atomic per surface**. When an agent
file is updated to the new model, its runtime template must be updated
in the same change. This prevents the window where method and task
diverge. The audit system can then check for contract consistency as
a structural property rather than chasing per-surface regressions.

---

## 15. Proposal State and Execution Readiness (2026-03-06)

**The existing philosophy already implies one recursive loop regardless of project mode.**
Brownfield, greenfield, and hybrid are findings from exploration. They describe how much grounding already exists. They do NOT justify separate proposer roles, separate artifact shapes, or separate descent rules.

**A proposal is a problem-state artifact, not an implementation plan.**
At proposal time, the runtime needs a stable artifact shape that records:
- resolved anchors
- unresolved anchors
- resolved contracts
- unresolved contracts
- research questions
- user/root questions
- new concern candidates / scope deltas
- shared seam candidates
- execution readiness

Brownfield proposals may have more resolved fields. Greenfield proposals may have more unresolved fields. The artifact shape stays the same.

**Execution readiness is fail-closed.**
Descent into microstrategy or implementation is allowed only when blocking unresolved fields have been routed to their consumers and the section is materially ready. When readiness is false, the runtime must reconcile, seed substrate, request decisions, or reopen problem framing. It must not compensate by inventing local architecture during implementation.

**Implementation cannot silently repair missing structure.**
If implementation discovers that anchors, contracts, or boundaries were never actually resolved, that is not implementation freedom. That is evidence that the proposal/reconciliation layer must reopen.

---

## 16. Risk-Optimization as Strategy Generator (2026-03-07)

**The gap in the existing loop**: The recursive problem-proposal structure (explore, recognize problems, propose, align, descend) handles understanding and solution generation. It does not address what happens when execution encounters risk, when agents abandon methodology under pressure, or how the system should scale its process to the actual difficulty of what it is doing.

**Strategy as side effect**: If we define an agent that assesses risk factors in plan execution, and another agent that proposes tool/workflow adjustments to mitigate those risks, and continue that loop until risks are below threshold, then **adaptive goal-planning emerges without the system ever attempting the vague cognitive task of "being strategic."** Strategy is the consequence of managing risk well.

**The risk-tool ecosystem mapping**: The system already has known risks (**context rot**, **silent drift**, **scope creep**, **brute-force regression**, **cross-section incoherence**, **tool island isolation**, **stale artifact contamination**) and tools that mitigate them (short-lived agents, alignment checks, agent file enforcement, execution-readiness gates, reconciliation, tool registries, freshness gates). The risk-optimization loop makes this mapping **explicit and dynamic** rather than implicit and static. New risks can be identified. New tools can be proposed. The mapping between risks and mitigations is a first-class concern.

**Relationship to the problem-proposal loop**: The two loops operate in parallel. The problem-proposal loop handles **what** to build and **why**. The risk-optimization loop handles **how safely** and **how efficiently** to execute. Neither replaces the other. Together they produce work that is both correct and proportionally guarded.

---

## 17. Risk Quantification and Scaling (2026-03-07)

**The uniform-process problem**: Without risk quantification, the system either applies maximum caution uniformly (wasting cycles on trivial steps) or applies no caution (introducing defects on dangerous steps). Both failure modes are invisible until their consequences manifest as wasted tokens or quality failures.

**Strategies scale to risk level**: A trivial rename does not need the same process as a cross-cutting architectural change. Risk quantification enables **proportional guardrailing** — heavy process on high-risk steps, fast execution on low-risk steps. The system does not target zero risk. It targets **risk below a defined threshold with effort proportional to the actual danger.**

**Perceived risk vs surfaced risk**: Initial risk assessment is based on what is known ahead of time. After each execution cycle, risk is **reassessed based on what actually happened**. If earlier steps resolved unknowns, later steps may be reclassified as lower-risk and optimized accordingly. The system always operates on what it immediately knows rather than assuming about unknowns.

**Agents don't know what they know**: Agents cannot inventory their own understanding or identify gaps before acting. They jump to execution without assessing whether they have enough information, whether the step is risky, or whether their tools are appropriate. The risk agent externalizes this assessment — it evaluates the execution plan before the executing agent encounters the difficulty.

---

## 18. Multi-Step Acceptance and Unknown Horizons (2026-03-07)

**Partial acceptance**: In a multi-step task package, early steps may have risk below threshold (accepted) while later steps have risk above threshold (rejected). **Accepted steps execute immediately. Rejected steps wait.** This avoids the upfront full-solve-graph planning that the existing philosophy already rejects.

**Unknown horizons**: Rejected steps are not permanently blocked. They wait for accepted steps to produce outputs. Those outputs provide new information that enables **reassessment**. A step that was rejected due to uncertainty may become acceptable once earlier steps have resolved the unknowns it depended on.

**Connection to the recursive structure**: This is the risk-optimization analog of the problem-proposal pattern's incremental descent. Just as the proposal loop descends only when the current layer is ready, the risk loop executes only the steps that are currently safe. Just as unresolved proposals signal upward, rejected steps wait for information from accepted steps before re-entering assessment.

**What travels between iterations**: After accepted steps execute, the reassessment receives their outputs — what was produced, what unknowns were resolved, what new risks surfaced. This is analogous to the upward signaling in the recursive structure, where agents report what was solved, what new problems emerged, and why.

---

## 19. Oscillation Prevention in Adaptive Systems (2026-03-07)

**The oscillation failure mode**: Adaptive systems can swing between two extremes. **Over-caution** adds guardrails that waste cycles without proportional quality gain. **Over-correction** removes guardrails that were actually needed, causing failures that re-trigger heavy guardrails. The system oscillates between expensive-but-safe and cheap-but-broken.

**Incremental change**: Adaptations to risk posture should be incremental, not dramatic. A single failure should not trigger maximum guardrails. A single success should not trigger removal of all protection. The magnitude of adjustment should be proportional to the evidence.

**Convergence criteria**: The loop should stop adapting when two conditions are met simultaneously: (1) **risk is below threshold** — the current guardrail configuration is adequate, and (2) **optimization is not yielding significant savings** — further reduction in guardrails would not meaningfully reduce cost. Meeting only one condition is insufficient. Below-threshold risk with significant optimization opportunity means the system is being wasteful. Optimized execution with above-threshold risk means the system is being reckless.

**Risk history**: The system should maintain memory of what worked and what did not. Which guardrail adjustments led to failures. Which reductions were safe. This history prevents the system from re-learning the same lessons across iterations, and it provides evidence for the incremental-change principle — adjustments grounded in history are more stable than adjustments based on single data points.

---

## 20. Brute-Force Regression Detection (2026-03-07)

**The specific failure mode**: When agents encounter difficulty, they abandon their designed methodology and **brute-force toward task completion**. They stop exploring. They stop recognizing problems. They stop proposing strategies. They begin hammering at the task with whatever approach occurs to them, and output quality degrades dramatically.

**Why this happens**: Agents do not plan to plan. Standard planning is reactive — agents solve problems as they encounter them without first assessing whether they have sufficient information, whether the step is within their capability, or whether their tools are appropriate. When difficulty exceeds the agent's capacity under the current approach, the agent does not signal upward or request decomposition. It regresses to brute force.

**Detection signals**: Brute-force regression manifests as: abandonment of the structured exploration-recognize-propose-align pattern; rapid iteration without reassessment; ignoring alignment checks; producing outputs that do not match the designed output contract; expanding scope beyond the assigned task boundary.

**Intervention, not punishment**: The correct response to detected regression is not to penalize the agent. It is to **restructure the work**: add guardrails to the difficult step, decompose it into smaller sub-steps, gather more information before attempting it, select different tools, or escalate to a higher layer. The risk-optimization loop provides the mechanism — if risk assessment detects that a step is likely to trigger regression, the tool agent can propose mitigations **before the executing agent ever encounters the difficulty.**

**Connection to execution readiness**: Brute-force regression is often a symptom of premature descent. The proposal layer declared readiness, but the executing agent discovered that anchors, contracts, or boundaries were not actually resolved. The regression is evidence that the proposal/reconciliation layer should reopen — consistent with the fail-closed readiness principle from Section 15.
