#!/usr/bin/env bash
# SQLite-backed coordination database for agent pipeline.
# Drop-in behavioral replacement for the file-based mailbox with full observability.
#
# Uses Python's built-in sqlite3 module for SQL execution (no sqlite3 CLI needed).
#
# Usage:
#   db.sh init       <db>                                    # create/initialize database
#   db.sh send       <db> <target> [--from <agent>] [msg...] # send message (stdin if no args)
#   db.sh recv       <db> <name>   [timeout]                 # block until message (0=forever)
#   db.sh check      <db> <name>                             # non-blocking pending count
#   db.sh drain      <db> <name>                             # read all pending, non-blocking
#   db.sh register   <db> <name>   [pid]                     # register agent
#   db.sh unregister <db> <name>                             # mark agent exited
#   db.sh agents     <db>                                    # list registered agents
#   db.sh cleanup    <db> [name]                             # mark agents cleaned up
#   db.sh log        <db> <kind> [tag] [body] [--agent <a>]  # record event
#   db.sh tail       <db> [kind] [--since <id>] [--limit <n>]# cursor-based event stream
#   db.sh query      <db> <kind> [--tag <t>] [--agent <a>] [--since <id>] [--limit <n>] # flexible filter

set -euo pipefail

POLL_INTERVAL=0.5

# ─── argument parsing ────────────────────────────────────────────────
cmd="${1:?Usage: db.sh <command> <db> ...}"
db="${2:?Missing database path}"
shift 2

# ─── SQL execution via Python ────────────────────────────────────────
# All SQL goes through this function. Uses Python's sqlite3 for portability.
# Usage: _sql "SELECT ..." [param1 param2 ...]
# Rows output as pipe-separated values, one row per line.
_sql() {
  local query="$1"
  shift
  local params=""
  for p in "$@"; do
    params="${params}$(printf '%s\x1f' "$p")"
  done
  python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
query = sys.argv[2]
raw_params = sys.argv[3] if len(sys.argv) > 3 else ''

params = tuple(raw_params.split('\x1f')[:-1]) if raw_params else ()

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
try:
    cur.execute(query, params)
    conn.commit()
    for row in cur.fetchall():
        print('|'.join(str(v) if v is not None else '' for v in row))
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
finally:
    conn.close()
" "$db" "$query" "$params"
}

# ─── schema ──────────────────────────────────────────────────────────
_init_schema() {
  python3 -c "
import sqlite3, sys, os

db_path = sys.argv[1]
os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
conn.executescript('''
CREATE TABLE IF NOT EXISTS id_seq (
  id INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY,
  ts         TEXT    NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%f\',\'now\')),
  sender     TEXT    DEFAULT \'\',
  target     TEXT    NOT NULL,
  body       TEXT    NOT NULL,
  claimed    INTEGER NOT NULL DEFAULT 0,
  claimed_by TEXT,
  claimed_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id    INTEGER PRIMARY KEY,
  ts    TEXT    NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%f\',\'now\')),
  kind  TEXT    NOT NULL,
  tag   TEXT    DEFAULT \'\',
  body  TEXT    DEFAULT \'\',
  agent TEXT    DEFAULT \'\'
);

CREATE TABLE IF NOT EXISTS agents (
  id     INTEGER PRIMARY KEY,
  ts     TEXT    NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%f\',\'now\')),
  name   TEXT    NOT NULL,
  pid    INTEGER,
  status TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_target_unclaimed
  ON messages(target) WHERE claimed = 0;
CREATE INDEX IF NOT EXISTS idx_messages_target_claimed_id
  ON messages(target, claimed, id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_kind_tag ON events(kind, tag);
CREATE INDEX IF NOT EXISTS idx_events_kind_id ON events(kind, id);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
''')
conn.close()
" "$db"
}

# ─── helpers ─────────────────────────────────────────────────────────

