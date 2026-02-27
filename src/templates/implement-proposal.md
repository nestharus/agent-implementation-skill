# Schedule: {{task-name}}
# Source: {{proposal-path}}

[wait] 1. decompose | claude-opus -- recursive section decomposition (implement.md Stage 1)
[wait] 2. docstrings | glm -- ensure all source files have module docstrings (implement.md Stage 2)
[wait] 3. scan | claude-opus,glm -- agent-driven codemap exploration + per-section file identification + deep scan on confirmed matches (implement.md Stage 3)
[wait] 3.5. substrate | gpt-codex-high,gpt-codex-xhigh -- shared integration substrate discovery: per-section shards → cross-section pruning → minimal anchor seeding (implement.md Stage 3.5; runs when greenfield or vacuum sections detected)
[wait] 4. section-loop | claude-opus,gpt-codex-high,glm -- per-section: integration proposals + strategic implementation + alignment checks + cross-section communication + global coordination (implement.md Stages 4-5)
[wait] 5. verify | gpt-codex-high -- constraint alignment check + lint + tests (implement.md Stage 6; use the policy's verification model if different)
[wait] 6. post-verify | glm -- full suite + import check + commit (implement.md Stage 7)
[wait] 7. promote | claude-opus -- review constraints/tradeoffs for project level
