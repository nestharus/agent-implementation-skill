#!/usr/bin/env bash
set -euo pipefail

# scan.sh — Stage 3 scan entrypoint and phase coordinator.
#
# Public CLI contract (unchanged):
#   scan.sh <quick|deep|both> <planspace> <codespace>
#
# quick:
# - reuse or generate structural scan
# - delegate codemap orchestration to scripts/codemap_build.py
# - delegate section exploration orchestration to scripts/section_explore.py
#
# deep:
# - analyze confirmed related files from each section's `## Related Files` block
# - dispatch deep analysis via `uv run --frozen agents --model glm`
# - emit canonical deep prompt output format without a blank line before bullets
# - validate and append deep analysis fields in-place:
#   - Affected areas
#   - Confidence
#   - Open questions
#
# both:
# - run quick, then deep
#
# Artifacts and section output contracts remain unchanged.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_HOME="${WORKFLOW_HOME:-$(dirname "$SCRIPT_DIR")}"

cmd="${1:?Usage: scan.sh <quick|deep|both> <planspace> <codespace>}"
PLANSPACE="${2:?Missing planspace path}"
CODESPACE="${3:?Missing codespace path}"

ARTIFACTS_DIR="$PLANSPACE/artifacts"
SECTIONS_DIR="$PLANSPACE/artifacts/sections"
STRUCTURAL_SCAN_PATH="$ARTIFACTS_DIR/structural-scan.md"
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

deep_already_annotated() {
  local section_file="$1"
  local source_file="$2"
  awk -v target="$source_file" '
    $0 == "### " target { in_block = 1; next }
    in_block && ($0 ~ /^### / || $0 ~ /^## /) { exit }
    in_block { print }
  ' "$section_file" | grep -q "Affected areas:" 2>/dev/null
}

deep_scan_file_type_hint() {
  local source_file="$1"
  case "$source_file" in
    *.py) echo "Affected areas should reference specific functions, classes, methods, or code regions." ;;
    *.md) echo "Affected areas should reference specific headings, sections, rules, or instruction blocks." ;;
    *.sh) echo "Affected areas should reference specific functions, sections, or command blocks." ;;
    *) echo "Affected areas should reference the most specific structural elements available." ;;
  esac
}

deep_scan_response_valid() {
  local response_file="$1"
  grep -q "^- Affected areas:" "$response_file" 2>/dev/null || return 1
  grep -q "^- Confidence:" "$response_file" 2>/dev/null || return 1
  grep -q "^- Open questions:" "$response_file" 2>/dev/null || return 1
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

run_structural_scan() {
  if [ -s "$STRUCTURAL_SCAN_PATH" ]; then
    echo "[STRUCTURAL] Reusing existing artifact: $STRUCTURAL_SCAN_PATH"
    return 0
  fi

  mkdir -p "$(dirname "$STRUCTURAL_SCAN_PATH")"
  local stderr_file="$SCAN_LOG_DIR/structural-scan.stderr.log"

  if ! uv run python "$SCRIPT_DIR/structural-scan.py" "$CODESPACE" "$STRUCTURAL_SCAN_PATH" 2> "$stderr_file"; then
    log_phase_failure "quick-structural-scan" "$(basename "$STRUCTURAL_SCAN_PATH")" "structural scan command failed (see $stderr_file)"
    return 1
  fi

  if ! grep -q '[^[:space:]]' "$STRUCTURAL_SCAN_PATH"; then
    log_phase_failure "quick-structural-scan" "$(basename "$STRUCTURAL_SCAN_PATH")" "structural scan output is empty"
    return 1
  fi

  echo "[STRUCTURAL] Wrote: $STRUCTURAL_SCAN_PATH"
}

run_quick_scan() {
  echo "=== Quick Scan: structural scan + codemap + per-section strategic exploration ==="

  if ! run_structural_scan; then
    return 1
  fi

  if ! uv run python "$SCRIPT_DIR/codemap_build.py" \
    "$PLANSPACE" "$CODESPACE" "$STRUCTURAL_SCAN_PATH" "$CODEMAP_PATH" "$SCAN_LOG_DIR"; then
    log_phase_failure "quick-codemap" "$(basename "$CODEMAP_PATH")" "codemap helper failed"
    return 1
  fi

  if ! uv run python "$SCRIPT_DIR/section_explore.py" \
    "$PLANSPACE" "$CODESPACE" "$CODEMAP_PATH" "$SECTIONS_DIR" "$SCAN_LOG_DIR" "$WORKFLOW_HOME"; then
    log_phase_failure "quick-explore" "$(basename "$SECTIONS_DIR")" "section exploration helper failed"
    return 1
  fi

  echo "=== Quick Scan Complete ==="
  return 0
}

run_deep_scan() {
  echo "=== Deep Scan: full content for confirmed related files ==="

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

      local file_type_hint
      file_type_hint=$(deep_scan_file_type_hint "$source_file")

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
# Task: Detailed File-Section Relevance Analysis

## Section
$section_content

## Source File: $source_file
$file_content

## Instructions
$file_type_hint
Write your analysis in this exact format:
- Affected areas: <specific functions, classes, or regions>
- Confidence: <high | medium | low>
- Open questions: <uncertainties, or "none">
PROMPT

      uv run --frozen agents --model glm --project "$CODESPACE" --file "$prompt_file" \
        > "$response_file" 2> "$stderr_file" || {
        log_phase_failure "deep-scan" "${section_name}:${source_file}" "deep analysis failed (see $stderr_file)"
        phase_failed=1
        continue
      }

      if ! deep_scan_response_valid "$response_file"; then
        log_phase_failure "deep-scan-validate" "${section_name}:${source_file}" "invalid deep analysis format (see $response_file)"
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
