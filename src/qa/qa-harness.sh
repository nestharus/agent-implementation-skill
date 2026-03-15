#!/usr/bin/env bash
# QA Harness — runs the workflow pipeline with live monitoring and post-run scoring.
#
# Usage:
#   qa-harness.sh <codespace> <spec-path> <answer-key-dir> [slug]
#
# Example:
#   qa-harness.sh ~/projects/qa1 ~/projects/qa1/project-spec.md \
#     ~/work/tmp/execution-philosophy/qa-eval/ground-truth pulseplan
#
# What it does:
#   1. Creates a fresh planspace at ~/.claude/workspaces/<slug>/
#   2. Launches the pipeline runner as a direct subprocess
#   3. Launches a QA monitor that polls run.db for problems
#   4. Waits for the workflow to complete (or fail/timeout)
#   5. Runs logex to extract the unified timeline
#   6. Runs score.sh against the produced codespace
#   7. Writes a QA report to the answer-key-dir/runs/

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────
CODESPACE="${1:?Usage: qa-harness.sh <codespace> <spec-path> <answer-key-dir> [slug]}"
SPEC_PATH="${2:?Missing spec path}"
ANSWER_KEY="${3:?Missing answer key directory}"
SLUG="${4:-$(basename "$CODESPACE")}"

CODESPACE="$(realpath "$CODESPACE")"
SPEC_PATH="$(realpath "$SPEC_PATH")"
ANSWER_KEY="$(realpath "$ANSWER_KEY")"

# Resolve WORKFLOW_HOME from this script's location (follow symlinks)
SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
WORKFLOW_HOME="$(dirname "$SCRIPT_DIR")"
export WORKFLOW_HOME

# Slug contract: the harness passes --slug to the runner, which forces the
# planspace path to ~/.claude/workspaces/$SLUG.  run-metadata.json written by
# the runner is the machine-readable contract confirming the slug was honored.
DB_SH="$WORKFLOW_HOME/scripts/db.sh"
PLANSPACE="$HOME/.claude/workspaces/$SLUG"
DB_PATH="$PLANSPACE/run.db"

# logex location — LOGEX_REPO is the agent-implementation-skill repo root
# (one level up from src/, which is WORKFLOW_HOME)
LOGEX_REPO="$(dirname "$WORKFLOW_HOME")"
CLAUDE_PROJECT_HASH="$(echo "$CODESPACE" | sed 's|/|-|g; s|^-||')"
CLAUDE_HOME="$HOME/.claude/projects/-$CLAUDE_PROJECT_HASH"

# Capture start time for logex --after filter (exclude stale events)
QA_START_ISO="$(date -Iseconds)"

# Run output directory
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$ANSWER_KEY/runs/$RUN_ID"

# No timeout — the run completes when the workflow agent exits or the budget is exhausted.
TIMEOUT_SECONDS=0

echo "═══════════════════════════════════════════════════"
echo "  QA Harness — $SLUG"
echo "═══════════════════════════════════════════════════"
echo "  Codespace:   $CODESPACE"
echo "  Spec:        $SPEC_PATH"
echo "  Answer key:  $ANSWER_KEY"
echo "  Planspace:   $PLANSPACE"
echo "  Run output:  $RUN_DIR"
echo "  Timeout:     $([ "$TIMEOUT_SECONDS" -gt 0 ] && echo "${TIMEOUT_SECONDS}s" || echo "none (budget-limited)")"
echo "═══════════════════════════════════════════════════"

# ── Preflight ─────────────────────────────────────────────────────────

if [ -d "$PLANSPACE" ]; then
  echo "ERROR: Planspace already exists: $PLANSPACE"
  echo "  Delete it first: rm -rf $PLANSPACE"
  exit 1
fi

if [ ! -f "$SPEC_PATH" ]; then
  echo "ERROR: Spec not found: $SPEC_PATH"
  exit 1
fi

if [ ! -f "$ANSWER_KEY/score.sh" ]; then
  echo "ERROR: score.sh not found in $ANSWER_KEY"
  exit 1
fi

mkdir -p "$RUN_DIR"

# ── Phase 1: Launch workflow agent ────────────────────────────────────

