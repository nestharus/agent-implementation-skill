# Task: Identify Files Related to This Section

You have a codemap of the project and a section from the proposal. Your job is to figure out which files in the codebase are related to this section's goals.

## Files to Read
1. Codemap: `{codemap_path}`
2. Section specification: `{section_file}`
3. Codemap corrections (if exists): `{corrections_signal}`

## How to Work

Read the codemap first — it tells you where things are and how they relate.
If codemap corrections exist, read them and treat as authoritative fixes to
the codemap (wrong paths, missing entries, misclassified files). Then read
the section specification. Explore specific files or directories to confirm
relevance. Use GLM agents for quick file reads.

Think strategically:
- Which parts of the codebase does this section need to modify?
- Which files define interfaces or contracts this section depends on?
- Which files might be affected as a consequence of this section's changes?
- Don't list every file — focus on files that actually matter for this section.

## Output Format

Write a markdown block starting with `## Related Files` followed by `### <relative-path>` entries with a brief reason for each file. Example:

## Related Files

### src/config
Defines configuration structure that this section needs to extend with event settings.

### src/core/engine
Core processing loop where event emission hooks need to be added.