# Update agent status by appending a new row (append-only).
_update_status() {
  local name="$1" new_status="$2"
  python3 -c "
import sqlite3, sys

db_path, name, new_status = sys.argv[1], sys.argv[2], sys.argv[3]
conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('SELECT pid FROM agents WHERE name=? ORDER BY id DESC LIMIT 1', (name,))
row = cur.fetchone()
if row:
    cur.execute('INSERT INTO id_seq DEFAULT VALUES')
    nid = cur.lastrowid
    cur.execute('INSERT INTO agents(id, name, pid, status) VALUES(?, ?, ?, ?)',
                (nid, name, row[0], new_status))
    conn.commit()
conn.close()
" "$db" "$name" "$new_status"
}

# Parse optional flags from remaining args.
# Sets variables: FLAG_FROM, FLAG_AGENT, FLAG_SINCE, FLAG_LIMIT, FLAG_TAG, POSITIONAL
_parse_flags() {
  FLAG_FROM=""
  FLAG_AGENT=""
  FLAG_SINCE=""
  FLAG_LIMIT=""
  FLAG_TAG=""
  POSITIONAL=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --from)  FLAG_FROM="${2:?--from requires a value}"; shift 2 ;;
      --agent) FLAG_AGENT="${2:?--agent requires a value}"; shift 2 ;;
      --since) FLAG_SINCE="${2:?--since requires a value}"; shift 2 ;;
      --limit) FLAG_LIMIT="${2:?--limit requires a value}"; shift 2 ;;
      --tag)   FLAG_TAG="${2:?--tag requires a value}"; shift 2 ;;
      *)       POSITIONAL+=("$1"); shift ;;
    esac
  done
}

# ─── subcommands ─────────────────────────────────────────────────────
case "$cmd" in

  # ── init ──────────────────────────────────────────────────────────
  init)
    _init_schema
    echo "initialized:$db"
    ;;

  # ── send ──────────────────────────────────────────────────────────
  send)
    _parse_flags "$@"
    target="${POSITIONAL[0]:?Missing target mailbox name}"
    sender="$FLAG_FROM"

    # Collect body: remaining positional args or stdin
    if [ "${#POSITIONAL[@]}" -gt 1 ]; then
      body="${POSITIONAL[*]:1}"
    else
      body=$(cat)
    fi

    result=$(python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
sender = sys.argv[2]
target = sys.argv[3]
body = sys.argv[4]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('INSERT INTO id_seq DEFAULT VALUES')
nid = cur.lastrowid
cur.execute('INSERT INTO messages(id, sender, target, body) VALUES(?, ?, ?, ?)',
            (nid, sender, target, body))
conn.commit()
conn.close()
print(nid)
" "$db" "$sender" "$target" "$body")
    echo "sent:${target}:${result}"
    ;;

  # ── recv ──────────────────────────────────────────────────────────
  recv)
    name="${1:?Missing mailbox name}"
    timeout="${2:-0}"

    _update_status "$name" "waiting"
    elapsed_ms=0
    timeout_ms=$((timeout * 1000))
    poll_ms=500

    while true; do
      # Atomically claim the oldest unclaimed message for this target.
      result=$(python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
name = sys.argv[2]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

while True:
    cur.execute('BEGIN IMMEDIATE')
    cur.execute('''SELECT id, body FROM messages
                   WHERE target=? AND claimed=0
                   ORDER BY id ASC LIMIT 1''', (name,))
    row = cur.fetchone()
    if not row:
        conn.execute('COMMIT')
        conn.close()
        sys.exit(1)
    msg_id, body = row
    cur.execute('''UPDATE messages
                   SET claimed=1, claimed_by=?, claimed_at=strftime('%Y-%m-%dT%H:%M:%f','now')
                   WHERE id=? AND claimed=0''', (name, msg_id))
    if cur.rowcount == 0:
        conn.execute('COMMIT')
        continue  # another process claimed it, retry
    conn.execute('COMMIT')
    conn.close()
    print(body)
    sys.exit(0)
" "$db" "$name" 2>/dev/null) && {
        _update_status "$name" "running"
        printf '%s\n' "$result"
        exit 0
      }

      if [ "$timeout" != "0" ] && [ "$elapsed_ms" -ge "$timeout_ms" ]; then
        _update_status "$name" "running"
        echo "TIMEOUT"
        exit 1
      fi

      sleep "$POLL_INTERVAL"
      elapsed_ms=$((elapsed_ms + poll_ms))
    done
    ;;

  # ── check ─────────────────────────────────────────────────────────
  check)
    name="${1:?Missing mailbox name}"
    _sql "SELECT COUNT(*) FROM messages WHERE target=? AND claimed=0" "$name"
    ;;

  # ── drain ─────────────────────────────────────────────────────────
  drain)
    name="${1:?Missing mailbox name}"

    # Claim all unclaimed messages atomically and return them.
    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