echo ""
echo "[Phase 1] Starting workflow agent..."
echo "  Started at: $(date -Iseconds)"

WORKFLOW_LOG="$RUN_DIR/workflow.log"
WORKFLOW_PID_FILE="$RUN_DIR/workflow.pid"

# Launch the pipeline runner directly as a subprocess
(
  cd "$WORKFLOW_HOME" && \
  PYTHONPATH="$WORKFLOW_HOME" python3 -m pipeline \
    "$PLANSPACE" "$CODESPACE" \
    --spec "$SPEC_PATH" --slug "$SLUG" --qa-mode \
    > "$WORKFLOW_LOG" 2>&1
  echo $? > "$RUN_DIR/workflow.exit"
) &
WORKFLOW_PID=$!
echo "$WORKFLOW_PID" > "$WORKFLOW_PID_FILE"
echo "  Workflow PID: $WORKFLOW_PID"

# Give the workflow a moment to create the planspace
echo "  Waiting for planspace creation..."
WAIT_COUNT=0
while [ ! -f "$DB_PATH" ] && [ $WAIT_COUNT -lt 120 ]; do
  sleep 5
  WAIT_COUNT=$((WAIT_COUNT + 5))
  # Check if workflow already died
  if ! kill -0 "$WORKFLOW_PID" 2>/dev/null; then
    echo "  ERROR: Workflow agent exited before creating planspace"
    echo "  Check $WORKFLOW_LOG for details"
    cat "$WORKFLOW_LOG" | tail -20
    exit 1
  fi
done

