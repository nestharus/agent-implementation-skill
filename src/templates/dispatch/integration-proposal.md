# Task: Integration Proposal for Section {section_number}

## Summary
{summary}

## Files to Read
1. Section proposal excerpt: `{proposal_excerpt}`
2. Section alignment excerpt: `{alignment_excerpt}`
3. Section specification: `{section_path}`
4. Related source files (prioritize; read selectively if long):
{files_block}{problem_frame_ref}{strategic_state_ref}{codemap_ref}{corrections_ref}{substrate_ref}{tools_ref}{todos_ref}{intent_problem_ref}{intent_rubric_ref}{intent_philosophy_ref}{intent_registry_ref}{governance_ref}{research_ref}
{existing_note}{problems_block}{notes_block}{decisions_block}{additional_inputs_block}
## Instructions

A section is a **problem region / concern**, not a file bundle. Related
files are a starting hypothesis. You are expected to explore and may
discover additional relevant files or identify irrelevant ones.

If an intent problem definition or rubric is listed above, treat it as the
canonical problem definition and alignment rubric for this section. Anchor
your proposal to it.

Treat TODO extraction (if listed in "Files to Read" above) as the
canonical in-scope microstrategy surface. If your proposal conflicts with
TODOs, reconcile explicitly (update plan or propose TODO updates).

You are writing an INTEGRATION PROPOSAL — a **problem-state diagnostic**
describing the current integration landscape for this section. The
proposal excerpt says WHAT to build. Your job is to explore the codebase,
determine what integration surfaces are resolved vs. unresolved, and
record your findings in both a human-readable markdown document and a
machine-readable `proposal-state.json` sidecar.

You are NOT deciding which files to create or where new modules belong.
You are diagnosing the integration problem and recording what you find.

### Accuracy First — Zero Tolerance for Fabrication

You have zero tolerance for fabricated understanding or bypassed safeguards;
operational risk is managed proportionally by ROAL. You MUST explore the
codebase before writing any proposal. A proposal written without reading
existing code is a guess — guesses introduce risk. Never skip exploration,
never produce a shallow proposal, never simplify to save tokens. "This is
simple enough to skip exploration" is never valid reasoning.

### Phase 1: Explore and Understand

Before writing anything, explore the codebase strategically. You MUST
understand the existing code before proposing how to integrate.

**Start with the codemap** if available — it captures the project's
structure, key files, and how parts relate. If codemap corrections exist,
treat them as authoritative fixes (wrong paths, missing entries,
misclassified files). Use it to orient yourself before diving into
individual files.

Your goal in exploration is to **populate the problem-state fields**:

- For each integration surface the section needs, determine whether an
  anchor exists in the current code (resolved) or not (unresolved).
- For each interface the section will cross, determine whether the
  contract is known and verified (resolved) or unknown/ambiguous
  (unresolved).
- Record questions that arise — distinguish between questions you can
  answer with more exploration (research_questions) and questions only
  the user can answer (user_root_questions).
- Note any cross-section coordination needs (shared_seam_candidates)
  and any problem regions that may need their own section
  (new_section_candidates).

**Do NOT invent architecture for unresolved items.** If an anchor is
unresolved, record it as unresolved. Do not fabricate file paths, module
structures, or scaffolding to make it look resolved. The downstream
pipeline handles unresolved items through re-exploration or escalation.

**Commission follow-up work when needed:**

You have direct codebase access for exploration during your current
session. Task requests commission additional work that runs AFTER you
complete — use them for follow-up analysis, verification, or targeted
sub-tasks that should inform the next strategic iteration.

To submit a task request, write to `{task_submission_path}`:

Legacy single-task format (still accepted):
```json
{{
    "task_type": "scan.explore",
    "concern_scope": "section-{section_number}",
    "payload_path": "<path-to-exploration-prompt>",
    "priority": "normal"
}}
```

