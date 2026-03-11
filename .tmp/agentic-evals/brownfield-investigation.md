Findings are written to [brownfield-investigation.md](/home/nes/projects/agent-implementation-skill/.tmp/agentic-evals/brownfield-investigation.md).

The core conclusion is that scan does not use a real brownfield/greenfield classifier here. It uses presence of `## Related Files` as the branch condition, which forces existing sections onto validation-only. Rewrite support exists, but only if validation emits a `status: "stale"` update signal. The main gap is that `validate_existing_related_files()` treats a zero-exit validation run as success even when no valid signal is produced, then caches the hash and suppresses future rewrites. I also called out the weaker prompt contract on the adjudicator path and the likely fix point in `src/scripts/lib/scan/scan_related_files.py`.
ration or a full rewrite path.

## 1. How brownfield vs greenfield is currently detected

In `src/scripts/scan/exploration.py`, `run_section_exploration()` decides the path by checking the section markdown text itself:

- `if "## Related Files" in section_text:` -> call `validate_existing_related_files(...)`
- else -> call `_explore_section(...)`

Relevant lines:

- `src/scripts/scan/exploration.py:38-51`
- `src/scripts/scan/exploration.py:53-63`

Important consequences:

- This is not using project mode, codemap structure, or a separate brownfield/greenfield signal.
- Presence of the heading alone is enough.
- An empty, partial, or stale block is still treated as "existing related files", so it takes validation-only.

`src/scripts/scan/codemap.py` does not participate in this branch decision. Its role here is:

- build or reuse `codemap.md`
- maintain freshness/fingerprint state
- provide the codemap artifact that validation and exploration read

So the practical distinction is:

- "greenfield" section for scan exploration = no `## Related Files` heading
- "brownfield" section for scan exploration = has `## Related Files` heading

## 2. What validation does for brownfield sections

The brownfield path is `validate_existing_related_files()` in `src/scripts/lib/scan/scan_related_files.py`.

### Step A: Decide whether to skip validation

The function computes a combined hash from:

- current codemap file hash
- codemap corrections file hash
- section content hash, after stripping scan summary noise

Relevant lines:

- `src/scripts/lib/scan/scan_related_files.py:103-110`

If the combined hash matches the previously stored hash in `scan-logs/<section>/codemap-hash.txt`, validation is skipped:

- `src/scripts/lib/scan/scan_related_files.py:116-122`

This hash is only an invalidation gate. It does not determine whether the list is semantically stale.

### Step B: Dispatch the validation agent

If the combined hash changed, the code:

- writes `validate-prompt.md`
- dispatches `scan-related-files-adjudicator.md`
- tells the agent to write a structured signal to `artifacts/signals/<section>-related-files-update.json`

Relevant lines:

- `src/scripts/lib/scan/scan_related_files.py:124-163`
- `src/scripts/scan/templates/validate_related_files.md:1-19`

The template says:

- read the section
- read the codemap
- check whether the list is still accurate
- write either `{"status": "current"}` or `{"status": "stale", "additions": [...], "removals": [...], "reason": "..."}`

### Step C: Apply a rewrite only if the signal says `status == "stale"`

After the agent returns successfully, the runtime looks for the signal file.

If the file exists and parses, then:

- `status == "stale"` -> call `apply_related_files_update()`
- `status in ("ok", "applied")` -> accept as already handled/current
- other non-empty statuses -> warn and force revalidation next run

Relevant lines:

- `src/scripts/lib/scan/scan_related_files.py:165-225`

`apply_related_files_update()` can rewrite the block mechanically:

- remove entries whose headings match `removals`
- append entries for `additions`

Relevant lines:

- `src/scripts/lib/scan/scan_related_files.py:29-79`

So yes, validation does have the ability to rewrite, but only through the update-signal path.

## 3. Exact gap

There are two closely related gaps.

### Gap A: Existing block means validation-only, never fresh exploration

Once a section contains `## Related Files`, the scan code will never call `_explore_section()` for it.

That means stale sections depend entirely on the adjudicator producing a stale update signal. There is no alternate path that says "this existing block is too stale, regenerate the Related Files block from scratch."

This is a missing code path, not a missing editor primitive.

### Gap B: Validation does not require a valid update signal

This is the more concrete runtime bug.

In `validate_existing_related_files()`:

- successful agent exit code logs `validation complete`
- if the signal file does not exist at all, the function still leaves `write_hash = True`
- then it writes `codemap-hash.txt`

Relevant lines:

- `src/scripts/lib/scan/scan_related_files.py:165-225`

Effect:

- validation can "succeed" without producing any structured decision
- no rewrite occurs
- stale entries remain in the section
- the new combined hash is cached, so the system will skip future validation until codemap/section changes again

