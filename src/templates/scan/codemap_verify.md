# Task: Verify Codemap Routing Claims

## Codespace Root
`{codespace}`

## Files to Read
1. Codemap: `{codemap_path}`

## Instructions
Sample 3-5 files mentioned in the codemap's Routing Table. Resolve relative
paths against the codespace root above. For each:
1. Read the file
2. Verify the codemap's description matches reality
3. Note any discrepancies

Write a brief verification report. If any routing claims are wrong,
write corrections to `{corrections_signal}`:
```json
{{"corrections": [{{"file": "path", "claimed": "...", "actual": "..."}}], "verified": true}}
```

If everything checks out, write: `{{"corrections": [], "verified": true}}`
