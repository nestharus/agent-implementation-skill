# Task: Analyze File Relevance for Section

Read this file in the context of the section's goals. Explain what parts of the file matter for this section and why. Note any concerns, dependencies, or open questions you discover.

## Files to Read
1. Section specification: `{section_file}`
2. Source file: `{abs_source}`
3. Codemap (for context): `{codemap_path}`{corrections_ref}

## Instructions

Read both files. Reason about the source file in context of the section. What specific parts are relevant to the section? Are there functions, classes, configurations, or patterns that the section will need to interact with? Are there risks or complications? Write your analysis naturally — focus on what someone implementing this section needs to know about this file.

## Feedback (IMPORTANT)

After your analysis, write a JSON feedback file to: `{feedback_file}`

Format:
```json
{{
  "source_file": "{source_file}",
  "relevant": true,
  "missing_files": ["path/to/file1", "path/to/file2"],
  "summary_lines": ["Key finding one.", "Key finding two."],
  "reason": "Brief explanation if not relevant, or why missing files matter"
}}
```

- `source_file`: The relative path to the file being analyzed (copy the
  value above exactly — this preserves traceability from feedback to file).
- `relevant`: Is this file actually relevant to the section? Set false if
  the file was incorrectly included (e.g., shares a name but different concern).
- `missing_files`: Files NOT in the section's list that SHOULD be. Only
  include files you discovered while reading this file (imports, callers,
  shared config, etc.) that the section will need. Use paths relative to
  the codespace root.
- `out_of_scope`: (optional) List of problems or concerns discovered that
  are OUTSIDE this section's scope. Each entry should describe what the
  problem is and which section or higher level should handle it.
- `summary_lines`: A list of 1-3 short strings summarizing the key findings
  for this file. Each string should be a single sentence. These are embedded
  directly into the section file as routing context — do NOT include markdown
  formatting or filler phrases.
- `reason`: Brief explanation.