name = sys.argv[2]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

cur.execute('BEGIN IMMEDIATE')
cur.execute('''SELECT id, body FROM messages
               WHERE target=? AND claimed=0
               ORDER BY id ASC''', (name,))
rows = cur.fetchall()

if rows:
    ids = [r[0] for r in rows]
    placeholders = ','.join('?' * len(ids))
    cur.execute(f'''UPDATE messages
                    SET claimed=1, claimed_by=?, claimed_at=strftime('%Y-%m-%dT%H:%M:%f','now')
                    WHERE id IN ({placeholders}) AND claimed=0''', [name] + ids)
conn.execute('COMMIT')

for i, (_, body) in enumerate(rows):
    print(body)
    if i < len(rows) - 1:
        print('---')

conn.close()
" "$db" "$name"
    ;;

  # ── register ──────────────────────────────────────────────────────
  register)
    name="${1:?Missing agent name}"
    pid="${2:-$PPID}"

    result=$(python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
name = sys.argv[2]
pid = int(sys.argv[3])

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('INSERT INTO id_seq DEFAULT VALUES')
nid = cur.lastrowid
cur.execute('INSERT INTO agents(id, name, pid, status) VALUES(?, ?, ?, ?)',
            (nid, name, pid, 'running'))
conn.commit()
conn.close()
print(nid)
" "$db" "$name" "$pid")
    echo "registered:${name}:${pid}"
    ;;

  # ── unregister ────────────────────────────────────────────────────
  unregister)
    name="${1:?Missing agent name}"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
name = sys.argv[2]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('INSERT INTO id_seq DEFAULT VALUES')
nid = cur.lastrowid
cur.execute('INSERT INTO agents(id, name, pid, status) VALUES(?, ?, NULL, ?)',
            (nid, name, 'exited'))
conn.commit()
conn.close()
" "$db" "$name"
    echo "unregistered:${name}"
    ;;

  # ── agents ────────────────────────────────────────────────────────
  agents)
    result=$(python3 -c "
import sqlite3, sys

db_path = sys.argv[1]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('''
  SELECT a.name, a.pid, a.status,
         (SELECT COUNT(*) FROM messages WHERE target = a.name AND claimed = 0) AS pending,
         a.ts
  FROM agents a
  INNER JOIN (
    SELECT name, MAX(id) AS max_id FROM agents GROUP BY name
  ) latest ON a.id = latest.max_id
  WHERE a.status != 'cleaned'
  ORDER BY a.name
''')
rows = cur.fetchall()
conn.close()

if not rows:
    print('NO_AGENTS')
else:
    for name, pid, status, pending, ts in rows:
        pid_str = str(pid) if pid is not None else ''
        print(f'{name} | pid={pid_str} | status={status} | pending={pending} | since={ts}')
" "$db")

    if [ "$result" = "NO_AGENTS" ]; then
      echo "No agents registered"
      exit 0
    fi
    echo "$result"
    ;;

  # ── cleanup ───────────────────────────────────────────────────────
  cleanup)
    name="${1:-}"
    if [ -n "$name" ]; then
      python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
name = sys.argv[2]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('INSERT INTO id_seq DEFAULT VALUES')
nid = cur.lastrowid
cur.execute('INSERT INTO agents(id, name, pid, status) VALUES(?, ?, NULL, ?)',
            (nid, name, 'cleaned'))
conn.commit()
conn.close()
" "$db" "$name"
      echo "cleaned:${name}"
    else
      python3 -c "
