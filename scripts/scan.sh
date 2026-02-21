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

run_section_exploration() {
  local section_files
  section_files=$(list_section_files)

  while IFS= read -r section_file; do
    local section_name
    section_name=$(basename "$section_file" .md)

    # Skip if section already has related files
    if grep -q "^## Related Files" "$section_file" 2>/dev/null; then
      echo "[EXPLORE] $section_name already has Related Files — skipping"
      continue
    fi

    local section_log_dir="$SCAN_LOG_DIR/$section_name"
    mkdir -p "$section_log_dir"
    local prompt_file="$section_log_dir/explore-prompt.md"
    local response_file="$section_log_dir/explore-response.md"
    local stderr_file="$section_log_dir/explore.stderr.log"

    local section_content
    section_content=$(cat "$section_file")

    local codemap_content
    codemap_content=$(cat "$CODEMAP_PATH")

    cat > "$prompt_file" << PROMPT
# Task: Identify Files Related to This Section

You have a codemap of the project and a section from the proposal. Your job is to figure out which files in the codebase are related to this section's goals.

## Codemap
$codemap_content

## Section
$section_content

## How to Work

Use the codemap as your starting point — it tells you where things are and how they relate. Then explore specific files or directories to confirm relevance. Use GLM agents for quick file reads.

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
# For each section's related files, dispatch an agent to reason about
# specific relevance details in context. No fixed output format — the
# agent writes what it discovers.

deep_already_annotated() {
  local section_file="$1"
  local source_file="$2"
  local block_content
  block_content=$(awk -v target="$source_file" '
    $0 == "### " target { in_block = 1; next }
    in_block && ($0 ~ /^### / || $0 ~ /^## /) { exit }
    in_block { print }
  ' "$section_file")

  # If block has more than 3 non-empty lines, it already has deep analysis
  local line_count
  line_count=$(echo "$block_content" | grep -c '[^[:space:]]' 2>/dev/null || echo 0)
  [ "$line_count" -gt 3 ]
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

rest = section[idx + len(marker):]
match = re.search(r"\n(?=###\s|##\s[^#])", rest)
insert_pos = idx + len(marker) + (match.start() if match else len(rest))

new_section = section[:insert_pos].rstrip() + "\n" + details + "\n" + section[insert_pos:]
section_path.write_text(new_section)
PY
}

run_deep_scan() {
  echo "=== Deep Scan: agent-driven analysis of confirmed related files ==="

  local phase_failed=0

  local section_files
  section_files=$(list_section_files)

  while IFS= read -r section_file; do
    local section_name
    section_name=$(basename "$section_file" .md)

    local section_log_dir="$SCAN_LOG_DIR/$section_name"
    mkdir -p "$section_log_dir"

    local related_files
    related_files=$(deep_scan_related_files "$section_file" || true)

    if [ -z "$related_files" ]; then
      continue
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

      local safe_name
      local path_token extension_token source_hash
      path_token=$(echo "$source_file" | tr '/.' '__' | tr -cd '[:alnum:]_-' | cut -c1-80)
      extension_token="${source_file##*.}"
      if [ "$extension_token" = "$source_file" ]; then
        extension_token="noext"
      fi
      source_hash=$(printf '%s' "$source_file" | sha1sum | awk '{print $1}' | cut -c1-10)
      safe_name="${path_token}.${extension_token}.${source_hash}"
      local prompt_file="$section_log_dir/deep-${safe_name}-prompt.md"
      local response_file="$section_log_dir/deep-${safe_name}-response.md"
      local stderr_file="$section_log_dir/deep-${safe_name}.stderr.log"

      local section_content
      section_content=$(cat "$section_file")

      local file_content
      file_content=$(cat "$abs_source")

      cat > "$prompt_file" << PROMPT
# Task: Analyze File Relevance for Section

Read this file in the context of the section's goals. Explain what parts of the file matter for this section and why. Note any concerns, dependencies, or open questions you discover.

## Section
$section_content

## Source File: $source_file
$file_content

## Instructions

Reason about this file. What specific parts are relevant to the section? Are there functions, classes, configurations, or patterns that the section will need to interact with? Are there risks or complications? Write your analysis naturally — focus on what someone implementing this section needs to know about this file.
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

      update_match "$section_file" "$source_file" "$response_file" || {
        log_phase_failure "deep-update" "${section_name}:${source_file}" "failed to update section file"
        phase_failed=1
        continue
      }

      echo "[DEEP] $section_name × $(basename "$source_file")"

    done <<< "$related_files"
  done <<< "$section_files"

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
