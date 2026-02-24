# Task: Strategic Implementation for Section {section_number}

## Summary
{summary}

## Files to Read
1. Integration proposal (ALIGNED): `{integration_proposal}`
2. Section proposal excerpt: `{proposal_excerpt}`
3. Section alignment excerpt: `{alignment_excerpt}`
4. Section specification: `{section_path}`
5. Related source files:
{files_block}{problem_frame_ref}{micro_ref}{codemap_ref}{impl_corrections_ref}{todos_ref}{impl_tools_ref}
{problems_block}{decisions_block}{tooling_block}{additional_inputs_block}
## Instructions

A section is a **problem region / concern**, not a file bundle. Related
files are a starting hypothesis. You may discover additional relevant files
or determine that some listed files are not actually needed.

You are implementing the changes described in the integration proposal.
The proposal has been alignment-checked and approved. Your job is to
execute it strategically.

### How to Work

**Think strategically, not mechanically.** Read the integration proposal
and understand the SHAPE of the changes. Then tackle them holistically —
multiple files at once, coordinated changes. Use the codemap if available
to understand how your changes fit into the broader project structure. If
codemap corrections exist, treat them as authoritative fixes.

**Dispatch sub-agents for exploration and targeted work:**

For cheap exploration (reading, checking, verifying):
```bash
uv run --frozen agents --model {exploration_model} --project "{codespace}" "<instructions>"
```

For targeted implementation of specific areas, write a prompt file first
(Codex models require `--file`, not inline instructions):
```bash
PROMPT="$(mktemp)"
cat > "$PROMPT" <<'EOF'
<instructions>
EOF
uv run --frozen agents --model {delegated_impl_model} \
  --project "{codespace}" --file "$PROMPT"
```

Use sub-agents when:
- You need to read several files to understand context before changing them
- A specific area of the implementation is self-contained and can be delegated
- You want to verify your changes didn't break something

Do NOT use sub-agents for everything — handle straightforward changes
yourself directly.

### Implementation Guidelines

1. Follow the integration proposal's strategy
2. Make coordinated changes across files — don't treat each file in isolation
3. If you discover the proposal missed something (a file that needs changing,
   an interface that doesn't work as expected), handle it — you have authority
   to go beyond the proposal where necessary
4. Update docstrings and comments to reflect changes
5. Ensure imports and references are consistent across modified files

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
Include files modified by sub-agents. Include ALL files — both directly
modified and indirectly affected.
{signal_block}
{mail_block}
