# Investigation: Brownfield Scan Does Not Rewrite Stale Related Files

## Context

An agentic eval scenario (`scan-brownfield-related-files-revalidation`) exposed a behavioral gap:
when `scan.cli quick` runs against a section that already has a `## Related Files` block containing
stale entries (files that no longer exist or aren't relevant), the scan **validates** the section
but does **not rewrite** the stale entries.

The eval log shows:
```
[EXPLORE] section-01: validating Related Files against updated codemap/section
[EXPLORE] section-01: validation complete
```

But the section's Related Files block still contains the old stale entries (e.g., `legacy_refunds.py`,
`old_rules.py`) and does NOT contain the current relevant files (`src/payments/refund_service.py`,
`src/payments/approvals.py`).

## Your Task

Investigate the scan system to understand:

1. **How does the scan detect brownfield vs greenfield sections?**
   - Read `src/scripts/scan/exploration.py` (the `run_section_exploration` function)
   - What determines whether a section gets fresh Related Files vs validation-only?

2. **What does "validation" do for brownfield sections?**
   - Trace the validation path — what code runs when existing Related Files are found?
   - Does the validation check if entries are still valid? Does it have the ability to rewrite?

3. **Where is the gap?**
   - Is there a code path that SHOULD rewrite stale entries but doesn't?
   - Or is the rewrite capability entirely missing (never implemented)?
   - Is there a signal/flag that should trigger a rewrite but isn't being checked?

4. **What would a fix look like conceptually?**
   - Where in the code should the rewrite happen?
   - What information is available at that point (codemap, section content, etc.)?
   - Are there any constraints (e.g., should stale detection be LLM-based or hash-based)?

## Files to Read

Start with these files and follow imports as needed:
- `src/scripts/scan/exploration.py` — section exploration entry point
- `src/scripts/scan/codemap.py` — codemap build
- `src/scripts/lib/scan/scan_related_files.py` — related files logic
- `src/scripts/scan/cli.py` — CLI entry point (already known)

## Output

Write your findings to `$PROJECT_ROOT/.tmp/agentic-evals/brownfield-investigation.md` with:
- A clear explanation of how the current code works
- The exact gap (missing code path, missing condition, etc.)
- What information is available at the gap point
- Suggested fix approach (conceptual, not implementation)
