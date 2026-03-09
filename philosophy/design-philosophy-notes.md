# Design Philosophy Notes (Verbatim — 2026-02-21, updated 2026-02-28)

## Context
These are the user's verbatim notes during alignment review of the agent-implementation-skill pipeline. They describe a philosophical approach to problem solving that goes beyond the original pipeline design.

---

## On Alignment vs Audit

Some of these may need to be investigated further. For example, what does signal detection by regex mean?
Dispatch, don't absorb, is for agents. A script has no limit on what it can absorb.
section-loop has old behavior from old methodologies that were never actually addressed. the file behavior instead of problem behavior. sections represent concerns.
it looks like the code is having agents audit rather than align. Plans are incomplete. There's nothing to really audit because plans don't describe features. They describe problems. The feature descriptions are scattered throughout the code in the TODO blocks with the overall proposal in an md file for alignment. That particular proposal IS a feature dock. An implmentation plan. However, that proposal is already broken across TODO blocks so there is no audit to perform. There is only alignment that the TODO blocks solve the problem the proposal is trying to solve. Proposal doesn't outline specific implementation details. It outlines strategies. The TODO blocks are the microstrategies for the current location. They describe general approaches for this section of code. They still aren't the raw algorithms. Do the TODO blocks align with the overall from the proposal of its section? Then finally does the actual implementation code align the TODO blocks? We never perform audits for feature coverage because we continue to generate as we go on.

This hierarchy scales. We can continue to write proposals and proposals and propsoals for regions until we finally get to our TODO blocks.

## On Strategic Exploration

On top of this, we still have strategy. Strategic exploration. We work to understand the problem we are trying to solve (a step I skipped). Each region has its own problem that needs to be solve. Each region introduces its own challenges. We must first recognize the problems. This is what we must address. I believe "that" is our proposal. We never know a solution. We only know problems. We continue to decompose problems all the way down. THEN we start to solve our tiny problems. We align to see if we're solving our actual problems. We never do feature coverage. Our proposal becomes our code. When we write our code, it is important to also write our proposal. We need to summarize all the way up so that we can understand the proposal at each layer. So that we can understand the problems, and whether they align with the upper problems.. and so that we can understand the proposal, and whether it aligns with our problems.

## On Scanning and Heuristics

I am discussing a philosophical approach to problem solving right now. There are additional concerns. The scanning side. How do we understand the codebase we are working with? We heavily utilize heuristics. Recognizing folder structures. Projects. Selecting a folder to investigate/understand. Selecting specific files in that folder to get a better understanding. Summarizing all the way around to understand the general shape of the project and general concerns. This isn't exhaustive. We rely on heuristics. We don't need to know every single detail or every single file. This is incredibly important. We need a general understanding of the skeleton. That skeleton generlaly tells us what packages contain. This helps us with routing. It helps us theorize about what we may need to check for our actual problems. Where we may need to actually search. THat is the point of this codemap. Any attempt to analyze each and every single file and produce a complete understanding of the codebase is missing the entire point. We don't need to scan the entire codebase to solve our problem. THat is incredibly wasteful. We need enough information so that we can find relevant files. So when we are looking at problems.. we do another stage of exploration. First stage gives us the map. Second stage (and so forth) uses that map. We are always exploring. Exploring is a part of how we do problem solving at every single level. We're always writing proposals. We're always defining and understanding problems. We never stop.

## On Tools and Adaptation

