# Task: Extract Section {section_number} Excerpts

## Summary
{summary}
{decisions_block}
## Files to Read
1. Section specification: `{section_path}`
2. Global proposal: `{global_proposal}`
3. Global alignment: `{global_alignment}`

## Instructions

Read the section specification first to understand what section {section_number}
covers. Then read both global documents.

### Output 1: Proposal Excerpt
From the global proposal, extract the parts relevant to this section.
Copy/paste the relevant content WITH enough surrounding context to be
self-contained. Do NOT rewrite or interpret — use the original text.
Include any context paragraphs needed for the excerpt to make sense
on its own.

Write to: `{proposal_excerpt}`

### Output 2: Alignment Excerpt
From the global alignment, extract the parts relevant to this section.
Same rules: copy/paste with context, do NOT rewrite. Include alignment
criteria, constraints, examples, and anti-patterns that apply to this
section's problem space.

Write to: `{alignment_excerpt}`

### Output 3: Problem Frame (MANDATORY)
Write a problem frame for this section — a pre-exploration brief
that captures understanding BEFORE any integration work begins.

**The pipeline validates this artifact exists and is non-empty.**
Use whatever structure best captures the problem. The recommended
headings below are a starting point, not a rigid template:

- **Problem Statement**: What problem is this section solving? (1-2 sentences,
  must be specific and falsifiable — not "improve X" but "X currently does Y,
  it needs to do Z because of constraint C")
- **Evidence**: What evidence from the proposal/alignment supports this
  being the right problem to solve?
- **Constraints**: What constraints from the global alignment apply to
  this section specifically?
- **Success Criteria**: How will we know this section is done correctly?
- **Out of Scope**: What does this section explicitly NOT cover?

Adapt, merge, or rename sections as the problem demands. The goal
is a clear, grounded brief — not heading compliance.

Write to: `{problem_frame_path}`

### Important
- Excerpts are copy/paste, not summaries. Use the original text.
- Include enough surrounding context that each file stands alone.
- If the global document covers this section across multiple places,
  include all relevant parts.
- Preserve section headings and structure from the originals.
- The problem frame IS a summary — keep it brief and focused.
- The problem frame is MANDATORY — do not skip it.
{signal_block}
{mail_block}
