#!/usr/bin/env bash
set -euo pipefail

# scan.sh — File relevance scan (Stage 3)
#
# Shell-script driven: enumerates section files × source files, extracts
# summaries via tools, dispatches GLM for matching, appends results to
# section files. No Claude orchestrator agent needed.
#
# Usage:
#   scan.sh quick <planspace> <codespace>   # quick scan (summaries only)
#   scan.sh deep  <planspace> <codespace>   # deep scan (full content for hits)
#   scan.sh both  <planspace> <codespace>   # quick then deep
#
# Resume-safe: skips pairs already recorded in section files.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_HOME="${WORKFLOW_HOME:-$(dirname "$SCRIPT_DIR")}"
TOOLS="$WORKFLOW_HOME/tools"

cmd="${1:?Usage: scan.sh <quick|deep|both> <planspace> <codespace>}"
PLANSPACE="${2:?Missing planspace path}"
CODESPACE="${3:?Missing codespace path}"

SECTIONS_DIR="$PLANSPACE/artifacts/sections"
RESPONSE_DIR="$PLANSPACE/artifacts/scan-responses"
mkdir -p "$RESPONSE_DIR"

# --- Helper: get extraction tool for a file extension ---
get_tool() {
  local ext="$1"
  local tool="$TOOLS/extract-docstring-${ext}"
  if [ -f "$tool" ]; then
    echo "$tool"
    return 0
  fi

  echo "[TOOL] No extractor for .${ext} — dispatching Opus to create one..." >&2

  local tool_prompt="$RESPONSE_DIR/create-tool-${ext}-prompt.md"
  cat > "$tool_prompt" << EOF
# Task: Create Docstring Extraction Tool

Read the interface specification at: $TOOLS/README.md

Write a new extraction tool for .${ext} files at: $tool

The tool must:
1. Extract the module-level docstring/comment from .${ext} files
2. Support --batch and --stdin modes
3. Output "NO DOCSTRING" if no docstring found
4. Follow the exact interface from README.md
5. Be executable (include shebang line)

After writing the tool, run: chmod +x $tool
EOF

  uv run agents --model claude-opus --file "$tool_prompt" \
    > "$RESPONSE_DIR/create-tool-${ext}.log" 2>&1

  if [ -f "$tool" ]; then
    chmod +x "$tool"
    echo "$tool"
    return 0
  else
    echo "[ERROR] Failed to create tool for .${ext}" >&2
    return 1
  fi
}

# --- Helper: extract section summary ---
extract_section_summary() {
  local section_file="$1"
  python3 "$TOOLS/extract-summary-md" "$section_file" | tail -n +2
}

# --- Helper: extract file docstring/summary ---
extract_file_docstring() {
  local source_file="$1"
  local ext="${source_file##*.}"

  # .md files use extract-summary-md (returns YAML summary, not docstring)
  if [ "$ext" = "md" ]; then
    local summary
    summary=$(python3 "$TOOLS/extract-summary-md" "$source_file" 2>/dev/null | tail -n +2)
    if [ -z "$summary" ] || [ "$summary" = "NO SUMMARY" ]; then
      # Fallback: first heading + first paragraph
      head -20 "$source_file" | sed -n '/^#/{p;q;}; /^[^-#]/{p;q;}'
    else
      echo "$summary"
    fi
    return 0
  fi

  # All other files: use extension-keyed extraction tool
  local tool
  tool=$(get_tool "$ext") || return 1
  python3 "$tool" "$source_file" | tail -n +2
}

# --- Helper: check if file already scanned for this section ---
already_scanned() {
  local section_file="$1"
  local source_file="$2"
  grep -qF "### ${source_file}" "$section_file" 2>/dev/null
}

# --- Helper: append match to section file ---
append_match() {
  local section_file="$1"
  local source_file="$2"
  local relevance="$3"

  # Ensure Related Files header exists
  if ! grep -q "^## Related Files" "$section_file" 2>/dev/null; then
    printf "\n## Related Files\n" >> "$section_file"
  fi

  printf "\n### %s\n- Relevance: %s\n" "$source_file" "$relevance" >> "$section_file"
}

# --- Helper: update match with deep scan results ---
update_match() {
  local section_file="$1"
  local source_file="$2"
  local details_file="$3"

  # Read deep scan details and append after the existing Relevance line
  local details
  details=$(cat "$details_file")

  # Use python for reliable multi-line insertion after the match header
  python3 -c "
import sys
section = open('$section_file', 'r').read()
marker = '### $source_file'
idx = section.find(marker)
if idx == -1:
    sys.exit(0)
# Find the next ### or ## or end of file
rest = section[idx + len(marker):]
# Find end of this entry (next ### or ## at start of line, or EOF)
import re
m = re.search(r'\n(?=###\s|##\s[^#])', rest)
if m:
    insert_pos = idx + len(marker) + m.start()
else:
    insert_pos = len(section)
# Build replacement
new_content = section[:insert_pos].rstrip() + '\n' + '''$details''' + '\n' + section[insert_pos:]
open('$section_file', 'w').write(new_content)
"
}

# --- Enumerate source files (relative to codespace) ---
find_source_files() {
  (cd "$CODESPACE" && find . -type f \
    \( -name "*.py" -o -name "*.sh" -o -name "*.md" \) \
    -not -path "./.git/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/node_modules/*" \
    -not -name "*.pyc" \
    -not -name "LICENSE" \
    | sed 's|^\./||' \
    | sort)
}

