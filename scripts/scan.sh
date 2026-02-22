#!/usr/bin/env bash
set -euo pipefail

# scan.sh — Stage 3 scan entrypoint and phase coordinator.
#
# Public CLI contract:
#   scan.sh <quick|deep|both> <planspace> <codespace>
#
# quick:
# - dispatch an Opus agent to explore the codespace and build a codemap
# - dispatch Opus agents per section to identify related files using the codemap
#
# deep:
# - for each section's confirmed related files, dispatch an agent to reason
#   about specific relevance in context
#
# both:
# - run quick, then deep
#
# Design: scripts do mechanical coordination (dispatch, check, log).
# Agents do reasoning (explore, understand, decide relevance).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_HOME="${WORKFLOW_HOME:-$(dirname "$SCRIPT_DIR")}"

cmd="${1:?Usage: scan.sh <quick|deep|both> <planspace> <codespace>}"
PLANSPACE="${2:?Missing planspace path}"
CODESPACE="${3:?Missing codespace path}"

ARTIFACTS_DIR="$PLANSPACE/artifacts"
SECTIONS_DIR="$PLANSPACE/artifacts/sections"
CODEMAP_PATH="$ARTIFACTS_DIR/codemap.md"
SCAN_LOG_DIR="$ARTIFACTS_DIR/scan-logs"
mkdir -p "$SCAN_LOG_DIR"

validate_preflight() {
  if [ ! -d "$CODESPACE" ] || [ ! -r "$CODESPACE" ]; then
    echo "[ERROR] Missing or inaccessible codespace: $CODESPACE" >&2
    return 1
  fi

  if [ ! -d "$SECTIONS_DIR" ]; then
    echo "[ERROR] Missing sections directory: $SECTIONS_DIR" >&2
    return 1
  fi

  local section_count
  section_count=$(find "$SECTIONS_DIR" -name "section-*.md" | wc -l | tr -d ' ')
  if [ "$section_count" -eq 0 ]; then
    echo "[ERROR] No section files found in: $SECTIONS_DIR" >&2
    return 1
  fi
}

list_section_files() {
  find "$SECTIONS_DIR" -name "section-*.md" | sort
}

log_phase_failure() {
  local phase="$1"
  local context="$2"
  local message="$3"
  local failure_log="$SCAN_LOG_DIR/failures.log"

  printf '%s phase=%s context=%s message=%s\n' \
    "$(date -Iseconds 2>/dev/null || date)" \
    "$phase" "$context" "$message" >> "$failure_log"
  echo "[FAIL] phase=$phase context=$context message=$message" >&2
}

# --- Quick Scan ---
# Dispatches agents to explore and understand the codebase.
# Agents reason about what they find; the script only coordinates.