The REALLY cool thing is that we don't have to just write code at the bottom layer. The bottom layer gives us new tools. That allows us to use tools and at the upper layer to define a proposal. We may need to write new tools. Connect tools together. Understand friction. This allows us to REALLY adapt at every layer. We may need to deploy agents to other regions to get us new tools. We are crafting a thesis of our proposal based on discovery and proposals that we get. Those proposals may be incomplete. They may partially solve a problem. We need to recognize that. The underlying agents also need to explain WHY they partially solved it. These are the notes.. part of the proposal. That the agent is stating "there are new problems. Here they are. We need these answers to continue." The underlying code may also be incomplete. The underlying proposals, TODOs, etc may be incomplete. We can always signal new problems. Those problems become part of that upper layer. That upper layer can try to solve and if they can't they signal upwards. When we attempt to solve these problems we do so in the context of the existing application. If the problems are completely brand new ground.. meaning that there is nothing to explore.. they will bubble to the very top layer where they will be recorded as brand new concerns/ground to cover. When we have to cover this new ground, this is where we have to do research.. this can happen when there are gaps in our current application as well.. partially solved problems. We need to do external research with the context we have. We have to bubble this stuff upwards because if we solve things that are out of scope of above problems.. then we're no longer aligned. We have to align all the way up. If we are adding scope then we must solve at the root. So any time we solve new problems that aren't encompassed by higher level problems, we have to go up and reframe what we are trying to do.

## On Greenfield vs Brownfield

If no code matches in exploration.. that means greenfield.. new ground. We covered brownfield. Greenfield is pure research. Greenfield adds new sections.

## On Mode as Observation, Not Routing (2026-03-06)

Greenfield, brownfield, and hybrid are observations from exploration. They tell us what we found. They do not tell us to run a different planning algorithm. The loop is still explore, recognize problems, propose, align, and only descend when the current layer is actually ready. We should not fork proposer roles, proposal artifact shapes, or descent rules by mode. Mode can remain as telemetry or as one weak input into judgment, but it should not be the routing key that turns planning into local architecture invention.

## On the Recursive Structure

I think where things are getting confusing. We have problems. Then we have proposals. We execute on proposals. We align proposals to problems. Proposals create new problems. What must we solve to satisfy the proposal? Then we create a new proposal. Does this new proposal align to our new problems? Does it align to the problems from the upper proposal that we are responsible for solving?

I am kind of jumping around here as I refine my understanding of this algorithm. It seems like it is more complex than "sections".

## On Scaling and Why Strategy Matters

There is a scaling problem that is not necessarily revealed that the user's proposal addresses. If you brute force rather than rely on the heuristics and strategy, you can run into scaling issues that require countless cycles. If all agents only work in isolation then we solve small little waves of problems without ever understanding the big picture. The system eventually settles, but it settles after MANY cycles. With strategy we can understand where we are going to and solve many waves of problems in one go. This of course can uncover new problems. The point is we do so with far fewer tokens and far fewer cycles at the same quality.

## On Friction Between Isolated Islands

The user also looks at how agents can strategically address friction between isolated islands of lower layers. Either by addressing that friction directly or deploying new agents depending on complexity.

## On Model Selection and Agent Definitions

However, this requires more of the model and is far more sensitive to agent file definitions because we have to impart on those agents a method of thinking and we have to select models that can think and adapt rather than just following directions. Some models follow directions. Others think and adapt strategically.

## On Proposal Evaluation

We don't have to follow the user's proposal if we come up with a superior proposal. However, that superior proposal MUST solve the same problems. An optimization or complexity argument is an excuse about not solving the task you were assigned to do. You need to be very careful that you do not introduce any additional constraints or rules that the user did not specify as doing so will no longer be solving their problems.

## On Proposal Artifacts as Problem State (2026-03-06)

The proposal is not "which files do I change?" unless those anchors are already resolved. At this layer the proposal is the current state of understanding of the problem. What anchors are real? What anchors are unresolved? What contracts are already grounded? What contracts are still open? What research questions remain? What user or root questions remain? What new concern space did we discover? What shared seams may require reconciliation or substrate work? Are we actually ready to go down another layer?

That shape should stay the same whether we are in brownfield or greenfield. Brownfield means more of the state is already resolved against the existing application. Greenfield means more unresolved state. It does not mean the artifact becomes an implementation plan or a scaffolding checklist.

## On Execution Readiness (2026-03-06)

We need a fail-closed idea of readiness before implementation. If unresolved anchors, unresolved contracts, root questions, or shared seam questions still exist, then we are not ready to descend. At that point the runtime should route upward, reconcile across sections, or seed the minimum substrate. It should not let implementation absorb the ambiguity.

