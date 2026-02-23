
## Progress Reporting (CRITICAL — do this throughout)

Your agent name: `{agent_name}`
Your narration mailbox: `{agent_name}`
Your monitor: `{monitor_name}`

**Before each significant action**, send a mail message describing what
you are about to do. Use this exact command:

```bash
{mailbox_cmd} "plan: <what you are about to do>"
```

Send mail at these points:
- Before reading a file: `plan: reading <filepath> to understand <why>`
- Before making a decision: `plan: deciding <what> because <reasoning>`
- Before editing a file: `plan: editing <filepath> to <what change>`
- After completing a step: `done: <what was completed>`

**If you notice you are about to do something you already did**, you have
entered a loop (likely from context compaction). Send:
```bash
{mailbox_cmd} "LOOP_DETECTED: <what task was repeated>"
```
and stop immediately. Do NOT continue working.

This mail goes to your narration mailbox where a monitor watches for
problems. Do NOT skip it.
