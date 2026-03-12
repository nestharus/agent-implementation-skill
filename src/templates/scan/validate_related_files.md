# Task: Validate Related Files List

## Files to Read
1. Section specification: `{section_file}`
2. Codemap: `{codemap_path}`
{corrections_ref}

{missing_existing_section}

## Instructions
This section already has a `## Related Files` list. Check whether it is
still accurate given the current codemap and section problem statement.
If codemap corrections exist, treat them as authoritative over codemap.md.

You may inspect targeted repository files to verify currently listed
entries and confirm obvious replacement candidates. Do NOT do broad
open-ended exploration unrelated to this section.

A currently listed path that does not exist is positive evidence that the
list is stale.

You MUST write exactly one structured signal to `{update_signal}`:
```json
{{"status": "current|stale", "additions": ["path/to/add"], "removals": ["path/to/remove"], "reason": "..."}}
```

If the list is current, write:
```json
{{"status": "current", "additions": [], "removals": [], "reason": "..." }}
```

If changes are needed, write:
```json
{{"status": "stale", "additions": [...], "removals": [...], "reason": "..." }}
```
