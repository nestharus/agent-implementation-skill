#!/usr/bin/env bash
# Workflow schedule driver. Manages [wait]/[run]/[done]/[fail]/[skip] markers.
#
# Schedule line format:
#   [status] N. step-name | model-name -- description (skill-section-ref)
#   N may be integer (3) or decimal (3.5)
#
# Usage:
#   workflow.sh next|done|fail|retry|skip|status <workspace-dir>
#   workflow.sh parse <workspace-dir> "<step-line>"

# =============================================================================
# TODO [sqlite-migration]: Consider migrating schedule to events table (Tier 2)
#
# WHAT: This script mutates schedule.md in-place with sed (status markers).
# It could migrate to events table (kind='schedule', tag=step_name) where
# each status change is an INSERT, preserving the full schedule history.
#
# WHY: Currently, schedule.md shows only the current state of each step.
# After a run, there's no record of retries, how long steps took, or
# which steps were skipped and then later re-run. With DB events, every
# next/done/fail/retry/skip becomes a timestamped event.
#
# DEFERRAL: This is Tier 2 — independent from the core mailbox→db.sh
# migration. The schedule is a separate concern with its own consumers
# (state-detector.md) and can migrate after Tier 1.
# parse subcommand stays as-is (parses text format, not a storage concern).
#
# See: /tmp/pipeline-audit/exploration/event-streams-design-direction.md (D7)
# =============================================================================

set -euo pipefail

cmd="${1:?Usage: workflow.sh <command> <workspace-dir>}"
workspace="${2:?Missing workspace directory}"
schedule="$workspace/schedule.md"

# parse doesn't need the schedule file
if [ "$cmd" = "parse" ]; then
  raw="${3:?Missing step line to parse}"
  line="${raw#*:}"
  # Use Python for portable parsing (no grep -oP / PCRE dependency)
  eval "$(python3 -c "
import re, sys
line = sys.argv[1]
m = re.match(r'\[(\w+)\]\s+(\d+(?:\.\d+)?)\.\s+(\S+)\s*\|\s*(.+?)\s*--\s*(.*?)(?:\s*\(([^)]*)\))?\s*$', line)
if m:
    print(f'step_status=[{m.group(1)}]')
    print(f'step_num={m.group(2)}')
    print(f'step_name={m.group(3)}')
    print(f'step_model={m.group(4).strip()}')
    print(f'step_desc={m.group(5).strip()}')
    print(f'step_ref={m.group(6) or \"\"}')
else:
    print('step_status=')
    print('step_num=')
    print('step_name=')
    print('step_model=')
    print('step_desc=')
    print('step_ref=')
" "$line")"
  echo "status=$step_status"
  echo "num=$step_num"
  echo "name=$step_name"
  echo "model=$step_model"
  echo "desc=$step_desc"
  echo "ref=$step_ref"
  exit 0
fi

[ -f "$schedule" ] || { echo "ERROR: $schedule not found"; exit 1; }

case "$cmd" in
  next)
    running=$(grep -n '^\[run\]' "$schedule" | head -1 || true)
    if [ -n "$running" ]; then
      echo "$running"
      exit 0
    fi
    wait_line=$(grep -n '^\[wait\]' "$schedule" | head -1 || true)
    if [ -n "$wait_line" ]; then
      line_num="${wait_line%%:*}"
      sed -i "${line_num}s/^\[wait\]/[run]/" "$schedule"
      grep -n '^\[run\]' "$schedule" | head -1
      exit 0
    fi
    echo "COMPLETE"
    ;;
  done)
    running=$(grep -n '^\[run\]' "$schedule" | head -1 || true)
    if [ -z "$running" ]; then
      echo "ERROR: no [run] step to mark done"
      exit 1
    fi
    line_num="${running%%:*}"
    sed -i "${line_num}s/^\[run\]/[done]/" "$schedule"
    echo "Marked done: ${running#*:}"
    ;;
  fail)
    running=$(grep -n '^\[run\]' "$schedule" | head -1 || true)
    if [ -z "$running" ]; then
      echo "ERROR: no [run] step to mark fail"
      exit 1
    fi
    line_num="${running%%:*}"
    sed -i "${line_num}s/^\[run\]/[fail]/" "$schedule"
    echo "Marked fail: ${running#*:}"
    ;;
  retry)
    fail_line=$(grep -n '^\[fail\]' "$schedule" | head -1 || true)
    if [ -z "$fail_line" ]; then
      echo "ERROR: no [fail] step to retry"
      exit 1
    fi
    line_num="${fail_line%%:*}"
    sed -i "${line_num}s/^\[fail\]/[wait]/" "$schedule"
    echo "Reset to wait: ${fail_line#*:}"
    ;;
  skip)
    running=$(grep -n '^\[run\]' "$schedule" | head -1 || true)
    if [ -z "$running" ]; then
      echo "ERROR: no [run] step to skip"
      exit 1
    fi
    line_num="${running%%:*}"
    sed -i "${line_num}s/^\[run\]/[skip]/" "$schedule"
    echo "Skipped: ${running#*:}"
    ;;
  status)
    total=$(grep -c '^\[' "$schedule" || true)
    done_count=$(grep -c '^\[done\]' "$schedule" || true)
    run_count=$(grep -c '^\[run\]' "$schedule" || true)
    fail_count=$(grep -c '^\[fail\]' "$schedule" || true)
    wait_count=$(grep -c '^\[wait\]' "$schedule" || true)
    skip_count=$(grep -c '^\[skip\]' "$schedule" || true)
    echo "Total: $total | Done: $done_count | Running: $run_count | Failed: $fail_count | Waiting: $wait_count | Skipped: $skip_count"
    ;;
  *)
    echo "Unknown command: $cmd"
    echo "Usage: workflow.sh next|done|fail|retry|skip|status|parse <workspace-dir>"
    exit 1
    ;;
esac
