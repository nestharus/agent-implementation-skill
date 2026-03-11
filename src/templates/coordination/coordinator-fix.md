# Task: Coordinated Fix for Problem Group {group_id}

## Problems to Fix

{problems_text}

## Affected Files
{file_list}

## Section Context
{section_specs}
{alignment_specs}
{codemap_block}{tools_block}
## Instructions

Fix ALL the problems listed above in a COORDINATED way. These problems
are related — they share files and/or have a common root cause. Fixing
them together avoids the cascade where fixing one problem in isolation
creates or re-triggers another.

### Strategy

1. **Explore first.** Before making changes, understand the full picture.
   Read the codemap if available to understand how these files fit into
   the broader project structure. If you need deeper exploration, submit
   a task request to `{task_submission_path}`:
   ```json
   {{"task_type": "scan.explore", "concern_scope": "coord-group-{group_id}", "payload_path": "<path-to-exploration-prompt>", "priority": "normal"}}
   ```

2. **Plan holistically.** Consider how all the problems interact. A single
   coordinated change may fix multiple problems at once.

3. **Implement.** Make the changes. For targeted sub-tasks, submit a
   task request:
   ```json
   {{"task_type": "coordination.fix", "concern_scope": "coord-group-{group_id}", "payload_path": "<path-to-fix-prompt>", "priority": "normal"}}
   ```

4. **Verify.** After implementation, submit a scan task to verify
   the fixes address all listed problems without introducing new issues.

Available task types: scan.explore, coordination.fix

The examples above use the legacy single-task format. You may also use
the v2 envelope format with chain or fanout actions — see your agent
file for the full v2 format reference.

If dispatched as part of a flow chain, your prompt will include a
`<flow-context>` block pointing to flow context and continuation paths.
Read the flow context to understand what previous steps produced. Write
follow-up declarations to the continuation path.

{task_submission_semantics}

### Report Modified Files

After implementation, write a list of ALL files you modified to:
`{modified_report}`

One file path per line (relative to codespace root `{codespace}`).
Include all files modified during this implementation.