This matches the eval symptom:

- log shows validation ran and completed
- stale entries remain
- no new relevant files appear

The code path that should protect against this does not exist.

By contrast, the deep-scan feedback updater has stricter handling:

- it validates that a signal exists and contains a `status`
- it can escalate if the first updater attempt produces no valid signal

Relevant lines:

- `src/scripts/scan/feedback.py:242-300`
- `src/scripts/lib/scan/scan_feedback_router.py:9-19`

The brownfield validation path has no equivalent fail-closed check or escalation.

### Gap C: Prompt/agent contract is weak for stale-list regeneration

The validation path uses `scan-related-files-adjudicator.md`, whose instructions are framed as:

- "Evidence-driven adjustment, not re-exploration"
- "Do not explore the filesystem to discover new candidates"
- "Don't remove files just because they weren't mentioned in new evidence"

Relevant lines:

- `src/agents/scan-related-files-adjudicator.md:17-23`
- `src/agents/scan-related-files-adjudicator.md:42-50`
- `src/agents/scan-related-files-adjudicator.md:63-73`

That prompt is a good fit for deep-scan feedback adjudication, where the system already has candidate additions/removals.
It is a weaker fit for stale brownfield section repair, where the only evidence is:

- current section intent
- current codemap
- optional codemap corrections

So the rewrite capability is not entirely missing, but the stale-detection and candidate-generation step is under-specified and under-enforced in the brownfield validation path.

## 4. What information is available at the gap point

At the point where `validate_existing_related_files()` decides what to do, the system has:

- the full section markdown file
- the current `codemap.md`
- optional `codemap-corrections.json`
- the previous validation hash
- the new combined hash
- a writable target signal path for structured updates
- access to the codespace for the dispatched agent (`project=codespace`)

What the runtime itself does not compute:

- the current parsed related-files set vs a newly derived candidate set
- whether any listed paths no longer exist in the codespace
- a deterministic stale/removal/addition set before dispatch

So the runtime has enough information to support a rewrite decision, but today it outsources that entirely to the agent and then does not require the agent to return a valid signal.

## 5. What a conceptual fix should look like

The fix belongs in the brownfield validation flow, centered on `validate_existing_related_files()`.

### Required behavioral change

Validation should not be considered complete unless it produces a valid structured decision.

Conceptually:

1. Dispatch validation.
2. Require a valid signal file with a recognized status.
3. If the signal is `stale`, apply the rewrite.
4. If the signal is `current`, cache the hash.
5. If the signal is missing or malformed, do not cache the hash. Either:
   - force revalidation on the next run, or
   - escalate/fallback immediately to a stronger rewrite path.

This is the same fail-closed posture already used elsewhere in scan.

### Better brownfield repair path

The stronger conceptual fix is to split two concerns:

- adjudication of known additions/removals
- regeneration of a stale Related Files block

For a stale brownfield block, a robust flow would be:

1. Use the current section + codemap (+ corrections) to derive a fresh candidate related-files set.
2. Diff that candidate set against the current block.
3. Apply additions/removals mechanically.

That candidate derivation could be done either by:

- reusing the explorer-style logic for a fresh candidate list, then diffing
- or keeping the validator but strengthening its contract so it must produce the full candidate change set

### Deterministic vs LLM-based stale detection

Hashing is not enough. The current hashes only answer "did inputs change?" not "is the current Related Files block still correct?"

A practical approach is hybrid:

- deterministic checks for obvious stale entries:
  - related-file path no longer exists in codespace
  - duplicate entries
  - malformed headings
- semantic/LLM judgment for relevance and additions:
  - which current files best match the section concern
  - which files are true replacements for outdated entries

So:

- existence checks can cheaply identify some removals
- relevance and missing-file discovery should remain codemap/LLM-based

## 6. Testing gap

Current tests mostly prove the mechanical apply path, not the brownfield validation contract.

Examples:

- `tests/component/test_scan_related_files.py:130-195` pre-seeds a stale update signal before validation runs
- `tests/integration/test_scan_stage3.py:221-240` similarly mocks dispatch to create a valid signal

What is missing:

- a test that validation returns success but writes no signal, and the hash must not be cached
- a test that a stale brownfield section gets rewritten from the validation path end-to-end
- ideally, an eval/fixture asserting that a stale section with obsolete paths is repaired into current paths

## Bottom line

The rewrite primitive already exists. The gap is in the brownfield validation control flow:

- sections with any existing `## Related Files` block are locked into validation-only
- validation depends on an agent-authored stale signal
- missing signal is treated as success instead of failure
- there is no fallback to fresh regeneration when adjudication does not produce a rewrite

That is why a stale brownfield Related Files block can survive a "validation complete" run unchanged.
