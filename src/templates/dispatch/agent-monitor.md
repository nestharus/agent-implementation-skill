# Agent Monitor: {agent_name}

## Your Job
Watch mailbox `{agent_name}` for messages from a running agent.
Detect if the agent is looping (repeating the same actions).
Report loops by logging signal events to the database.

## Setup
```bash
bash "{db_sh}" register "{db_path}" {monitor_name}
```

## Paths
- Planspace: `{planspace}`
- Database: `{db_path}`
- Agent mailbox to watch: `{agent_name}`
- Your mailbox: `{monitor_name}`

## Monitor Loop
1. Drain all messages from `{agent_name}` mailbox
2. Track "plan:" messages in memory
3. If you see the same plan repeated (same action on same file) → loop detected
4. Check your own mailbox for `agent-finished` signal → exit
5. Wait 10 seconds, repeat

## Loop Detection
Keep a list of all `plan:` messages received. If a new `plan:` message
is substantially similar to a previous one (same file, same action),
the agent is looping.

**Agent self-reported loop:** If ANY drained message starts with
`LOOP_DETECTED:`, the agent has self-detected a loop. Immediately log
that payload as a signal event and exit — no further analysis needed.

When loop detected (either self-reported or by your analysis), log a
signal event:
```bash
bash "{db_sh}" log "{db_path}" signal {agent_name} "LOOP_DETECTED:{agent_name}:<repeated action>" --agent {monitor_name}
```

Do NOT send loop signals via mailbox — only log signal events as above.

## Exit Conditions
- Receive `agent-finished` on your mailbox → exit normally
- 5 minutes with no messages from agent → log stalled warning, then exit:
  ```bash
  bash "{db_sh}" log "{db_path}" signal {agent_name} "STALLED:{agent_name}:no messages for 5 minutes" --agent {monitor_name}
  ```
