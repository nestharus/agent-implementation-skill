# Schedule: {{task-name}}
# Source: {{proposal-path}}

[wait] 1. decompose | claude-opus -- recursive section decomposition (implement.md Stage 1)
[wait] 2. docstrings | glm -- ensure all source files have module docstrings (implement.md Stage 2)
[wait] 3. scan | glm -- file relevance scan: docstrings vs sections (implement.md Stage 3)
[wait] 4. section-loop | claude-opus,gpt-5.3-codex-high -- per-section: solution + plan/implement + alignment check (implement.md Stages 4-5)
[wait] 5. verify | gpt-5.3-codex-high2 -- constraint audit + lint + tests (implement.md Stage 6)
[wait] 6. post-verify | glm -- full suite + import check + commit (implement.md Stage 7)
[wait] 7. promote | claude-opus -- review constraints/tradeoffs for project level