Implementation can solve approved local problems. It cannot silently compensate for missing structure. If the implementer discovers that the proposal omitted a structural decision, that is evidence we need to reopen the proposal/reconciliation layer, not permission to invent architecture in place.

---

## On Context Optimization (2026-02-28)

SKILL.md is a way to optimize context. Not all agents need to know about all agents. Agents need to know about the things they potentially launch. We need to optimize context to keep agents on track. Optimizing context still also means that agents need to know methods of thinking AND priorities so that they can make the right decisions. They just don't need to know the entire shape of the system to operate. Pragmatism dictates listing agents in SKILL.md to ensure every agent has everything they need, but it introduces real risk that agents will behave incorrectly through context rot. That safety is actually a danger.

## On Long-Running Agents (2026-02-28)

Long-running agents may compact and lose sight of what it is that they are doing. They can end up only implementing part of the task. Strategists are at serious risk here. Strategists can snapshot their understanding and then another strategist can make a decision about that understanding. Think about files with an optimized roadmap showing what a strategist has done. The strategist can use that + other decisions to make a next decision. The CRITICAL point here is that strategists tend to not trust their own previous conclusions because they are a fresh run. They will want to run research before acting, re-deriving all solutions. So the next philosophy is to avoid long-running agents and instead focus on history and decisions.

## On Task Submission Over Agent Spawning (2026-02-28)

The UI agent is a long-running agent so it is imperative that it only kickstarts the process and is able to receive messages and send messages. Right now the UI agent is sending prompts across other agents. This is incredibly dangerous. There should be a script that can route to next agents when other agents finish and the UI agent can simply kick that off so that it can just listen to commands from the user while the script runs. So we likely need some kind of agent routing and state machine. Keep in mind that we can have dynamically created agents... so it is less agent routing and more upcoming tasks. Agents need to be able to submit tasks rather than run agents. The script needs to just run the agents. It can then "resume" agent sessions with that history. This prevents long running agents and allows agents to manage their own context for what they need so that they can stay on task.

## On Agent File Enforcement (2026-02-28)

Agents must always be executed with their agent file + their task (prompt). We can enforce this from the script side. We can't have these random rogue agents running. Every agent must be constrained to rules that we set forth. Agents are running rogue agents right now and causes system contamination. The primary philosophy is system safety. This means that dynamically created agents must be aligned to the system, which would be its own alignment process against philosophy to correct the agents. That means dynamically created agents will be very expensive. This likely will not scale. We may need to revisit dynamically created agents.

## On Dynamic Agent Templates (2026-02-28)

We can follow common templates for dynamically created agents. For the intent layer they specialize agents against proposals. They continue to update themselves as well. They always follow the same "skeleton" though. They look at similar things. We just fill in different details specifically around the proposal. The risk with this is what if a proposal doesn't fit our skeleton? Flexibility and strategy is a core tenet so we need to adopt this in a way that allows agents to be flexible while keeping things safe in a way that keeps the system more deterministic. The agents need some kind of structure/guide without needing a full alignment phase. They need some kind of template to work off of and expand from. A template is a much better starting place than nothing at all.

## On Our System vs The Target System (2026-02-28)

There is an important distinction between "our system" — the pipeline itself (section_loop, scan, substrate, agents, scripts) — and "the target system" — whatever codebase the pipeline is building or modifying. Our system is always Python + Bash + Markdown. We control it. We know the language. The target system could be anything — Python, C++, Rust, whatever.

The philosophy about no hardcoded language assumptions applies to the target system. Agents must reason about any codebase without assuming Python. But our own system IS Python. Using AST analysis, linting, or language-specific enforcement on our own code is just engineering — not a philosophy violation. A required parameter in dispatch_agent() is us enforcing our own API contract. An AST scanning our dispatch calls is us verifying our own codebase. These are fine.

