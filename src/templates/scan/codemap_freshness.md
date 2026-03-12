# Task: Evaluate Codemap Freshness

The codespace has changed since the codemap was last built.

## Files to Read
1. Current codemap: `{codemap_path}`
2. Codespace root: `{codespace}`{corrections_ref}

## What Changed
{change_description}

## Instructions

Quickly scan the codespace structure (list top-level dirs, check key files
mentioned in the codemap's Routing Table). Determine whether the existing
codemap is still a valid routing map or needs rebuilding.

If codemap corrections exist, treat them as authoritative fixes to the
codemap when judging whether routing is still valid.

Write your decision as a structured JSON signal to `{freshness_signal}`:

```json
{{"rebuild": true|false, "reason": "brief explanation"}}
```

- `rebuild: false` if the codemap's routing table and subsystem descriptions
  are still accurate (minor file additions/changes don't invalidate routing)
- `rebuild: true` if major structural changes occurred (new directories,
  removed subsystems, reorganized code) that make the routing table wrong
