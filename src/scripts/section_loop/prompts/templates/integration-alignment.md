# Task: Integration Proposal Alignment Check — Section {section_number}

## Summary
{summary}

## Files to Read
1. Section alignment excerpt: `{alignment_excerpt}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Section specification: `{section_path}`
4. Integration proposal to review: `{integration_proposal}`{surface_line}{codemap_line}{corrections_line}

## Instructions

Read the alignment excerpt and proposal excerpt first — these define the
PROBLEM and CONSTRAINTS. Then read the integration proposal. If codemap
corrections exist, treat them as authoritative over codemap.md.

Check SHAPE AND DIRECTION only:
- Is the integration proposal still solving the RIGHT PROBLEM?
- Has the intent drifted from what the proposal/alignment describe?
- Does the integration strategy make sense given the actual codebase?
- Are there any fundamental misunderstandings about what's needed?

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
