# Task: Update Related Files for {section_name}

## Files to Read
1. Section: `{section_file}`
2. Codemap: `{codemap_path}`

{missing_section}{irrelevant_section}## Instructions
Review the candidates above against the section's problem and related files.
Write an update signal:

Write to: `{updater_signal}`
```json
{{"status": "stale", "additions": ["path/to/add.py"], "removals": ["path/to/remove.py"], "reason": "deep scan feedback: added missing dependencies, removed irrelevant files"}}
```

Only include additions that are genuinely relevant. Only include removals
when confident the file is unrelated to the section's concern.
