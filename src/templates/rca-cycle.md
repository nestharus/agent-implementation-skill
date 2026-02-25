# Schedule: {{task-name}}
# Source: {{failing-tests}}

[wait] 1. baseline | glm -- capture current test failures (rca.md Setup)
[wait] 2. investigate | gpt-5.3-codex-high -- RCA report on failures (rca.md Step 1)
[wait] 3. plan-fix | claude-opus -- plan correct fix from RCA report (rca.md Step 2)
[wait] 4. apply-fix | claude-opus -- apply fix to working branch (rca.md Step 2)
[wait] 5. verify | glm -- rerun tests, check for regressions (rca.md Step 3)
[wait] 6. cleanup | claude-opus -- remove worktrees, commit (rca.md Cleanup)
