# Task: Rank File Relevance for Section

## Section
Read: `{section_file}`

## Related Files
{file_list_text}

## Instructions
Rank each file into a tier based on how central it is to this section's concern:
- **tier-1**: Core files — directly implement or define the section's concern
- **tier-2**: Supporting files — needed for context but not primary targets
- **tier-3**: Peripheral files — tangentially related, low priority

Also decide which tiers should be deep-scanned NOW. Consider:
- Always include tier-1
- Include tier-2 if the section has complex integration concerns
- Include tier-3 only if the section scope is unclear and peripheral context helps

You own the scan budget. The script will scan exactly the tiers you
specify in `scan_now` — no more, no less. There are no script-level
caps or overrides. If you think all tiers need scanning, include all
of them.

Write a JSON file to: `{tier_file}`
```json
{{"tiers": {{"tier-1": ["path/a"], "tier-2": ["path/b"], "tier-3": ["path/c"]}}, "scan_now": ["tier-1", "tier-2"], "reason": "why these tiers need scanning"}}
```