Chain format (v2) — declare sequential follow-up steps:
```json
{{
    "version": 2,
    "actions": [
        {{
            "kind": "chain",
            "steps": [
                {{"task_type": "scan.explore", "concern_scope": "section-{section_number}", "payload_path": "<path-to-explore-prompt>"}},
                {{"task_type": "proposal.integration", "concern_scope": "section-{section_number}", "payload_path": "<path-to-proposal-prompt>"}}
            ]
        }}
    ]
}}
```

If dispatched as part of a flow chain, your prompt will include a
`<flow-context>` block pointing to flow context and continuation paths.
Read the flow context to understand what previous steps produced. Write
follow-up declarations to the continuation path.

Available task types for this role: {allowed_tasks}

The dispatcher handles agent selection and model choice. You declare
WHAT analysis you need, not which agent or model runs it.

Use task requests for follow-up work like:
- Deeper file analysis beyond your current exploration
- Verification of your proposal's assumptions
- Investigation of callers/callees in distant modules
- Cross-section dependency checks

For your current exploration, read files directly. Explore strategically:
form a hypothesis, verify it with a targeted read, adjust, repeat.

### Phase 2: Write the Problem-State Proposal

After exploring, write your proposal as a **problem-state diagnostic**
covering:

1. **Exploration summary** — What did you examine? What did you learn
   about the current state of the code relevant to this section?
2. **Resolved anchors** — For each integration point where you found
   concrete existing code, describe what exists and how the section
   connects to it. Cite specific files and functions you verified.
3. **Unresolved anchors** — For each integration point where no existing
   code was found or the connection is ambiguous, describe what is
   needed and why it is unresolved. Do NOT propose what to create —
   state what is missing.
4. **Contract status** — Which interface contracts (function signatures,
   data shapes, protocols) are confirmed vs. unknown? For resolved
   contracts, cite where you verified them.
5. **Open questions** — Research questions (answerable with more
   exploration) and user root questions (only the user can answer).
6. **Cross-section concerns** — Shared seams that need coordination with
   other sections, and any new section candidates discovered.
7. **Readiness assessment** — Is the section ready for implementation?
   Why or why not? Be honest. If blocking fields are non-empty,
   `execution_ready` MUST be `false`.

Write your human-readable integration proposal to: `{integration_proposal}`

### Machine Artifact: Proposal State

Write a structured `proposal-state.json` sidecar to:
`{proposal_state_path}`

This file MUST conform to the canonical proposal-state schema:

```json
{{
    "resolved_anchors": ["<anchor description with file:function citations>"],
    "unresolved_anchors": ["<what is needed and why it is unresolved>"],
    "resolved_contracts": ["<confirmed interface contract with citation>"],
    "unresolved_contracts": ["<needed but undefined/unverified contract>"],
    "research_questions": ["<question answerable with more exploration>"],
    "blocking_research_questions": ["<research question that determines structural direction — blocks implementation>"],
    "user_root_questions": ["<question only the user can answer>"],
    "new_section_candidates": ["<problem region that may need its own section>"],
    "shared_seam_candidates": ["<integration surface shared with other sections>"],
    "execution_ready": false,
    "readiness_rationale": "<honest explanation of readiness status>",
    "problem_ids": ["<PRB-XXXX IDs from governance packet that this proposal addresses>"],
    "pattern_ids": ["<PAT-XXXX IDs from governance packet whose patterns this proposal follows>"],
    "profile_id": "<governing philosophy profile, e.g. PHI-global>",
    "pattern_deviations": ["<any established patterns deviated from, with rationale>"],
    "governance_questions": ["<unresolved governance questions discovered during proposal>"]
}}
```

**Blocking fields** (any non-empty list forces `execution_ready` to `false`):
`unresolved_anchors`, `unresolved_contracts`, `blocking_research_questions`,
`user_root_questions`, `shared_seam_candidates`.

**`execution_ready` is fail-closed.** When in doubt, set it to `false`.
A premature `true` causes downstream implementation failures. An honest
`false` causes a re-exploration cycle, which is the correct outcome.

Every item in the JSON must correspond to something discussed in the
markdown proposal. The JSON is the machine-readable truth; the markdown
is the human-readable explanation.

{signal_block}
{mail_block}
