# Task: Update Related Files for {section_name}

## Files to Read
1. Section: `{section_file}`
2. Codemap: `{codemap_path}`{corrections_ref}

{missing_section}{irrelevant_section}## Instructions

If codemap corrections exist, treat them as authoritative fixes to the
codemap (wrong paths, missing entries, misclassified files).
Review the candidates above against the section's problem and related files.
Write an update signal:

Write to: `{updater_signal}`
```json
{{"status": "stale", "additions": ["path/to/add"], "removals": ["path/to/remove"], "reason": "deep scan feedback: added missing dependencies, removed irrelevant files"}}
```

Only include additions that are genuinely relevant. Only include removals
when confident the file is unrelated to the section's concern.
