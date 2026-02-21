#!/usr/bin/env bash
# Workflow schedule driver. Manages [wait]/[run]/[done]/[fail]/[skip] markers.
#
# Schedule line format:
#   [status] N. step-name | model-name -- description (skill-section-ref)
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
# (orchestrator.md, state-detector.md) and can migrate after Tier 1.
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
  step_status=$(echo "$line" | grep -oP '^\[\w+\]')
  step_num=$(echo "$line" | grep -oP '\d+\.' | head -1 | tr -d '.')
  step_name=$(echo "$line" | sed -E 's/^\[\w+\]\s+[0-9]+\.\s+//' | sed -E 's/\s*\|.*//')
  step_model=$(echo "$line" | grep -oP '\|\s*\K.+(?=\s+--)' | xargs)
  step_desc=$(echo "$line" | sed -E 's/.*--\s*//' | sed -E 's/\s*\(.*\)//')
  step_ref=$(echo "$line" | grep -oP '\(\K[^)]+' || true)
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