import sqlite3, sys

db_path = sys.argv[1]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

# Find agents whose latest status is not 'cleaned'
cur.execute('''
  SELECT a.name FROM agents a
  INNER JOIN (SELECT name, MAX(id) AS max_id FROM agents GROUP BY name) latest
    ON a.id = latest.max_id
  WHERE a.status != 'cleaned'
''')
names = [r[0] for r in cur.fetchall()]

for name in names:
    cur.execute('INSERT INTO id_seq DEFAULT VALUES')
    nid = cur.lastrowid
    cur.execute('INSERT INTO agents(id, name, pid, status) VALUES(?, ?, NULL, ?)',
                (nid, name, 'cleaned'))

conn.commit()
conn.close()
" "$db"
      echo "cleaned:all"
    fi
    ;;

  # ── log ───────────────────────────────────────────────────────────
  log)
    _parse_flags "$@"
    kind="${POSITIONAL[0]:?Missing event kind}"
    tag="${POSITIONAL[1]:-}"
    body="${POSITIONAL[2]:-}"
    agent="$FLAG_AGENT"

    result=$(python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
kind = sys.argv[2]
tag = sys.argv[3]
body = sys.argv[4]
agent = sys.argv[5]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('INSERT INTO id_seq DEFAULT VALUES')
nid = cur.lastrowid
cur.execute('INSERT INTO events(id, kind, tag, body, agent) VALUES(?, ?, ?, ?, ?)',
            (nid, kind, tag, body, agent))
conn.commit()
conn.close()
print(nid)
" "$db" "$kind" "$tag" "$body" "$agent")
    echo "logged:${result}:${kind}:${tag}"
    ;;

  # ── tail ──────────────────────────────────────────────────────────
  tail)
    _parse_flags "$@"
    kind="${POSITIONAL[0]:-}"
    since="$FLAG_SINCE"
    limit="$FLAG_LIMIT"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
kind = sys.argv[2]
since = sys.argv[3]
limit = sys.argv[4]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

clauses = []
params = []
if kind:
    clauses.append('kind = ?')
    params.append(kind)
if since:
    clauses.append('id > ?')
    params.append(int(since))

where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
limit_clause = f'LIMIT {int(limit)}' if limit else ''

cur.execute(f'SELECT id, ts, kind, tag, body, agent FROM events {where} ORDER BY id ASC {limit_clause}', params)
for row in cur.fetchall():
    print('|'.join(str(v) if v is not None else '' for v in row))

conn.close()
" "$db" "$kind" "$since" "$limit"
    ;;

  # ── query ─────────────────────────────────────────────────────────
  query)
    _parse_flags "$@"
    kind="${POSITIONAL[0]:?Missing event kind}"
    tag="$FLAG_TAG"
    agent="$FLAG_AGENT"
    since="$FLAG_SINCE"
    limit="$FLAG_LIMIT"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
kind = sys.argv[2]
tag = sys.argv[3]
agent = sys.argv[4]
since = sys.argv[5]
limit = sys.argv[6]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

clauses = ['kind = ?']
params = [kind]
if tag:
    clauses.append('tag = ?')
    params.append(tag)
if agent:
    clauses.append('agent = ?')
    params.append(agent)
if since:
    clauses.append('id > ?')
    params.append(int(since))

where = 'WHERE ' + ' AND '.join(clauses)
limit_clause = f'LIMIT {int(limit)}' if limit else ''

cur.execute(f'SELECT id, ts, kind, tag, body, agent FROM events {where} ORDER BY id DESC {limit_clause}', params)
for row in cur.fetchall():
    print('|'.join(str(v) if v is not None else '' for v in row))

conn.close()
" "$db" "$kind" "$tag" "$agent" "$since" "$limit"
    ;;

  # ── unknown ───────────────────────────────────────────────────────
  *)
    echo "Unknown command: $cmd" >&2
    echo "Usage: db.sh init|send|recv|check|drain|register|unregister|agents|cleanup|log|tail|query <db> ..." >&2
    exit 1
    ;;
esac
