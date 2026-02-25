# Task: Validate Related Files List

## Files to Read
1. Section specification: `{section_file}`
2. Codemap: `{codemap_path}`
{corrections_ref}

## Instructions
This section already has a `## Related Files` list. Check whether it is
still accurate given the current codemap and section problem statement.
If codemap corrections exist, treat them as authoritative over codemap.md.

Propose a structured signal at `{update_signal}`:
```json
{{"status": "current|stale", "additions": ["path/to/add"], "removals": ["path/to/remove"], "reason": "..."}}
```

If the list is current, write `{{"status": "current"}}`.
If changes are needed, include additions and/or removals with reasons.