# --- Quick scan: summaries only ---
do_quick_scan() {
  echo "=== Quick Scan: file docstrings × section summaries ==="

  local section_files
  section_files=$(find "$SECTIONS_DIR" -name "section-*.md" | sort)

  local source_files
  source_files=$(find_source_files)

  local total_sections total_files
  total_sections=$(echo "$section_files" | wc -l)
  total_files=$(echo "$source_files" | wc -l)
  echo "Sections: $total_sections, Files: $total_files, Pairs: $((total_sections * total_files))"

  local section_num=0
  while IFS= read -r section_file; do
    section_num=$((section_num + 1))
    local section_name
    section_name=$(basename "$section_file" .md)

    local section_summary
    section_summary=$(extract_section_summary "$section_file")
    if [ "$section_summary" = "NO SUMMARY" ]; then
      echo "[SKIP] $section_name — no summary (run Phase C first)"
      continue
    fi

    local file_num=0
    while IFS= read -r source_file; do
      file_num=$((file_num + 1))

      # Resume: skip if already scanned
      if already_scanned "$section_file" "$source_file"; then
        continue
      fi

      local abs_source="$CODESPACE/$source_file"

      local docstring
      docstring=$(extract_file_docstring "$abs_source") || continue
      if [ "$docstring" = "NO DOCSTRING" ]; then
        continue
      fi

      # Build GLM prompt — use safe filename (replace / with __)
      local safe_name
      safe_name=$(echo "$source_file" | tr '/' '__' | sed 's/\.[^.]*$//')
      local prompt_file="$RESPONSE_DIR/quick-${section_name}-${safe_name}.md"
      local response_file="$RESPONSE_DIR/quick-${section_name}-${safe_name}-response.md"

      cat > "$prompt_file" << PROMPT
# Task: File-Section Relevance Check

Is this source file related to this proposal section?

## Section Summary
$section_summary

## File Docstring ($source_file)
$docstring

## Instructions
Reply with exactly one line:
RELATED: <brief reason why this file relates to the section>
or
NOT_RELATED

Nothing else.
PROMPT

      uv run agents --model glm --file "$prompt_file" \
        > "$response_file" 2>&1 || {
        echo "[FAIL] $section_name × $source_file"
        continue
      }

      # Parse response
      if grep -q "^RELATED:" "$response_file" 2>/dev/null; then
        local reason
        reason=$(grep "^RELATED:" "$response_file" | sed 's/^RELATED: *//')
        append_match "$section_file" "$source_file" "$reason"
        echo "[MATCH] $section_name × $source_file"
      fi

    done <<< "$source_files"

    echo "[DONE] $section_name ($section_num/$total_sections)"
  done <<< "$section_files"

  echo "=== Quick Scan Complete ==="
}

# --- Deep scan: full content for hits ---
do_deep_scan() {
  echo "=== Deep Scan: full content for related files ==="

  local section_files
  section_files=$(find "$SECTIONS_DIR" -name "section-*.md" | sort)

  while IFS= read -r section_file; do
    local section_name
    section_name=$(basename "$section_file" .md)

    # Find all related files in this section
    local related_files
    related_files=$(grep "^### " "$section_file" 2>/dev/null | sed 's/^### //' || true)

    if [ -z "$related_files" ]; then
      continue
    fi

    while IFS= read -r source_file; do
      [ -z "$source_file" ] && continue

      # Resume: skip if deep scan already done (has "Affected areas" line)
      if grep -A5 "### ${source_file}" "$section_file" | grep -q "Affected areas:" 2>/dev/null; then
        continue
      fi

      local abs_source="$CODESPACE/$source_file"
      local safe_name
      safe_name=$(echo "$source_file" | tr '/' '__' | sed 's/\.[^.]*$//')
      local prompt_file="$RESPONSE_DIR/deep-${section_name}-${safe_name}.md"
      local response_file="$RESPONSE_DIR/deep-${section_name}-${safe_name}-response.md"

      local section_content
      section_content=$(cat "$section_file")

      local file_content
      file_content=$(cat "$abs_source")

      cat > "$prompt_file" << PROMPT
# Task: Detailed File-Section Relevance Analysis

Analyze how this source file relates to this proposal section in detail.

## Section
$section_content

## Source File: $source_file
$file_content

## Instructions
Write your analysis in this exact format (nothing else):

- Affected areas: <specific functions, classes, or regions of the file>
- Confidence: <high | medium | low>
- Open questions: <what you're not sure about, or "none">
PROMPT

      uv run agents --model glm --file "$prompt_file" \
        > "$response_file" 2>&1 || {
        echo "[FAIL] deep: $section_name × $(basename "$source_file")"
        continue
      }

      update_match "$section_file" "$source_file" "$response_file"
      echo "[DEEP] $section_name × $(basename "$source_file")"

    done <<< "$related_files"
  done <<< "$section_files"

  echo "=== Deep Scan Complete ==="
}

# --- Main ---
case "$cmd" in
  quick) do_quick_scan ;;
  deep)  do_deep_scan ;;
  both)  do_quick_scan; do_deep_scan ;;
  *)     echo "Usage: scan.sh <quick|deep|both> <planspace> <codespace>" >&2; exit 1 ;;
esac