if [ ! -f "$DB_PATH" ]; then
  # Fallback: the runner may have created the planspace under a different name.
  # Search run-metadata.json files for one that matches our slug or codespace.
  echo "  Expected planspace not found. Searching for matching run-metadata.json..."
  FOUND_PLANSPACE=""
  for meta in "$HOME"/.claude/workspaces/*/artifacts/run-metadata.json; do
    [ -f "$meta" ] || continue
    # Check if the metadata contains our slug or codespace path
    if python3 -c "
import json, sys
m = json.load(open('$meta'))
if m.get('slug') == '$SLUG' or m.get('codespace') == '$CODESPACE':
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
      FOUND_PLANSPACE="$(dirname "$(dirname "$meta")")"
      break
    fi
  done

  if [ -n "$FOUND_PLANSPACE" ]; then
    echo "  Found matching planspace via run-metadata.json: $FOUND_PLANSPACE"
    PLANSPACE="$FOUND_PLANSPACE"
    DB_PATH="$PLANSPACE/run.db"
    # Re-check that run.db actually exists at the discovered path
    if [ ! -f "$DB_PATH" ]; then
      echo "  ERROR: run-metadata.json matched but run.db missing at $DB_PATH"
      kill "$WORKFLOW_PID" 2>/dev/null || true
      exit 1
    fi
  else
    echo "  ERROR: Planspace not created after 120s (no matching run-metadata.json found)"
    kill "$WORKFLOW_PID" 2>/dev/null || true
    exit 1
  fi
fi

echo "  Planspace created at: $(date -Iseconds)"

# ── Phase 2: Launch QA monitor ────────────────────────────────────────

echo ""
echo "[Phase 2] Starting QA monitor..."

QA_LOG="$RUN_DIR/qa-monitor.log"
QA_PID_FILE="$RUN_DIR/qa-monitor.pid"
QA_AGENT_NAME="qa-monitor"

# Register the QA monitor in the coordination DB
bash "$DB_SH" register "$DB_PATH" "$QA_AGENT_NAME" $$ 2>/dev/null || true

# The QA monitor runs as a loop checking run.db
(
  LAST_EVENT_ID=0
  LAST_SIGNAL_ID=0
  CYCLE=0
  START_TIME=$(date +%s)

  # Initialize QA report
  mkdir -p "$PLANSPACE/artifacts"
  cat > "$PLANSPACE/artifacts/qa-report.md" << QEOF
# QA Monitor Report

- **Start time**: $(date -Iseconds)
- **Planspace**: $PLANSPACE
- **Codespace**: $CODESPACE
- **Monitor**: $QA_AGENT_NAME
- **Run ID**: $RUN_ID

## Findings

| Time | Severity | Rule | Finding |
|------|----------|------|---------|
QEOF

  log_finding() {
    local severity="$1" rule="$2" detail="$3"
    local ts
    ts="$(date +%H:%M:%S)"
    echo "| $ts | $severity | $rule | $detail |" >> "$PLANSPACE/artifacts/qa-report.md"
    bash "$DB_SH" log "$DB_PATH" qa-finding "$severity:$rule" "$detail" --agent "$QA_AGENT_NAME" 2>/dev/null || true
    echo "[$severity] $rule: $detail"
  }

  while true; do
    CYCLE=$((CYCLE + 1))

    # Check if workflow is still running
    if ! kill -0 "$WORKFLOW_PID" 2>/dev/null; then
      log_finding "INFO" "LIFECYCLE" "Workflow agent exited"
      break
    fi

    # ── Event cursor ──
    NEW_EVENTS=$(bash "$DB_SH" tail "$DB_PATH" summary --since "$LAST_EVENT_ID" 2>/dev/null || true)
    if [ -n "$NEW_EVENTS" ]; then
      LAST_EVENT_ID=$(echo "$NEW_EVENTS" | tail -1 | cut -d'|' -f1)
      while IFS='|' read -r evt_id evt_ts evt_kind evt_tag evt_body evt_agent; do
        [ -z "$evt_id" ] && continue

        # D1: Error strings in event bodies
        if echo "$evt_body" | grep -qiE 'traceback|FileNotFoundError|Permission denied|TIMEOUT:|\[FAIL\]'; then
          log_finding "WARN" "D1" "Error in event $evt_id: $(echo "$evt_body" | head -c 200)"
        fi

        # A4: Explicit loop signals
        if echo "$evt_body" | grep -qi 'LOOP_DETECTED'; then
          log_finding "PAUSE" "A4" "Loop detected in event $evt_id: $evt_body"
        fi

        # B7: Feature coverage language
        if echo "$evt_body" | grep -qiE 'all features implemented|feature checklist|coverage percentage|feature count|missing features|feature complete'; then
          log_finding "PAUSE" "B7" "Invalid frame in event $evt_id: $(echo "$evt_body" | head -c 200)"
        fi
      done <<< "$NEW_EVENTS"
    fi

    # ── Signal cursor ──
    NEW_SIGNALS=$(bash "$DB_SH" tail "$DB_PATH" signal --since "$LAST_SIGNAL_ID" 2>/dev/null || true)
    if [ -n "$NEW_SIGNALS" ]; then
      LAST_SIGNAL_ID=$(echo "$NEW_SIGNALS" | tail -1 | cut -d'|' -f1)
      while IFS='|' read -r evt_id evt_ts evt_kind evt_tag evt_body evt_agent; do
        [ -z "$evt_id" ] && continue
        if echo "$evt_body" | grep -qi 'LOOP_DETECTED'; then
          log_finding "PAUSE" "A4" "Loop signal $evt_id: $evt_body"
        fi
        if echo "$evt_body" | grep -qi 'STALLED'; then
          log_finding "WARN" "A5" "Stall signal $evt_id: $evt_body"
        fi
      done <<< "$NEW_SIGNALS"
    fi

    # ── Aggregate checks (every 4th cycle = ~60s) ──
    if [ $((CYCLE % 4)) -eq 0 ]; then
      # A5: Silence detection — last summary event timestamp
      LAST_TS=$(bash "$DB_SH" query "$DB_PATH" summary --limit 1 2>/dev/null | cut -d'|' -f2 || true)
      if [ -n "$LAST_TS" ]; then
        LAST_EPOCH=$(python3 -c "
from datetime import datetime, timezone
try:
    t = '$LAST_TS'.strip()
    if t.endswith('Z'): t = t[:-1] + '+00:00'
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    print(int(dt.timestamp()))
except: print(0)
" 2>/dev/null || echo 0)
        NOW_EPOCH=$(date +%s)
        SILENCE=$((NOW_EPOCH - LAST_EPOCH))
        if [ "$LAST_EPOCH" -gt 0 ] && [ "$SILENCE" -gt 600 ]; then
          log_finding "WARN" "A5" "No summary events for ${SILENCE}s"
        fi
      fi

      # A1: Alignment attempt overflow per section (>= 3 PROBLEMS)
      ALIGN_COUNTS=$(bash "$DB_SH" tail "$DB_PATH" summary 2>/dev/null \
        | python3 -c "
import sys, re
from collections import Counter
counts = Counter()
for line in sys.stdin:
    parts = line.strip().split('|')
    if len(parts) >= 5:
        tag, body = parts[3], parts[4]
        m = re.search(r'(proposal|impl)-align:(\d{2})', tag)
        if m and 'PROBLEMS' in body:
            counts[m.group(2)] += 1
for sec, cnt in counts.items():
    if cnt >= 3:
        print(f'{sec}:{cnt}')
" 2>/dev/null || true)
      if [ -n "$ALIGN_COUNTS" ]; then
        while IFS= read -r line; do
          sec="${line%%:*}"
          cnt="${line##*:}"
          log_finding "PAUSE" "A1" "Section $sec has $cnt alignment PROBLEMS attempts"
        done <<< "$ALIGN_COUNTS"
      fi

      # C1: Sub-agent dispatch count per section (> 15)
      DISPATCH_COUNTS=$(bash "$DB_SH" tail "$DB_PATH" summary 2>/dev/null \
        | python3 -c "
import sys, re
from collections import Counter
counts = Counter()
for line in sys.stdin:
    parts = line.strip().split('|')
    if len(parts) >= 4:
        tag = parts[3]
        m = re.search(r'dispatch:(\d{2})', tag)
        if m:
            counts[m.group(1)] += 1
for sec, cnt in counts.items():
    if cnt > 15:
        print(f'{sec}:{cnt}')
" 2>/dev/null || true)
      if [ -n "$DISPATCH_COUNTS" ]; then
        while IFS= read -r line; do
          sec="${line%%:*}"
          cnt="${line##*:}"
          log_finding "WARN" "C1" "Section $sec has $cnt dispatches (threshold: 15)"
        done <<< "$DISPATCH_COUNTS"
      fi
    fi

    # ── Heartbeat (every 20th cycle = ~5 min) ──
    if [ $((CYCLE % 20)) -eq 0 ]; then
      EVT_COUNT=$(bash "$DB_SH" tail "$DB_PATH" 2>/dev/null | wc -l) || EVT_COUNT=0
      AGENT_COUNT=$(bash "$DB_SH" agents "$DB_PATH" 2>/dev/null | wc -l) || AGENT_COUNT=0
      FINDING_COUNT=$(bash "$DB_SH" tail "$DB_PATH" qa-finding 2>/dev/null | wc -l) || FINDING_COUNT=0
      ELAPSED=$(( $(date +%s) - START_TIME ))
      log_finding "HEARTBEAT" "-" "events:$EVT_COUNT agents:$AGENT_COUNT findings:$FINDING_COUNT elapsed:${ELAPSED}s"
    fi

    # ── Timeout check (disabled when TIMEOUT_SECONDS=0) ──
    if [ "$TIMEOUT_SECONDS" -gt 0 ]; then
      ELAPSED=$(( $(date +%s) - START_TIME ))
      if [ "$ELAPSED" -gt "$TIMEOUT_SECONDS" ]; then
        log_finding "ABORT" "TIMEOUT" "QA harness timeout after ${ELAPSED}s"
        kill "$WORKFLOW_PID" 2>/dev/null || true
        break
      fi
    fi

    sleep 15
  done

  # Write summary to report
  cat >> "$PLANSPACE/artifacts/qa-report.md" << SEOF

## Summary

- **End time**: $(date -Iseconds)
- **Duration**: ${ELAPSED:-0}s
- **Total events**: $(bash "$DB_SH" tail "$DB_PATH" 2>/dev/null | wc -l)
- **Total findings**: $(bash "$DB_SH" tail "$DB_PATH" qa-finding 2>/dev/null | wc -l)
SEOF

) > "$QA_LOG" 2>&1 &
QA_PID=$!
echo "$QA_PID" > "$QA_PID_FILE"
echo "  QA Monitor PID: $QA_PID"

# ── Phase 3: Wait for workflow completion ─────────────────────────────

echo ""
echo "[Phase 3] Waiting for workflow to complete..."
echo "  (timeout: ${TIMEOUT_SECONDS}s)"

wait "$WORKFLOW_PID" 2>/dev/null || true
WORKFLOW_EXIT=$(cat "$RUN_DIR/workflow.exit" 2>/dev/null || echo "unknown")
echo "  Workflow exited with: $WORKFLOW_EXIT"

# Give QA monitor a moment to process final events, then stop it
sleep 5
kill "$QA_PID" 2>/dev/null || true
wait "$QA_PID" 2>/dev/null || true
echo "  QA monitor stopped"

# ── Phase 4: Extract timeline with logex ──────────────────────────────

echo ""
echo "[Phase 4] Extracting timeline with logex..."

if [ -d "$CLAUDE_HOME" ]; then
  CLAUDE_FLAG="--claude-home $CLAUDE_HOME"
else
  CLAUDE_FLAG=""
fi

# Only extract from run_db, artifacts, and claude sessions — skip codex/opencode/gemini
# which contain stale data from unrelated runs. Use --after to exclude pre-run events.
LOGEX_COMMON_ARGS=(
  "$PLANSPACE"
  --source run_db,artifact,claude
  --after "$QA_START_ISO"
)
if [ -d "$CLAUDE_HOME" ]; then
  LOGEX_COMMON_ARGS+=(--claude-home "$CLAUDE_HOME")
fi

(
  cd "$LOGEX_REPO"
  PYTHONPATH=src/scripts uv run python -m log_extract \
    "${LOGEX_COMMON_ARGS[@]}" \
    --format jsonl \
    > "$RUN_DIR/timeline.jsonl" 2>"$RUN_DIR/logex-errors.log"
) || echo "  WARNING: logex failed (check $RUN_DIR/logex-errors.log)"

if [ -f "$RUN_DIR/timeline.jsonl" ]; then
  LINE_COUNT=$(wc -l < "$RUN_DIR/timeline.jsonl")
  echo "  Timeline: $LINE_COUNT events"
else
  echo "  Timeline: (not generated)"
fi

# Also generate text format for human reading
(
  cd "$LOGEX_REPO"
  PYTHONPATH=src/scripts uv run python -m log_extract \
    "${LOGEX_COMMON_ARGS[@]}" \
    --format text --no-color \
    > "$RUN_DIR/timeline.txt" 2>/dev/null
) || true

# ── Phase 5: Score the codespace ──────────────────────────────────────

echo ""
echo "[Phase 5] Scoring codespace..."

bash "$ANSWER_KEY/score.sh" "$CODESPACE" > "$RUN_DIR/score.txt" 2>&1 || true
cat "$RUN_DIR/score.txt"

# ── Phase 6: Copy artifacts ───────────────────────────────────────────

echo ""
echo "[Phase 6] Collecting artifacts..."

# Copy QA report if it exists
if [ -f "$PLANSPACE/artifacts/qa-report.md" ]; then
  cp "$PLANSPACE/artifacts/qa-report.md" "$RUN_DIR/qa-report.md"
  echo "  Copied qa-report.md"
fi

# Copy workflow log
echo "  Workflow log: $RUN_DIR/workflow.log ($(wc -l < "$WORKFLOW_LOG") lines)"
echo "  QA monitor log: $RUN_DIR/qa-monitor.log"

# ── Summary ───────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════"
echo "  QA Run Complete — $RUN_ID"
echo "═══════════════════════════════════════════════════"
echo "  Run directory:  $RUN_DIR"
echo "  Score:          $(grep -c '^PASS' "$RUN_DIR/score.txt" 2>/dev/null || echo 0)/$(grep -cE '^(PASS|FAIL)' "$RUN_DIR/score.txt" 2>/dev/null || echo 0)"
echo "  Timeline:       $RUN_DIR/timeline.txt"
echo "  QA findings:    $RUN_DIR/qa-report.md"
echo "  Workflow log:   $RUN_DIR/workflow.log"
echo "═══════════════════════════════════════════════════"
