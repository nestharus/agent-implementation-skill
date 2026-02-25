# Task: Implementation Alignment Check — Section {section_number}

## Summary
{summary}

## Files to Read
1. Section alignment excerpt: `{alignment_excerpt}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Integration proposal: `{integration_proposal}`
4. Section specification: `{section_path}`
5. Implemented files (read each one):
{files_block}{surface_line}{codemap_line}{impl_corrections_line}{micro_line}{todo_line}{todo_resolution_line}{intent_problem_ref}{intent_rubric_ref}{intent_philosophy_ref}

## Worktree root
`{codespace}`

## Instructions

Read the alignment excerpt and proposal excerpt first — these define the
PROBLEM and CONSTRAINTS. Then read the integration proposal to understand
WHAT was planned. If codemap corrections exist, treat them as authoritative
over codemap.md. If a microstrategy exists, it provides the tactical
per-file breakdown. Finally read the implemented files.

Check SHAPE AND DIRECTION:
- Is the implementation still solving the RIGHT PROBLEM?
- Does the code match the intent of the integration proposal?
- Has anything drifted from the original problem definition?
- Are the changes internally consistent across files?
- If TODO extractions exist, were they resolved appropriately?
  (implemented, rewritten with justification, or explicitly deferred)

**Go beyond the file list.** The section spec may require creating new
files or producing artifacts at specific paths. Check the worktree for
any file the section mentions that should exist.

Do NOT check:
- Code style or formatting preferences
- Whether variable names are perfect
- Minor documentation wording
- Edge cases that weren't in the alignment constraints

Reply with EXACTLY one of:

ALIGNED

or

PROBLEMS:
- <specific problem 1: what's wrong, why it matters, what should change>
- <specific problem 2: what's wrong, why it matters, what should change>
...

or

UNDERSPECIFIED: <what information is missing and why alignment can't be checked>

Each problem must be specific and actionable.

Your first line must be the verdict label above. You MUST also include the
structured JSON verdict block described by the alignment-judge method.
