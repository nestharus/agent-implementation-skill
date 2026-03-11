# Design Request: Fix Two Agentic Workflow Bugs Found by Eval Harness

Two behavioral bugs were discovered by our agentic eval harness. Both need targeted fixes
that preserve existing safety guarantees. For each bug, provide a concrete design with
exact file paths, function names, and code-level changes.

---

## Bug 1: Brownfield Scan Does Not Rewrite Stale Related Files

### Symptom

When `scan.cli quick` runs against a section that already has a `## Related Files` block
containing stale entries (files that no longer exist or aren't relevant), the scan validates
the section but does NOT rewrite the stale entries.

Eval log:
```
[EXPLORE] section-01: validating Related Files against updated codemap/section
[EXPLORE] section-01: validation complete
```

Section still contains `legacy_refunds.py`, `old_rules.py` instead of `src/payments/refund_service.py`.

### Root Cause (from investigation)

Three related gaps in `src/scripts/lib/scan/scan_related_files.py`:

**Gap A**: Sections with any existing `## Related Files` heading are locked into validation-only
via `src/scripts/scan/exploration.py:38-51`. They never get fresh exploration.

**Gap B** (concrete runtime bug): `validate_existing_related_files()` treats a successful agent
exit code as success even when no structured signal file is produced. It then caches the combined
hash in `codemap-hash.txt`, suppressing future revalidation. The signal file is the ONLY mechanism
that can trigger `apply_related_files_update()`.

Relevant code path:
- `src/scripts/lib/scan/scan_related_files.py:165-225` — post-dispatch signal handling
- `src/scripts/lib/scan/scan_related_files.py:103-122` — hash-based skip gate

**Gap C**: The adjudicator agent prompt (`src/agents/scan-related-files-adjudicator.md`) says
"do not explore the filesystem" and "don't remove files just because they weren't mentioned."
This is too conservative for stale brownfield repair.

### What exists that works

- `apply_related_files_update()` at lines 29-79 can mechanically rewrite the block (remove/add entries)
- Deep-scan feedback updater (`src/scripts/scan/feedback.py:242-300`) has proper fail-closed handling
- The validation agent receives section content + codemap + codespace access

### Design constraints

- The rewrite primitive exists — don't rebuild it
- Hash caching must only happen after a valid signal is produced
- Fail-closed: no signal = no cache = retry next run
- The adjudicator must be able to detect genuinely stale entries (files that don't exist)
- LLM-based relevance judgment should remain for non-obvious cases

### What I need from you

1. Exact changes to `validate_existing_related_files()` in `src/scripts/lib/scan/scan_related_files.py`
   to enforce fail-closed signal handling (no signal = no hash cache)
2. Whether to add a deterministic pre-check (file existence verification) before dispatch
3. How to handle the case where the adjudicator returns `status: "current"` but files don't exist
4. Any prompt changes needed for `src/agents/scan-related-files-adjudicator.md`

---

## Bug 2: Philosophy Bootstrap Grounding Validation False Positive

### Symptom

Philosophy bootstrap correctly distills P1-P6 principles from user source material, but
grounding validation rejects the output because it finds "unmapped" principle IDs P7 and P8
in the expansion guidance section of `philosophy.md`.

Eval log:
```
[section-loop] Intent bootstrap: philosophy grounding validation failed — blocking section (fail-closed)
```

Judge found: P1-P6 correct, source map complete, but bootstrap_state='failed' instead of 'ready'.

### Root Cause (from investigation)

In `src/scripts/lib/intent/philosophy_bootstrap.py`, the grounding validator uses:
```python
principle_ids = set(re.findall(r"\bP\d+\b", philosophy_text))
```

This scans the ENTIRE document including prose. The distiller writes expansion guidance like:
```
currently P6
Use sequential numbering (P7, P8, ...)
```

The validator sees P7 and P8 as real principles, finds them missing from the source map, and
fail-closes the bootstrap.

### Failure chain

1. `_classify_distiller_result()` returns `valid_nonempty` (correct)
2. `validate_philosophy_grounding()` extracts P1-P8 from regex
3. Source map has P1-P6 only
4. Unmapped = {P7, P8} → writes NEEDS_PARENT signal + failed status
5. Bootstrap blocked

### What should remain strict

- Every DECLARED principle must have a source-map entry
- Source map entries must keep current schema checks
- Malformed/incomplete source maps should still fail closed

### What should NOT be required

- Source-map entries for hypothetical numbering examples in expansion guidance
- Source-map entries for incidental P\d+ references in prose

### Secondary issue

`validate_philosophy_grounding()` writes failure status with `source_mode="repo_sources"` even
during user-source bootstrap — makes failure metadata inaccurate.

### Design constraints

- Preserve the fail-closed quality gate
- Don't weaken the check for actual declared principles
- The fix should be in the validator, not in the distiller prompt (the distiller's output is correct)

### What I need from you

1. Exact change to the principle ID extraction in `validate_philosophy_grounding()` to only
   match declared principles (not prose mentions)
2. What constitutes a "declared principle" — heading pattern? Section boundary?
3. Whether to also fix the `source_mode` metadata bug
4. Any edge cases to watch for (e.g., principles defined in non-standard heading formats)

---

## Deliverables

For each bug, provide:
1. **Exact code changes** — file path, function, what to change and why
2. **Test expectations** — what the eval should see after the fix
3. **Edge cases** — what could go wrong with the fix