run_codemap_build() {
  if [ -s "$CODEMAP_PATH" ]; then
    echo "[CODEMAP] Reusing existing artifact: $CODEMAP_PATH"
    return 0
  fi

  mkdir -p "$(dirname "$CODEMAP_PATH")"
  local prompt_file="$SCAN_LOG_DIR/codemap-prompt.md"
  local stderr_file="$SCAN_LOG_DIR/codemap.stderr.log"

  cat > "$prompt_file" << 'PROMPT'
# Task: Explore Codebase and Build Codemap

You are an exploration agent. Your job is to understand this codebase by exploring it — not by following a template or enforcing a fixed structure.

## How to Explore

1. Start with the root: list the top-level directory to see what's there
2. Read key files that help you understand purpose: README, configuration files, entry points
3. Explore directories that seem important — read files, understand relationships
4. Use GLM agents for quick file reads when you need to check many files
5. Follow your curiosity — if something looks important, investigate it

## What to Write

Write a codemap that captures your understanding of the codebase. Include:
- What this project is and does
- How the code is organized (not just a directory listing — what the organization *means*)
- Key files and why they matter
- How different parts relate to each other
- Anything surprising, unusual, or important for someone working with this code

The format should fit what you discovered. Don't force the codebase into a template — let the structure of the codemap reflect the structure of the project. If it has 3 major subsystems, describe 3 subsystems. If it has 20 small utilities, describe the pattern. If it's a single-file script, say so.

## Output

Write your codemap as markdown. Focus on understanding, not cataloging.

## Resolution Rubric

Keep the codemap COARSE by default. Only include detailed internals
(function signatures, class hierarchies) for files that appear in 2+
section specifications. For everything else, describe purpose and
relationships, not implementation details.

The codemap is a ROUTING MAP — it helps agents find the right files,
not understand every line.

## Project Mode Classification

After writing the codemap, determine whether this is a **greenfield** or
**brownfield** project:
- **greenfield**: Empty or near-empty project (only config/scaffold files,
  no substantive source code yet)
- **brownfield**: Existing source code that new work must integrate with

Write your classification to: \`$ARTIFACTS_DIR/project-mode.txt\`
The file should contain EXACTLY one word: \`greenfield\` or \`brownfield\`.

**Also write a structured JSON signal** to
\`$ARTIFACTS_DIR/signals/project-mode.json\`:
\`\`\`json
{"mode": "greenfield|brownfield", "confidence": "high|medium|low", "reason": "..."}
\`\`\`
PROMPT

  if ! uv run --frozen agents --model claude-opus --project "$CODESPACE" --file "$prompt_file" \
    > "$CODEMAP_PATH" 2> "$stderr_file"; then
    log_phase_failure "quick-codemap" "$(basename "$CODEMAP_PATH")" "codemap agent failed (see $stderr_file)"
    return 1
  fi

  if ! grep -q '[^[:space:]]' "$CODEMAP_PATH"; then
    log_phase_failure "quick-codemap" "$(basename "$CODEMAP_PATH")" "codemap agent produced empty output"
    return 1
  fi

  echo "[CODEMAP] Wrote: $CODEMAP_PATH"
}

apply_related_files_update() {
  local section_file="$1"
  local signal_file="$2"

  uv run python - "$section_file" "$signal_file" << 'APPLY_PY'
import json
import re
import sys
from pathlib import Path

section_path = Path(sys.argv[1])
signal_path = Path(sys.argv[2])

if not signal_path.exists():
    raise SystemExit(0)

try:
    signal = json.loads(signal_path.read_text())
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

if signal.get("status") != "stale":
    raise SystemExit(0)

section = section_path.read_text()
removals = signal.get("removals", [])
additions = signal.get("additions", [])

if not removals and not additions:
    raise SystemExit(0)

# Process removals: remove ### <path> blocks from the section
for rm_path in removals:
    marker = f"### {rm_path}"
    idx = section.find(marker)
    if idx == -1:
        continue
    # Find the end of this block: next ### or next ## (non-###) or EOF
    rest = section[idx + len(marker):]
    match = re.search(r"\n(?=###\s|##\s[^#])", rest)
    if match:
        end_pos = idx + len(marker) + match.start()
    else:
        end_pos = len(section)
    # Remove the block (including any leading blank line)
    before = section[:idx].rstrip("\n")
    after = section[end_pos:]
    section = before + after

# Process additions: append under ## Related Files
for add_path in additions:
    marker = f"### {add_path}"
    if marker in section:
        continue  # already present
    rf_idx = section.find("## Related Files")
    if rf_idx == -1:
        continue
    # Find end of Related Files section: next ## that isn't ###, or EOF
    rf_rest = section[rf_idx + len("## Related Files"):]
    rf_match = re.search(r"\n(?=## [^#])", rf_rest)
    if rf_match:
        insert_pos = rf_idx + len("## Related Files") + rf_match.start()
    else:
        insert_pos = len(section)
    entry = f"\n\n### {add_path}\nAdded by validation — confirm relevance during deep scan."
    section = section[:insert_pos] + entry + section[insert_pos:]

section_path.write_text(section)
print(f"applied: {len(removals)} removals, {len(additions)} additions")
APPLY_PY
}

run_section_exploration() {
  local section_files
  section_files=$(list_section_files)

  while IFS= read -r section_file; do
    local section_name
    section_name=$(basename "$section_file" .md)

    # If section already has Related Files, run a lightweight validation
    # pass instead of skipping entirely. This avoids "stale truth" where
    # early file lists become wrong as the project evolves.
    if grep -q "^## Related Files" "$section_file" 2>/dev/null; then
      # Check if codemap OR section file changed since last exploration
      local codemap_hash_file="$SCAN_LOG_DIR/$section_name/codemap-hash.txt"
      local combined_hash=""
      local codemap_content=""
      local section_content=""
      if [ -f "$CODEMAP_PATH" ]; then
        codemap_content=$(sha256sum "$CODEMAP_PATH" 2>/dev/null | awk '{print $1}')
      fi
      section_content=$(sha256sum "$section_file" 2>/dev/null | awk '{print $1}')
      combined_hash=$(echo "${codemap_content}:${section_content}" | sha256sum | awk '{print $1}')

      local prev_hash=""
      if [ -f "$codemap_hash_file" ]; then
        prev_hash=$(cat "$codemap_hash_file")
      fi

      if [ "$combined_hash" = "$prev_hash" ] && [ -n "$prev_hash" ]; then
        echo "[EXPLORE] $section_name: Related Files exist, codemap+section unchanged — skipping"
        continue
      fi

      # Codemap or section changed (or first validation run) — dispatch validation
      echo "[EXPLORE] $section_name: validating Related Files against updated codemap/section"
      mkdir -p "$SCAN_LOG_DIR/$section_name"

      local validate_prompt="$SCAN_LOG_DIR/$section_name/validate-prompt.md"
      local validate_output="$SCAN_LOG_DIR/$section_name/validate-output.md"
      cat > "$validate_prompt" << VALIDATE_PROMPT
# Task: Validate Related Files List

## Files to Read
1. Section specification: \`$section_file\`
2. Codemap: \`$CODEMAP_PATH\`

## Instructions
This section already has a \`## Related Files\` list. Check whether it is
still accurate given the current codemap and section problem statement.

Propose a structured signal at \`$ARTIFACTS_DIR/signals/${section_name}-related-files-update.json\`:
\`\`\`json
{"status": "current|stale", "additions": ["path/to/add.py"], "removals": ["path/to/remove.py"], "reason": "..."}
\`\`\`

If the list is current, write \`{"status": "current"}\`.
If changes are needed, include additions and/or removals with reasons.
VALIDATE_PROMPT

      if uv run --frozen agents --model claude-opus --project "$CODESPACE" --file "$validate_prompt" \
        > "$validate_output" 2>&1; then
        echo "[EXPLORE] $section_name: validation complete"

        # P4: Apply validation results if stale (at most once per run)
        local list_updated_marker="$SCAN_LOG_DIR/$section_name/list_updated"
        if [ ! -f "$list_updated_marker" ]; then
          local signal_file="$ARTIFACTS_DIR/signals/${section_name}-related-files-update.json"
          if [ -f "$signal_file" ]; then
            local signal_status
            signal_status=$(python3 -c "
import json
try:
    print(json.load(open('$signal_file')).get('status',''))
except: print('')
" 2>/dev/null || true)
            if [ "$signal_status" = "stale" ]; then
              echo "[EXPLORE] $section_name: applying related-files updates"
              if apply_related_files_update "$section_file" "$signal_file"; then
                touch "$list_updated_marker"
                echo "[EXPLORE] $section_name: list updated — will not re-validate this run"
              else
                echo "[EXPLORE] $section_name: auto-apply failed — keeping existing list"
              fi
            fi
          fi
        else
          echo "[EXPLORE] $section_name: already updated this run — skipping re-validation"
        fi
      else
        echo "[EXPLORE] $section_name: validation failed — keeping existing list"
      fi

      # Save combined hash for next run
      echo "$combined_hash" > "$codemap_hash_file"
      continue
    fi

    local section_log_dir="$SCAN_LOG_DIR/$section_name"
    mkdir -p "$section_log_dir"
    local prompt_file="$section_log_dir/explore-prompt.md"
    local response_file="$section_log_dir/explore-response.md"
    local stderr_file="$section_log_dir/explore.stderr.log"

    cat > "$prompt_file" << PROMPT
# Task: Identify Files Related to This Section

You have a codemap of the project and a section from the proposal. Your job is to figure out which files in the codebase are related to this section's goals.

## Files to Read
1. Codemap: \`$CODEMAP_PATH\`
2. Section specification: \`$section_file\`

## How to Work

Read the codemap first — it tells you where things are and how they relate. Then read the section specification. Explore specific files or directories to confirm relevance. Use GLM agents for quick file reads.

Think strategically:
- Which parts of the codebase does this section need to modify?
- Which files define interfaces or contracts this section depends on?
- Which files might be affected as a consequence of this section's changes?
- Don't list every file — focus on files that actually matter for this section.

## Output Format

Write a markdown block starting with \`## Related Files\` followed by \`### <relative-path>\` entries with a brief reason for each file. Example:

## Related Files

### src/config.py
Defines configuration structure that this section needs to extend with event settings.

### src/core/engine.py
Core processing loop where event emission hooks need to be added.
PROMPT

    if ! uv run --frozen agents --model claude-opus --project "$CODESPACE" --file "$prompt_file" \
      > "$response_file" 2> "$stderr_file"; then
      log_phase_failure "quick-explore" "$section_name" "exploration agent failed (see $stderr_file)"
      continue
    fi

    # Append related files to section file
    if grep -q "^## Related Files" "$response_file" 2>/dev/null; then
      printf '\n' >> "$section_file"
      cat "$response_file" >> "$section_file"
      echo "[EXPLORE] $section_name — related files identified"
    else
      log_phase_failure "quick-explore" "$section_name" "agent output missing Related Files block"
    fi

  done <<< "$section_files"
}

run_quick_scan() {
  echo "=== Quick Scan: codemap exploration + per-section file identification ==="

  if ! run_codemap_build; then
    return 1
  fi

  run_section_exploration

  echo "=== Quick Scan Complete ==="
  return 0
}

# --- Deep Scan ---
# For each section's related files, dispatch an agent to analyze relevance
# in context. Agent writes analysis prose + structured feedback JSON
# (relevant, missing_files, reason) for post-scan aggregation.

deep_already_annotated() {
  local section_file="$1"
  local source_file="$2"
  local section_log_dir="$SCAN_LOG_DIR/$(basename "$section_file" .md)"

  # Check if a deep-scan response file exists for this (section, file) pair.
  # The response file is the authoritative record that deep analysis ran —
  # not a line-count heuristic on the section file content.
  local path_token extension_token source_hash safe_name
  path_token=$(echo "$source_file" | tr '/.' '__' | tr -cd '[:alnum:]_-' | cut -c1-80)
  extension_token="${source_file##*.}"
  [ "$extension_token" = "$source_file" ] && extension_token="noext"
  source_hash=$(printf '%s' "$source_file" | (sha1sum 2>/dev/null || shasum -a 1 2>/dev/null || python3 -c "import hashlib,sys; print(hashlib.sha1(sys.stdin.buffer.read()).hexdigest())") | awk '{print $1}' | cut -c1-10)
  safe_name="${path_token}.${extension_token}.${source_hash}"

  [ -s "$section_log_dir/deep-${safe_name}-response.md" ]
}

deep_scan_related_files() {
  local section_file="$1"
  awk '
    /^## Related Files$/ { in_related = 1; next }
    in_related && /^## / { in_related = 0 }
    in_related && /^### / { sub(/^### /, ""); print }
  ' "$section_file"
}

update_match() {
  local section_file="$1"
  local source_file="$2"
  local details_file="$3"

  uv run python - "$section_file" "$source_file" "$details_file" << 'PY'
import re
import sys
from pathlib import Path

section_path = Path(sys.argv[1])
source_file = sys.argv[2]
details_path = Path(sys.argv[3])

section = section_path.read_text()
details = details_path.read_text().strip()
if not details:
    raise SystemExit(0)

marker = f"### {source_file}"
idx = section.find(marker)
if idx == -1:
    raise SystemExit(0)

# Extract first 3 non-empty lines as summary (routing context only)
summary_lines = [l.strip() for l in details.split("\n") if l.strip()][:3]
summary = "\n".join(f"> {l}" for l in summary_lines)

rest = section[idx + len(marker):]
match = re.search(r"\n(?=###\s|##\s[^#])", rest)
insert_pos = idx + len(marker) + (match.start() if match else len(rest))

new_section = section[:insert_pos].rstrip() + "\n" + summary + "\n" + section[insert_pos:]
section_path.write_text(new_section)
PY
}

run_deep_scan() {
  # Skip deep scan for greenfield projects (no existing code to analyze)
  local mode_file="$ARTIFACTS_DIR/project-mode.txt"
  if [ -f "$mode_file" ] && [ "$(cat "$mode_file")" = "greenfield" ]; then
    echo "=== Deep Scan: skipped (greenfield project) ==="
    return 0
  fi

  echo "=== Deep Scan: agent-driven analysis of confirmed related files ==="

  local phase_failed=0

  local section_files
  section_files=$(list_section_files)

  while IFS= read -r section_file; do
    local section_name
    section_name=$(basename "$section_file" .md)

    local section_log_dir="$SCAN_LOG_DIR/$section_name"
    mkdir -p "$section_log_dir"

    # Skip deep scan for greenfield sections (no existing code to analyze)
    local sec_num
    sec_num=$(python3 -c "import re,sys; m=re.search(r'\d+',sys.argv[1]); print(m.group(0) if m else '')" "$section_name")
    local sec_mode_file="$ARTIFACTS_DIR/sections/section-${sec_num}-mode.txt"
    if [ -f "$sec_mode_file" ] && [ "$(cat "$sec_mode_file")" = "greenfield" ]; then
      echo "  $section_name: skipped (greenfield section)"
      # Create research artifact placeholder for greenfield sections
      local research_dir="$ARTIFACTS_DIR/research"
      mkdir -p "$research_dir"
      if [ ! -f "$research_dir/section-${sec_num}.md" ]; then
        echo "# Research: Section ${sec_num} (Greenfield)" > "$research_dir/section-${sec_num}.md"
        echo "" >> "$research_dir/section-${sec_num}.md"
        echo "This section was classified as greenfield. No existing code to analyze." >> "$research_dir/section-${sec_num}.md"
        echo "Research questions and design decisions should be captured here." >> "$research_dir/section-${sec_num}.md"
      fi
      continue
    fi

    local related_files
    related_files=$(deep_scan_related_files "$section_file" || true)

    if [ -z "$related_files" ]; then
      continue
    fi

    # Tier ranking: GLM ranks files by relevance to reduce scan scope
    local tier_file="$ARTIFACTS_DIR/sections/${section_name}-file-tiers.json"
    if [ ! -f "$tier_file" ]; then
      local tier_prompt="$section_log_dir/tier-prompt.md"
      local tier_output="$section_log_dir/tier-output.md"
      local file_list_text=""
      while IFS= read -r rf; do
        [ -z "$rf" ] && continue
        file_list_text="${file_list_text}\n- ${rf}"
      done <<< "$related_files"

      cat > "$tier_prompt" << TIER_PROMPT
# Task: Rank File Relevance for Section

## Section
Read: \`$section_file\`

## Related Files
$(echo -e "$file_list_text")

## Instructions
Rank each file into a tier based on how central it is to this section's concern:
- **tier-1**: Core files — directly implement or define the section's concern
- **tier-2**: Supporting files — needed for context but not primary targets
- **tier-3**: Peripheral files — tangentially related, low priority

Also decide which tiers should be deep-scanned NOW. Consider:
- Always include tier-1
- Include tier-2 if the section has complex integration concerns
- Include tier-3 only if the section scope is unclear and peripheral context helps

Write a JSON file to: \`$tier_file\`
\`\`\`json
{"tiers": {"tier-1": ["path/a.py"], "tier-2": ["path/b.py"], "tier-3": ["path/c.py"]}, "scan_now": ["tier-1", "tier-2"], "reason": "why these tiers need scanning"}
\`\`\`
TIER_PROMPT

      if uv run --frozen agents --model glm --project "$CODESPACE" --file "$tier_prompt" \
        > "$tier_output" 2>&1; then
        echo "[TIER] $section_name: file tiers ranked"
      else
        echo "[TIER] $section_name: tier ranking failed — scanning all files"
      fi
    fi

    # Determine scan scope from agent-chosen tiers (or default to tier-1 + tier-2)
    local scan_files="$related_files"
    if [ -f "$tier_file" ]; then
      local scoped_files
      scoped_files=$(python3 -c "
import json, sys
try:
    d = json.load(open('$tier_file'))
    tiers = d.get('tiers', {})
    scan_now = d.get('scan_now', ['tier-1', 'tier-2'])
    seen = set()
    for tier_name in scan_now:
        for f in tiers.get(tier_name, []):
            if f not in seen:
                seen.add(f)
                print(f)
except: pass
" 2>/dev/null || true)
      if [ -n "$scoped_files" ]; then
        scan_files="$scoped_files"
        local total_count scoped_count scan_tiers_label
        total_count=$(echo "$related_files" | grep -c '[^[:space:]]' || echo 0)
        scoped_count=$(echo "$scoped_files" | grep -c '[^[:space:]]' || echo 0)
        scan_tiers_label=$(python3 -c "
import json
try:
    d = json.load(open('$tier_file'))
    print('+'.join(d.get('scan_now', ['tier-1', 'tier-2'])))
except: print('tier-1+tier-2')
" 2>/dev/null || echo "tier-1+tier-2")
        echo "[TIER] $section_name: scanning $scoped_count files ($scan_tiers_label) of $total_count total"
      fi
    fi

    while IFS= read -r source_file; do
      [ -z "$source_file" ] && continue

      if deep_already_annotated "$section_file" "$source_file"; then
        continue
      fi

      local abs_source="$CODESPACE/$source_file"
      if [ ! -f "$abs_source" ]; then
        log_phase_failure "deep-scan" "${section_name}:${source_file}" "source file missing in codespace"
        phase_failed=1
        continue
      fi

      # Compute safe_name early (needed by both cache-hit and cache-miss paths)
      local safe_name
      local path_token extension_token source_hash
      path_token=$(echo "$source_file" | tr '/.' '__' | tr -cd '[:alnum:]_-' | cut -c1-80)
      extension_token="${source_file##*.}"
      if [ "$extension_token" = "$source_file" ]; then
        extension_token="noext"
      fi
      source_hash=$(printf '%s' "$source_file" | (sha1sum 2>/dev/null || shasum -a 1 2>/dev/null || python3 -c "import hashlib,sys; print(hashlib.sha1(sys.stdin.buffer.read()).hexdigest())") | awk '{print $1}' | cut -c1-10)
      safe_name="${path_token}.${extension_token}.${source_hash}"
      local prompt_file="$section_log_dir/deep-${safe_name}-prompt.md"
      local response_file="$section_log_dir/deep-${safe_name}-response.md"
      local stderr_file="$section_log_dir/deep-${safe_name}.stderr.log"
      local feedback_file="$section_log_dir/deep-${safe_name}-feedback.json"

      # File-card cache: reuse analysis if file+section content unchanged
      local file_cards_dir="$ARTIFACTS_DIR/file-cards"
      mkdir -p "$file_cards_dir"
      local content_hash
      content_hash=$(cat "$section_file" "$abs_source" 2>/dev/null | sha256sum | awk '{print $1}' || python3 -c "
import hashlib
data  = open('$section_file','rb').read()
data += open('$abs_source','rb').read()
print(hashlib.sha256(data).hexdigest())
")
      local card_path="$file_cards_dir/${content_hash}.md"
      if [ -f "$card_path" ]; then
        echo "  $section_name: $source_file (cached)"
        # Populate response_file from cache so downstream artifacts are not skipped
        cp "$card_path" "$response_file" 2>/dev/null || true
        # Copy cached feedback if available
        local cached_feedback="$file_cards_dir/${content_hash}-feedback.json"
        if [ -f "$cached_feedback" ]; then
          cp "$cached_feedback" "$feedback_file" 2>/dev/null || true
        fi
        update_match "$section_file" "$source_file" "$response_file" || {
          log_phase_failure "deep-update" "${section_name}:${source_file}" "failed to update section file (cached)"
          phase_failed=1
        }
        echo "[DEEP] $section_name × $(basename "$source_file") (cached)"
        continue
      fi

      cat > "$prompt_file" << PROMPT
# Task: Analyze File Relevance for Section

Read this file in the context of the section's goals. Explain what parts of the file matter for this section and why. Note any concerns, dependencies, or open questions you discover.

## Files to Read
1. Section specification: \`$section_file\`
2. Source file: \`$abs_source\`
3. Codemap (for context): \`$CODEMAP_PATH\`

## Instructions

Read both files. Reason about the source file in context of the section. What specific parts are relevant to the section? Are there functions, classes, configurations, or patterns that the section will need to interact with? Are there risks or complications? Write your analysis naturally — focus on what someone implementing this section needs to know about this file.

## Feedback (IMPORTANT)

After your analysis, write a JSON feedback file to: \`$feedback_file\`

Format:
\`\`\`json
{
  "source_file": "$source_file",
  "relevant": true,
  "missing_files": ["path/to/file1.py", "path/to/file2.py"],
  "reason": "Brief explanation if not relevant, or why missing files matter"
}
\`\`\`

- \`source_file\`: The relative path to the file being analyzed (copy the
  value above exactly — this preserves traceability from feedback to file).
- \`relevant\`: Is this file actually relevant to the section? Set false if
  the file was incorrectly included (e.g., shares a name but different concern).
- \`missing_files\`: Files NOT in the section's list that SHOULD be. Only
  include files you discovered while reading this file (imports, callers,
  shared config, etc.) that the section will need. Use paths relative to
  the codespace root.
- \`out_of_scope\`: (optional) List of problems or concerns discovered that
  are OUTSIDE this section's scope. Each entry should describe what the
  problem is and which section or higher level should handle it.
- \`reason\`: Brief explanation.
PROMPT

      uv run --frozen agents --model glm --project "$CODESPACE" --file "$prompt_file" \
        > "$response_file" 2> "$stderr_file" || {
        log_phase_failure "deep-scan" "${section_name}:${source_file}" "deep analysis failed (see $stderr_file)"
        phase_failed=1
        continue
      }

      if ! grep -q '[^[:space:]]' "$response_file" 2>/dev/null; then
        log_phase_failure "deep-scan" "${section_name}:${source_file}" "agent produced empty output"
        phase_failed=1
        continue
      fi

      # Write file-card cache entries (response + feedback)
      cp "$response_file" "$card_path" 2>/dev/null || true
      if [ -f "$feedback_file" ]; then
        local cached_feedback="$file_cards_dir/${content_hash}-feedback.json"
        cp "$feedback_file" "$cached_feedback" 2>/dev/null || true
      fi

      update_match "$section_file" "$source_file" "$response_file" || {
        log_phase_failure "deep-update" "${section_name}:${source_file}" "failed to update section file"
        phase_failed=1
        continue
      }

      echo "[DEEP] $section_name × $(basename "$source_file")"

    done <<< "$scan_files"
  done <<< "$section_files"

  # Post-scan: collect feedback and produce report
  echo "--- Deep Scan: collecting feedback ---"
  local feedback_report="$ARTIFACTS_DIR/scan-feedback.md"
  echo "# Scan Feedback Report" > "$feedback_report"
  echo "" >> "$feedback_report"
  echo "Generated by deep scan. Review and apply if needed." >> "$feedback_report"
  echo "" >> "$feedback_report"

  local has_feedback=0
  while IFS= read -r section_file; do
    local sec_name
    sec_name=$(basename "$section_file" .md)
    local sec_log_dir="$SCAN_LOG_DIR/$sec_name"

    local irrelevant_files=""
    local missing_files=""
    local out_of_scope_items=""

    for fb_file in "$sec_log_dir"/deep-*-feedback.json; do
      [ -f "$fb_file" ] || continue
      # Parse feedback JSON (best-effort with grep/sed)
      local relevant
      relevant=$(python3 -c "
import json, sys
try:
    d = json.load(open('$fb_file'))
    print('true' if d.get('relevant', True) else 'false')
except: print('true')
" 2>/dev/null || echo "true")

      if [ "$relevant" = "false" ]; then
        local reason src_path
        eval "$(python3 -c "
import json
try:
    d = json.load(open('$fb_file'))
    print('reason=' + repr(d.get('reason','')))
    print('src_path=' + repr(d.get('source_file','')))
except:
    print(\"reason=''\")
    print(\"src_path=''\")
" 2>/dev/null || echo "reason=''; src_path=''")"
        # Use source_file from JSON for traceability (not parsed from filename)
        if [ -z "$src_path" ]; then
          src_path="${fb_file##*/deep-}"
          src_path="${src_path%%-feedback.json}"
        fi
        irrelevant_files="${irrelevant_files}\n- ${src_path}: ${reason}"
        has_feedback=1
      fi

      local new_missing
      new_missing=$(python3 -c "
import json
try:
    d = json.load(open('$fb_file'))
    for f in d.get('missing_files', []):
        if f.strip(): print(f.strip())
except: pass
" 2>/dev/null || true)

      if [ -n "$new_missing" ]; then
        while IFS= read -r mf; do
          missing_files="${missing_files}\n- ${mf}"
        done <<< "$new_missing"
        has_feedback=1
      fi

      # Collect out-of-scope findings
      local new_oos
      new_oos=$(python3 -c "
import json
try:
    d = json.load(open('$fb_file'))
    for item in d.get('out_of_scope', []):
        if isinstance(item, str) and item.strip(): print(item.strip())
except: pass
" 2>/dev/null || true)

      if [ -n "$new_oos" ]; then
        while IFS= read -r oos; do
          out_of_scope_items="${out_of_scope_items}\n- ${oos}"
        done <<< "$new_oos"
        has_feedback=1
      fi
    done

    if [ -n "$irrelevant_files" ] || [ -n "$missing_files" ] || [ -n "$out_of_scope_items" ]; then
      echo "## $sec_name" >> "$feedback_report"
      echo "" >> "$feedback_report"
      if [ -n "$irrelevant_files" ]; then
        echo "### Irrelevant files (consider removing)" >> "$feedback_report"
        echo -e "$irrelevant_files" >> "$feedback_report"
        echo "" >> "$feedback_report"
      fi
      if [ -n "$missing_files" ]; then
        echo "### Missing files (consider adding)" >> "$feedback_report"
        echo -e "$missing_files" >> "$feedback_report"
        echo "" >> "$feedback_report"
      fi
      if [ -n "$out_of_scope_items" ]; then
        echo "### Open problems (out of scope for this section)" >> "$feedback_report"
        echo -e "$out_of_scope_items" >> "$feedback_report"
        echo "" >> "$feedback_report"
      fi
    fi
  done <<< "$section_files"

  if [ "$has_feedback" -ne 0 ]; then
    echo "[FEEDBACK] Scan feedback written to: $feedback_report"
  else
    echo "## No feedback" >> "$feedback_report"
    echo "All files confirmed relevant. No missing files detected." >> "$feedback_report"
  fi

  if [ "$phase_failed" -ne 0 ]; then
    echo "=== Deep Scan Complete (with failures) ==="
    return 1
  fi
  echo "=== Deep Scan Complete ==="
  return 0
}

validate_preflight || exit 1
case "$cmd" in
  quick)
    run_quick_scan || exit 1
    ;;
  deep)
    run_deep_scan || exit 1
    ;;
  both)
    run_quick_scan || exit 1
    run_deep_scan || exit 1
    ;;
  *)
    echo "Usage: scan.sh <quick|deep|both> <planspace> <codespace>" >&2
    exit 1
    ;;
esac
