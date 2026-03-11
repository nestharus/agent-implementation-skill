# Philosophy Bootstrap Grounding Investigation

## Summary

This is a code issue, not a fixture issue.

The bootstrap flow successfully uses the seeded user source, distills a good `philosophy.md`, and writes a valid `philosophy-source-map.json` for `P1` through `P6`. The failure happens in grounding validation because the validator treats every `P\d+` token anywhere in `philosophy.md` as a real distilled principle. In the reproduced eval output, the distiller writes expansion guidance that says:

- `currently P6`
- `Use sequential numbering (P7, P8, ...)`

The validator extracts `P7` and `P8` from that prose, sees that they are not present in the source map, and fail-closes the bootstrap as invented/unmapped principles.

## 1. How the Bootstrap Flow Works

Primary entry point: `src/scripts/lib/intent/philosophy_bootstrap.py`

Flow:

1. `ensure_global_philosophy(...)` starts by writing bootstrap status `discovering`.
2. If `artifacts/intent/global/philosophy-source-user.md` exists and is larger than `MIN_USER_SOURCE_BYTES`, bootstrap skips repo selection and uses that file directly as the sole source with `source_type="user_source"`.
3. It still builds a candidate catalog, but with a substantive user source it bypasses selector and verifier stages and sets `selected = {"sources": source_records}`.
4. It writes the distillation prompt (`philosophy-distill-prompt.md`) instructing the distiller to:
   - write `philosophy.md`
   - write `philosophy-source-map.json`
   - number principles `P1..PN`
   - include interactions
   - include expansion guidance
5. After dispatch, `_classify_distiller_result(...)` checks only artifact presence/basic validity:
   - both files must exist
   - `philosophy.md` must be readable
   - `philosophy-source-map.json` must parse as a non-empty JSON object
   - each map entry must have valid `source_type`, `source_file`, `source_section`
6. If classification is `valid_nonempty`, bootstrap runs `validate_philosophy_grounding(...)`.
7. If grounding passes, bootstrap writes:
   - `philosophy-source-manifest.json`
   - `philosophy-catalog-fingerprint.txt`
   - bootstrap status `ready`
   - no active blocking signal
8. If grounding fails, bootstrap writes:
   - `philosophy-bootstrap-signal.json` with state `NEEDS_PARENT`
   - `philosophy-bootstrap-status.json` with `bootstrap_state="failed"`
   - returns failed result to the caller

## 2. What Grounding Validation Checks

Validator: `validate_philosophy_grounding(...)`

It does not check semantic alignment of principle text against the source contents. It checks artifact grounding mechanically:

- source map file exists and is non-empty
- source map parses as JSON
- source map is a JSON object
- every source map entry is shaped like:
  - key: principle id such as `P1`
  - value: object with non-empty `source_type`, `source_file`, `source_section`
- `source_type` must be one of `repo_source` or `user_source`
- every detected principle id in `philosophy.md` must appear as a key in the source map

The critical extraction is:

- `principle_ids = set(re.findall(r"\bP\d+\b", philosophy_text))`

That regex scans the entire markdown file, not just the actual principle headings.

Failure branch:

- `unmapped = principle_ids - map_keys`
- if `unmapped` is non-empty, validator writes a blocking signal and failed status

The exact message emitted in the reproduced eval was:

`Principle IDs missing from source map: ['P7', 'P8']. Distilled philosophy may contain invented principles. Section execution will be blocked.`

## 3. Why It Fails Here

The fixture seeds a strong user philosophy source. The distiller output is correct on the intended behavior:

- `P1` through `P6` are present
- all six source-map entries point to `philosophy-source-user.md`
- the source map schema is valid

The failure is caused by expansion guidance text inside `philosophy.md`, not by bad grounding for actual principles.

Reproduced generated output:

- principles section defines only `P1`..`P6`
- expansion guidance says `currently P6` and `Use sequential numbering (P7, P8, ...)`

Because validation uses a whole-document regex, it incorrectly treats `P7` and `P8` as if they were already-distilled principles requiring source-map entries.

So the gate is too strict in the wrong dimension:

- it is strict about any `P\d+` token anywhere in the document
- it is not specifically strict about actual principle declarations

## 4. Exact Failure Condition

In this scenario the sequence is:

1. `_classify_distiller_result(...)` returns `valid_nonempty`
2. `validate_philosophy_grounding(...)` computes:
   - detected ids from regex: `P1..P8`
   - source map keys: `P1..P6`
   - unmapped: `P7`, `P8`
3. Validator writes:
   - `signals/philosophy-bootstrap-signal.json` with `state="NEEDS_PARENT"`
   - `intent/global/philosophy-bootstrap-status.json` with `bootstrap_state="failed"`
4. `ensure_global_philosophy(...)` logs:
   - `Intent bootstrap: philosophy grounding validation failed — blocking section (fail-closed)`

Observed artifact details from the kept failed eval:

- `philosophy.md` is semantically correct
- `philosophy-source-map.json` is valid and complete for `P1..P6`
- blocking signal detail explicitly names `P7` and `P8` as unmapped

## 5. Fixture Issue or Code Issue

Code issue.

Reasons:

- The fixture provides the exact minimal valid user-authored philosophy source the bootstrap is meant to support.
- The distiller output satisfies the eval’s intended contract for actual principles and provenance.
- The failure comes from validator overreach when reading non-principle prose in the same document.
- No extra seeded files are missing.
- No missing repository path or broken source file caused this.

The fixture should not need to seed fake `P7`/`P8` provenance for hypothetical future principles mentioned only in expansion guidance.

## 6. Suggested Fix Approach

Preserve the quality gate, but narrow it to real principle declarations.

Best conceptual fix:

- Change grounding validation so it extracts principle ids only from the principle-definition structure, not from arbitrary prose.
- Example approaches:
  - parse only headings like `### P1:`
  - parse only the `## Principles` section
  - use a stricter multiline regex anchored to heading syntax instead of `\bP\d+\b`

What should remain strict:

- every actual declared principle must have a source-map entry
- source map entries must keep the current schema checks
- malformed or incomplete source maps should still fail closed

What should not be required:

- source-map entries for hypothetical numbering examples in expansion guidance
- source-map entries for incidental references to principle ids in prose

Secondary cleanup:

- `validate_philosophy_grounding(...)` currently writes failure status with `source_mode="repo_sources"` even in a user-source bootstrap. That did not cause this failure, but it makes failure metadata inaccurate and should be corrected if the function is touched.
