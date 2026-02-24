
## Signals (if you encounter problems)

If you cannot complete the task, write a structured JSON signal file.
This is the primary and mandatory channel for signaling blockers.

**Signal file**: Write to `{signal_path}`
Format:
```json
{{
  "state": "<STATE>",
  "detail": "<brief explanation of the blocker>",
  "needs": "<what specific information or action is needed to unblock>",
  "why_blocked": "<REQUIRED for UNDERSPECIFIED/DEPENDENCY: concrete reason progress is impossible without external input>",
  "assumptions_refused": "<what assumptions you chose NOT to make and why>",
  "suggested_escalation_target": "<who should handle this: parent, user, or specific section>"
}}
```
States: UNDERSPECIFIED, NEED_DECISION, DEPENDENCY, OUT_OF_SCOPE, NEEDS_PARENT

**Required fields by state:**
- ALL states: `state`, `detail`, `needs`
- UNDERSPECIFIED: also requires `why_blocked` — explain what information is missing and why you cannot infer it
- DEPENDENCY: also requires `why_blocked` — explain which section/artifact is needed and why work cannot proceed without it
- NEED_DECISION: `why_blocked` is optional but recommended
- OUT_OF_SCOPE: also requires `why_blocked` — explain what work is out of scope and why it cannot be absorbed into this section. Optionally include `scope_delta` describing what new section should exist.
- NEEDS_PARENT: also requires `why_blocked` — explain what parent-level decision or reframing is needed and why this section cannot proceed without it

**Human-readable verdict line (non-authoritative)**: Also output EXACTLY ONE of these on its own line for human readability:
UNDERSPECIFIED: <what information is missing and why you can't proceed>
NEED_DECISION: <what tradeoff or constraint question needs a human answer>
DEPENDENCY: <which other section must be implemented first and why>
OUT_OF_SCOPE: <what work is outside this section's scope and what new section should handle it>
NEEDS_PARENT: <what parent-level decision is required and why this section is blocked>

The orchestrator does **not** parse this line for control flow; it is for humans only. The JSON signal file above is required and is the only channel the orchestrator reads.

Only use these if you truly cannot proceed. Do NOT silently invent
constraints or make assumptions — signal upward and let the parent decide.
