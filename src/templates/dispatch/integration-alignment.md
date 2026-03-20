# Task: Integration Proposal Alignment Check — Section {section_number}

## Summary
{summary}

## Files to Read
1. Section alignment excerpt: `{alignment_excerpt}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Section specification: `{section_path}`
4. Integration proposal to review: `{integration_proposal}`{proposal_state_line}{scope_grant_line}{surface_line}{codemap_line}{corrections_line}{intent_problem_ref}{intent_rubric_ref}{intent_philosophy_ref}{intent_registry_ref}
{intent_surfaces_block}
## Instructions

Read the alignment excerpt and proposal excerpt first — these define the
PROBLEM and CONSTRAINTS. Then read the integration proposal. If codemap
corrections exist, treat them as authoritative over codemap.md.

Check SHAPE AND DIRECTION only:
- Is the integration proposal still solving the RIGHT PROBLEM?
- If this section has a scope_grant, does the proposal serve the
  parent's delegated scope in addition to its own problem frame?
- Has the intent drifted from what the proposal/alignment describe?
- Does the integration strategy make sense given the actual codebase?
- Are there any fundamental misunderstandings about what's needed?
- If a proposal-state artifact exists, is it coherent with the proposal?
- Is `execution_ready` truthful? If blocking fields (unresolved_anchors,
  unresolved_contracts, user_root_questions, shared_seam_candidates)
  contain items, `execution_ready` MUST be `false`. A proposal with
  `execution_ready: true` and non-empty blocking fields cannot be ALIGNED.

Root sections do not have a scope_grant and skip the delegated-scope
check. For child sections, vertical alignment is additional: the
proposal must still solve the local section problems AND stay within
the parent's delegated scope. If it serves the local problems but
violates the scope_grant, report that as a vertical misalignment.

Do NOT check:
- Tiny implementation details (those get resolved during implementation)
- Exact code patterns or style choices
- Whether every edge case is covered
- Completeness of the strategy (some details are fetched on demand later)

Reply with EXACTLY one of:

ALIGNED

or

PROBLEMS:
- <specific problem 1: what's wrong and why it matters>
- <specific problem 2: what's wrong and why it matters>
...

or

UNDERSPECIFIED: <what information is missing and why alignment can't be checked>

Each problem must be specific and actionable. "Needs more detail" is NOT
a valid problem. "The proposal routes X through Y, but the alignment says
X must go through Z because of constraint C" IS a valid problem.

Your first line must be the verdict label above. You MUST also include the
structured JSON verdict block described by the alignment-judge method.