Where this matters: agents strategizing about how to explore, understand, and modify code — that's about the target system. That needs flexibility, heuristics, no language assumptions. But agents operating within our pipeline, following our schemas, producing our signal formats — that's about our system. That can be enforced mechanically because we define the rules.

## On Testing Philosophy (2026-02-28)

We need minimal high-quality high-signal tests. More tests or tightly coupled mocked tests are not useful. We focus on testing the right behavior, not on test count.

Tests that check for the absence of a previous behavior are not legal. A regression guard that says "this pattern used to exist and now it shouldn't" is brittle — it's coupled to historical accidents, not to system correctness.

Integration tests with mocked agent responses are ok but our system really just has one entrypoint that goes everywhere. What we actually need are component tests on underlying systems — mail, agent execution, dispatch, coordination, signal reading. These are the building blocks that have clear contracts.

If we have very specific schemas or skeletons for agents and the like, those can be enforced through linting. Schema validation, required fields, structural contracts — that's legitimate enforcement on our own system.

Somehow we have 600-700 tests. That is a lot of tests for a system this size. Many are likely low-signal or testing the same thing from different angles.

## On E2E Tests With Live LLMs (2026-02-28)

The other valuable thing, now that we are removing long-running agents from the system, is E2E tests that use live LLMs. When we had long-running agents this type of test was impossible because the behavior spanned too much context and was too unpredictable. Now that agents are short-lived, each interaction is bounded and testable.

The problem is that behavior is difficult to judge. If the LLM can't figure out its own behavior, how can another LLM figure out whether that behavior was right? We can write set scenarios. Set situations where we expect a particular decision to be made with a particular model. These would be manual tests. We are testing individual interactions — one agent, one decision, one expected outcome. These are valuable because they verify that our agent files + prompts actually produce the behavior we designed.

## On Sections as Concerns (2026-02-28)

Sections are NOT file bundles. A section is a problem region — a concern that needs to be addressed. The related files list is a starting hypothesis from exploration, not the identity of the section. The section can discover new files, discard irrelevant ones, or need files from anywhere in the codebase. If you treat sections as file bundles, cross-section coordination becomes mechanical (file overlap detection). If you treat sections as concerns, coordination becomes strategic (problem interaction, contract negotiation, friction resolution).

## On Migration Consistency (2026-02-28)

When migrating from one execution model to another (e.g., direct dispatch to task submission), the migration must be atomic per surface. If an agent file encodes the new model but its runtime template still encodes the old model, the agent faces split instructions and behavior degrades unpredictably. This is worse than either model alone because an agent under a consistent old model at least behaves predictably. A split-brain agent cannot.

---

## On Risk and Strategy (2026-03-07)

There's a gap in the loop. The recursive structure — explore, recognize problems, propose, align, descend — handles understanding and solution generation well. But it says nothing about what happens when execution encounters risk. When an agent hits difficulty and abandons its methodology. When the system applies the same heavy process to a trivial rename that it applies to a cross-cutting architectural change. When cycles are wasted on guardrails that aren't proportional to what's actually dangerous.

The insight is that strategy emerges as a side effect of risk management, not from trying to "be strategic" directly. If you define an agent that assesses risk factors in plan execution, and another agent that proposes tool and workflow adjustments to mitigate those risks, and you continue that loop until risks are below threshold — with intermediate accepted steps and unknown horizons beyond those steps — then you get adaptive goal-planning. Strategy is the consequence of managing risk well, not a separate cognitive task.

This connects to the existing philosophy. We already have an ecosystem of known risks — context rot, silent drift, scope creep, brute-force regression, cross-section incoherence, tool island isolation, stale artifact contamination. And we already have tools that mitigate them — short-lived agents, alignment checks, agent file enforcement, execution-readiness gates, reconciliation. The risk-optimization loop makes this mapping explicit and dynamic rather than implicit and static. New risks can be identified. New tools can be proposed. The mapping between risks and mitigations becomes a first-class concern rather than something baked into the system architecture and hoped for.

