# Task: Strategic Implementation for Section {section_number}

## Summary
{summary}

## Files to Read
1. Integration proposal (ALIGNED): `{integration_proposal}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Section alignment excerpt: `{alignment_excerpt}`
4. Section specification: `{section_path}`
5. Related source files:
{files_block}{problem_frame_ref}{micro_ref}{codemap_ref}{impl_corrections_ref}{substrate_ref}{todos_ref}{impl_tools_ref}{intent_problem_ref}{intent_rubric_ref}{intent_philosophy_ref}{intent_registry_ref}{proposal_state_ref}{reconciliation_ref}{readiness_ref}
{problems_block}{decisions_block}{tooling_block}{risk_inputs_block}{additional_inputs_block}
## Instructions

A section is a **problem region / concern**, not a file bundle. Related
files are a starting hypothesis. You may discover additional relevant files
or determine that some listed files are not actually needed.

If an intent problem definition or rubric is listed above, treat it as the
canonical problem definition and alignment rubric for this section. Anchor
your implementation to it.

You are implementing the changes described in the integration proposal.
The proposal has been alignment-checked and approved. Your job is to
execute it strategically.

### Accuracy First — Zero Risk Tolerance

Every shortcut introduces risk. You have zero tolerance for fabricated
understanding or bypassed safety gates; operational risk is managed
proportionally by ROAL. Follow the full process faithfully: explore
before changing, follow the proposal exactly, verify after changing.
Shortcuts are permitted ONLY when the remaining work is so trivially
small that no meaningful risk exists. "This is simple enough to do
directly" is not valid reasoning for skipping exploration or
verification.

### Risk Boundary

If a "Risk Inputs (from ROAL)" section appears above, it defines your
execution scope:

- **Accepted frontier**: these steps are the hard local authority. Execute
  only what they authorize.
- **Deferred steps**: these are NOT in your scope. Do not attempt them.
- **Reopened steps**: these are NOT locally solvable. Do not attempt them.
- **`dispatch_shape`**: this is follow-on topology metadata. It tells you
  the expected shape (chain/fanout/gate) but does NOT grant permission to
  widen your scope.

If no risk inputs are present, proceed normally per the integration
proposal.

### How to Work

**Think strategically, not mechanically.** Read the integration proposal
and understand the SHAPE of the changes. Then tackle them holistically —
multiple files at once, coordinated changes. Use the codemap if available
to understand how your changes fit into the broader project structure. If
codemap corrections exist, treat them as authoritative fixes.

**Commission follow-up work when needed:**

You have direct codebase access for exploration and implementation during
your current session. Task requests commission additional work that runs
AFTER you complete — use them for follow-up analysis, verification, or
self-contained sub-tasks that should run as separate dispatches.

To submit a task request, write to `{task_submission_path}`:

Legacy single-task format (still accepted):
```json
{{
    "task_type": "scan_explore",
    "concern_scope": "section-{section_number}",
    "payload_path": "<path-to-prompt-file>",
    "priority": "normal"
}}
```

For targeted implementation of a self-contained area:
```json
{{
    "task_type": "strategic_implementation",
    "concern_scope": "section-{section_number}",
    "payload_path": "<path-to-implementation-prompt>",
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
                {{"task_type": "strategic_implementation", "concern_scope": "section-{section_number}", "payload_path": "<path-to-impl-prompt>"}},
                {{"task_type": "alignment_check", "concern_scope": "section-{section_number}", "payload_path": "<path-to-verify-prompt>"}}
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
WHAT work you need, not which agent or model runs it.

Submit task requests for follow-up work like:
- Self-contained sub-tasks that can be delegated to a separate dispatch
- Post-implementation verification
- Deeper analysis of distant modules

Handle straightforward changes yourself directly — do NOT submit task
requests for work you can do in your current session.

### Implementation Guidelines

1. Follow the integration proposal's strategy
2. Make coordinated changes across files — don't treat each file in isolation
3. If you discover the proposal missed something structural — an unresolved
   anchor, an undefined contract, a section-boundary gap, or an architectural
   decision that was never made — you MUST emit a blocker signal
   (UNDERSPECIFIED or DEPENDENCY). Do NOT invent missing structure yourself.
4. Local mechanical necessities are still your responsibility: imports, obvious
   glue code, comment/doc updates, minor formatting. These do not require a
   blocker signal.
5. Update docstrings and comments to reflect changes
6. Ensure imports and references are consistent across modified files

### Structural Omission Handling

Your prompt includes references to the proposal-state artifact,
reconciliation result, and execution-readiness artifact (when they exist).
These tell you what the proposal resolved, what cross-section conflicts
were detected, and what blockers remain.

**If you encounter a structural gap** (missing anchor, undefined contract,
unclear section boundary, missing architectural decision):
- Do NOT absorb it by inventing structure. That reinforces collapse.
- Emit a blocker signal with state UNDERSPECIFIED or DEPENDENCY.
- Include `why_blocked` explaining what the proposal omitted.
- Continue implementing unblocked parts of the proposal.

**Mechanical necessities** (imports, glue code, docstrings, formatting)
are still your job — they have exactly one correct answer given the
surrounding code and do not require a blocker signal.

### TODO Handling

If a TODO extraction file is listed in "Files to Read" above, treat it
as the canonical in-scope TODO surface for this section.

If the section has in-code TODO blocks (microstrategies), you must either:
- **Implement** the TODO as specified
- **Rewrite/remove** the TODO with justification (if the approach changed)
- **Defer** with a clear reason pointing to which section/phase handles it

After handling TODOs, write a resolution summary to:
`{artifacts}/signals/section-{section_number}-todo-resolution.json`

```json
{{"todos": [{{"location": "file:line", "action": "implemented|rewritten|deferred", "reason": "..."}}]}}
```

### Report Modified Files

After implementation, write a list of ALL files you modified to:
`{modified_report}`

One file path per line (relative to codespace root `{codespace}`).
Include all files modified during this implementation — both directly
modified and indirectly affected.
{signal_block}
{mail_block}
