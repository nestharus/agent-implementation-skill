#!/usr/bin/env bash
# shellcheck disable=SC2012  # ls is safe here — filenames are controlled numeric .msg sequences
# File-based mailbox system for agent coordination.
# Agents block on recv until a message arrives. Atomic send/claim via mv.
#
# Usage:
#   mailbox.sh send     <root> <target> [message...]   # send message (stdin if no args)
#   mailbox.sh recv     <root> <name>   [timeout]       # block until message (0=forever)
#   mailbox.sh check    <root> <name>                   # non-blocking message count
#   mailbox.sh drain    <root> <name>                   # read all pending, non-blocking
#   mailbox.sh register <root> <name>   [pid]           # register agent
#   mailbox.sh unregister <root> <name>                 # remove agent
#   mailbox.sh agents   <root>                          # list registered agents
#   mailbox.sh cleanup  <root> [name]                   # remove mailbox(es)

set -euo pipefail

cmd="${1:?Usage: mailbox.sh <command> <root> ...}"
root="${2:?Missing mailbox root directory}"
POLL_INTERVAL=0.5

_ensure_dirs() {
  local name="$1"
  mkdir -p "$root/mailboxes/$name" "$root/.registry"
}

_next_seq() {
  local dir="$root/mailboxes/$1"
  local last
  last=$(ls "$dir"/*.msg 2>/dev/null | sort -V | tail -1 || true)
  if [ -z "$last" ]; then
    echo "0001"
  else
    local base
    base=$(basename "$last" .msg)
    printf "%04d" $(( 10#$base + 1 ))
  fi
}

_update_status() {
  local name="$1" status="$2"
  local regfile="$root/.registry/$name"
  if [ -f "$regfile" ]; then
    local tmp="$regfile.tmp.$$"
    sed "s/^status=.*/status=$status/" "$regfile" > "$tmp"
    mv "$tmp" "$regfile"
  fi
}

case "$cmd" in
  send)
    target="${3:?Missing target mailbox name}"
    _ensure_dirs "$target"
    seq=$(_next_seq "$target")
    tmpfile="$root/mailboxes/$target/$seq.msg.tmp.$$"
    destfile="$root/mailboxes/$target/$seq.msg"
    shift 3
    if [ $# -gt 0 ]; then
      printf '%s\n' "$*" > "$tmpfile"
    else
      cat > "$tmpfile"
    fi
    mv "$tmpfile" "$destfile"
    echo "sent:$target:$seq"
    ;;

  recv)
    name="${3:?Missing mailbox name}"
    timeout="${4:-0}"
    _ensure_dirs "$name"
    _update_status "$name" "waiting"
    elapsed_ms=0
    timeout_ms=$((timeout * 1000))
    poll_ms=500   # matches POLL_INTERVAL=0.5
    while true; do
      oldest=$(ls "$root/mailboxes/$name"/*.msg 2>/dev/null | sort -V | head -1 || true)
      if [ -n "$oldest" ]; then
        claimed="$oldest.claimed.$$"
        if mv "$oldest" "$claimed" 2>/dev/null; then
          _update_status "$name" "running"
          cat "$claimed"
          rm -f "$claimed"
          exit 0
        fi
        continue
      fi
      if [ "$timeout" != "0" ] && [ "$elapsed_ms" -ge "$timeout_ms" ]; then
        _update_status "$name" "running"
        echo "TIMEOUT"
        exit 1
      fi
      sleep "$POLL_INTERVAL"
      elapsed_ms=$((elapsed_ms + poll_ms))
    done
    ;;

  check)
    name="${3:?Missing mailbox name}"
    _ensure_dirs "$name"
    count=$(ls "$root/mailboxes/$name"/*.msg 2>/dev/null | wc -l || true)
    echo "$count"
    ;;

  drain)
    name="${3:?Missing mailbox name}"
    _ensure_dirs "$name"
    for msg in $(ls "$root/mailboxes/$name"/*.msg 2>/dev/null | sort -V); do
      claimed="$msg.claimed.$$"
      if mv "$msg" "$claimed" 2>/dev/null; then
        cat "$claimed"
        echo "---"
        rm -f "$claimed"
      fi
    done
    ;;

  register)
    name="${3:?Missing agent name}"
    pid="${4:-$$}"
    _ensure_dirs "$name"
    regfile="$root/.registry/$name"
    cat > "$regfile" <<EOF
pid=$pid
status=running
registered=$(date -Iseconds)
EOF
    echo "registered:$name:$pid"
    ;;

  unregister)
    name="${3:?Missing agent name}"
    rm -f "$root/.registry/$name"
    echo "unregistered:$name"
    ;;

  agents)
    if [ ! -d "$root/.registry" ]; then
      echo "No agents registered"
      exit 0
    fi
    for regfile in "$root/.registry"/*; do
      [ -f "$regfile" ] || continue
      name=$(basename "$regfile")
      [[ "$name" == *.tmp.* ]] && continue
      pid=$(grep '^pid=' "$regfile" | cut -d= -f2)
      status=$(grep '^status=' "$regfile" | cut -d= -f2)
      registered=$(grep '^registered=' "$regfile" | cut -d= -f2)
      pending=$(ls "$root/mailboxes/$name"/*.msg 2>/dev/null | wc -l || true)
      echo "$name | pid=$pid | status=$status | pending=$pending | since=$registered"
    done
    ;;

  cleanup)
    name="${3:-}"
    if [ -n "$name" ]; then
      rm -rf "$root/mailboxes/$name"
      rm -f "$root/.registry/$name"
      echo "cleaned:$name"
    else
      rm -rf "$root/mailboxes" "$root/.registry"
      echo "cleaned:all"
    fi
    ;;

  *)
    echo "Unknown command: $cmd"
    echo "Usage: mailbox.sh send|recv|check|drain|register|unregister|agents|cleanup <root> ..."
    exit 1
    ;;
esac