## On Risk Quantification (2026-03-07)

The system currently cannot distinguish between a high-risk step that needs heavy guardrails and a low-risk step that can be fast. Without quantification, we either uniformly apply maximum caution — wasting cycles on things that don't need it — or we apply no caution at all and introduce defects. Neither is acceptable.

Strategies should scale to risk level. A trivial rename doesn't need the same process as a cross-cutting architectural change. Risk quantification makes this possible. It also makes something else possible: perceived risk vs surfaced risk. Initial risk assessment is based on what we know ahead of time. After each execution cycle, risk is reassessed based on what actually happened. Later steps may be optimized if earlier steps resolved unknowns. The system always works with what it immediately knows rather than assuming about unknowns.

This ties into multi-step acceptance. Early steps in a task package may be accepted — risk below threshold — while later steps are rejected because they're too uncertain. Accepted steps execute. Rejected steps wait for accepted steps to provide outputs that inform reassessment. This is how the system avoids planning the full solve graph upfront. It accepts what it can see clearly and leaves the rest open. That's not laziness. That's honest about what we actually know.

## On Brute-Force Regression (2026-03-07)

This is the specific failure mode that motivated the entire risk-optimization concept. When agents encounter difficulty, they abandon their methodology and brute-force toward task completion. They stop exploring. They stop recognizing problems. They stop proposing strategies. They just start hammering at the task with whatever they can think of, and the quality of the output degrades dramatically.

Agents don't know what they know. They cannot inventory their own understanding or identify gaps before acting. They jump straight to execution without assessing whether they have enough information, whether the step is risky, or whether their tools are appropriate for the situation. Standard planning is reactive — agents solve problems as they encounter them. They don't orient around intermediary goals. They don't assess what they need to know before they start working. The system solves problems but doesn't plan to plan.

The system needs to detect when an agent is regressing to brute-force and intervene. Not by punishing the agent, but by adding guardrails, decomposing the step further, gathering more information first, or selecting different tools. The risk-optimization loop is the mechanism for this. If risk assessment detects that a step is dangerous, the tool agent can propose mitigations before the executing agent ever encounters the difficulty that would trigger regression.

## On Optimization Feedback (2026-03-07)

There are two failure directions. The system can waste cycles on unnecessary guardrails — being cautious about things that don't need caution. Or it can operate without sufficient guardrails — being fast about things that actually needed more protection. Both directions need feedback.

Without optimization feedback, the system has no way to know whether its guardrails are proportional to the actual risk. It cannot identify where effort is being spent that doesn't mitigate any real risk. It cannot identify where the absence of effort is introducing defects. Both over-caution and under-caution are invisible until their consequences manifest — either as wasted cycles or as quality failures.

This is what closes the loop. Risk assessment identifies dangers. Tool adjustment proposes mitigations. Optimization feedback evaluates whether those mitigations were proportional. Was the cost justified? Did the guardrail actually prevent anything? Did the lack of guardrail actually cause a problem? This feedback informs the next iteration's risk assessment, making it more accurate over time.

## On Adaptive Execution (2026-03-07)

Adaptive systems can swing between over-cautious and over-risky. Adding guardrails that waste cycles without proportional quality gain. Then removing guardrails that were actually needed, causing failures that re-trigger heavy guardrails. This oscillation is the natural failure mode of any system that adapts without convergence criteria.

Changes should be incremental, not dramatic. There should be convergence criteria: stop adapting when risk is below threshold AND optimization is not yielding significant savings. The system should have memory of what worked and what didn't — a risk history that informs future assessment. Risk levels should be quantified, not just categorical. The goal is not zero risk. The goal is risk below a defined threshold with effort proportional to the actual danger.

The key property of this loop is that it operates alongside the existing problem-proposal loop, not instead of it. The problem-proposal loop handles understanding and solutions. The risk-optimization loop handles execution safety and efficiency. Together they produce work that is both correct and proportionally guarded — without either wasting cycles on unnecessary process or introducing defects through insufficient process.
