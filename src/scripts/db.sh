#!/usr/bin/env bash
# SQLite-backed coordination database for agent pipeline.
# Drop-in behavioral replacement for the file-based mailbox with full observability.
#
# Uses Python's built-in sqlite3 module for SQL execution (no sqlite3 CLI needed).
#
# Usage:
#   db.sh init          <db>                                    # create/initialize database
#   db.sh send          <db> <target> [--from <agent>] [msg...] # send message (stdin if no args)
#   db.sh recv          <db> <name>   [timeout]                 # block until message (0=forever)
#   db.sh check         <db> <name>                             # non-blocking pending count
#   db.sh drain         <db> <name>                             # read all pending, non-blocking
#   db.sh register      <db> <name>   [pid]                     # register agent
#   db.sh unregister    <db> <name>                             # mark agent exited
#   db.sh agents        <db>                                    # list registered agents
#   db.sh cleanup       <db> [name]                             # mark agents cleaned up
#   db.sh log           <db> <kind> [tag] [body] [--agent <a>]  # record event
#   db.sh tail          <db> [kind] [--since <id>] [--limit <n>]# cursor-based event stream
#   db.sh query         <db> <kind> [--tag <t>] [--agent <a>] [--since <id>] [--limit <n>] # flexible filter
#   db.sh submit-task   <db> <submitted_by> <task_type> [opts]  # submit task, print ID
#   db.sh claim-task    <db> <dispatcher> <task_id>             # claim task for execution
#   db.sh complete-task <db> <task_id> [--output <path>]        # mark task complete
#   db.sh fail-task     <db> <task_id> [--error <msg>]          # mark task failed
#   db.sh cancel-task   <db> <task_id> [--error <reason>]       # cancel pending task
#   db.sh list-tasks    <db> [--status <s>] [--type <t>]        # list matching tasks
#   db.sh next-task     <db>                                    # next runnable task (pending, deps met)
#   db.sh gate-status   <db> <gate_id>                          # query gate + member state

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

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_by   TEXT    NOT NULL,
    task_type      TEXT    NOT NULL,
    problem_id     TEXT,
    concern_scope  TEXT,
    payload_path   TEXT,
    priority       TEXT    DEFAULT \'normal\',
    status         TEXT    DEFAULT \'pending\',
    claimed_by     TEXT,
    agent_file     TEXT,
    model          TEXT,
    output_path    TEXT,
    created_at     TEXT    DEFAULT (datetime(\'now\')),
    claimed_at     TEXT,
    completed_at   TEXT,
    error          TEXT,
    instance_id          TEXT,
    flow_id              TEXT,
    chain_id             TEXT,
    declared_by_task_id  INTEGER,
    trigger_gate_id      TEXT,
    flow_context_path    TEXT,
    continuation_path    TEXT,
    result_manifest_path TEXT,
    freshness_token      TEXT,
    updated_at           TEXT,
    dedupe_key           TEXT,
    status_reason        TEXT,
    superseded_by_task_id INTEGER,
    result_envelope_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type   ON tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_dedupe_active
  ON tasks(dedupe_key)
  WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'running', 'blocked', 'awaiting_input');
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);

CREATE TABLE IF NOT EXISTS task_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id),
    satisfied INTEGER NOT NULL DEFAULT 0,
    satisfied_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_scope TEXT NOT NULL,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    callback_task_type TEXT,
    callback_payload_path TEXT,
    verification_mode TEXT NOT NULL DEFAULT 'subscriber_verifies',
    status TEXT NOT NULL DEFAULT 'active',
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    notified_at TEXT,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    event_type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    claim_scope TEXT NOT NULL,
    claim_kind TEXT NOT NULL DEFAULT 'result',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, claim_scope)
);

CREATE TABLE IF NOT EXISTS user_input_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id),
    requested_by TEXT NOT NULL,
    requested_for_scope TEXT,
    question TEXT NOT NULL,
    response_schema_json TEXT,
    response_json TEXT,
    status TEXT NOT NULL DEFAULT 'awaiting_input',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS value_axes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_scope TEXT NOT NULL,
    axis_name TEXT NOT NULL,
    source_task_id INTEGER REFERENCES tasks(id),
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE(section_scope, axis_name)
);

CREATE INDEX IF NOT EXISTS idx_task_deps_task ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_task_deps_depends ON task_dependencies(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_task_subs_task ON task_subscriptions(task_id);
CREATE INDEX IF NOT EXISTS idx_task_subs_scope ON task_subscriptions(subscriber_scope);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_user_input_task ON user_input_requests(task_id);
CREATE INDEX IF NOT EXISTS idx_value_axes_scope_status ON value_axes(section_scope, status);

CREATE TRIGGER IF NOT EXISTS trg_tasks_default_updated_at
AFTER INSERT ON tasks
FOR EACH ROW
WHEN NEW.updated_at IS NULL
BEGIN
  UPDATE tasks
  SET updated_at = created_at
  WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS gates (
    gate_id                TEXT PRIMARY KEY,
    flow_id                TEXT NOT NULL,
    created_by_task_id     INTEGER,
    parent_gate_id         TEXT,
    mode                   TEXT NOT NULL DEFAULT \'all\',
    failure_policy         TEXT NOT NULL DEFAULT \'include\',
    status                 TEXT NOT NULL DEFAULT \'open\',
    expected_count         INTEGER NOT NULL,
    synthesis_task_type    TEXT,
    synthesis_problem_id   TEXT,
    synthesis_concern_scope TEXT,
    synthesis_payload_path TEXT,
    synthesis_priority     TEXT,
    aggregate_manifest_path TEXT,
    fired_task_id          INTEGER,
    created_at             TEXT DEFAULT (datetime(\'now\')),
    fired_at               TEXT
);

CREATE TABLE IF NOT EXISTS gate_members (
    gate_id              TEXT NOT NULL,
    chain_id             TEXT NOT NULL,
    slot_label           TEXT,
    leaf_task_id         INTEGER NOT NULL,
    status               TEXT NOT NULL DEFAULT \'pending\',
    result_manifest_path TEXT,
    completed_at         TEXT,
    PRIMARY KEY (gate_id, chain_id)
);

CREATE TABLE IF NOT EXISTS section_states (
    section_number   TEXT PRIMARY KEY,
    state            TEXT NOT NULL DEFAULT 'pending',
    updated_at       TEXT,
    error            TEXT,
    retry_count      INTEGER DEFAULT 0,
    blocked_reason   TEXT,
    context_json     TEXT,
    parent_section   TEXT DEFAULT NULL,
    depth            INTEGER DEFAULT 0,
    scope_grant      TEXT DEFAULT NULL,
    spawned_by_state TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_section_states_parent
  ON section_states(parent_section);
CREATE INDEX IF NOT EXISTS idx_section_states_depth
  ON section_states(depth);

CREATE TABLE IF NOT EXISTS section_transitions (
    id              INTEGER PRIMARY KEY,
    section_number  TEXT NOT NULL,
    from_state      TEXT NOT NULL,
    to_state        TEXT NOT NULL,
    event           TEXT NOT NULL,
    context_json    TEXT,
    attempt_number  INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_section_transitions_section
  ON section_transitions(section_number);
CREATE INDEX IF NOT EXISTS idx_section_transitions_section_to
  ON section_transitions(section_number, to_state);
CREATE INDEX IF NOT EXISTS idx_section_states_state
  ON section_states(state);

CREATE TABLE IF NOT EXISTS bootstrap_execution_log (
    id           INTEGER PRIMARY KEY,
    stage        TEXT    NOT NULL,
    status       TEXT    NOT NULL,
    started_at   TEXT,
    completed_at TEXT,
    error        TEXT
);
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
# Sets variables: FLAG_FROM, FLAG_AGENT, FLAG_SINCE, FLAG_LIMIT, FLAG_TAG,
#                 FLAG_PROBLEM, FLAG_SCOPE, FLAG_PAYLOAD, FLAG_PRIORITY,
#                 FLAG_DEPENDS_ON, FLAG_STATUS, FLAG_TYPE, FLAG_OUTPUT,
#                 FLAG_ERROR, FLAG_INSTANCE, FLAG_FLOW, FLAG_CHAIN,
#                 FLAG_DECLARED_BY_TASK, FLAG_TRIGGER_GATE, FLAG_FLOW_CONTEXT,
#                 FLAG_CONTINUATION, FLAG_RESULT_MANIFEST,
#                 FLAG_FRESHNESS_TOKEN, POSITIONAL
_parse_flags() {
  FLAG_FROM=""
  FLAG_AGENT=""
  FLAG_SINCE=""
  FLAG_LIMIT=""
  FLAG_TAG=""
  FLAG_PROBLEM=""
  FLAG_SCOPE=""
  FLAG_PAYLOAD=""
  FLAG_PRIORITY=""
  FLAG_DEPENDS_ON=""
  FLAG_STATUS=""
  FLAG_TYPE=""
  FLAG_OUTPUT=""
  FLAG_ERROR=""
  FLAG_INSTANCE=""
  FLAG_FLOW=""
  FLAG_CHAIN=""
  FLAG_DECLARED_BY_TASK=""
  FLAG_TRIGGER_GATE=""
  FLAG_FLOW_CONTEXT=""
  FLAG_CONTINUATION=""
  FLAG_RESULT_MANIFEST=""
  FLAG_FRESHNESS_TOKEN=""
  POSITIONAL=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --from)             FLAG_FROM="${2:?--from requires a value}"; shift 2 ;;
      --agent)            FLAG_AGENT="${2:?--agent requires a value}"; shift 2 ;;
      --since)            FLAG_SINCE="${2:?--since requires a value}"; shift 2 ;;
      --limit)            FLAG_LIMIT="${2:?--limit requires a value}"; shift 2 ;;
      --tag)              FLAG_TAG="${2:?--tag requires a value}"; shift 2 ;;
      --problem)          FLAG_PROBLEM="${2:?--problem requires a value}"; shift 2 ;;
      --scope)            FLAG_SCOPE="${2:?--scope requires a value}"; shift 2 ;;
      --payload)          FLAG_PAYLOAD="${2:?--payload requires a value}"; shift 2 ;;
      --priority)         FLAG_PRIORITY="${2:?--priority requires a value}"; shift 2 ;;
      --depends-on)       FLAG_DEPENDS_ON="${2:?--depends-on requires a value}"; shift 2 ;;
      --status)           FLAG_STATUS="${2:?--status requires a value}"; shift 2 ;;
      --type)             FLAG_TYPE="${2:?--type requires a value}"; shift 2 ;;
      --output)           FLAG_OUTPUT="${2:?--output requires a value}"; shift 2 ;;
      --error)            FLAG_ERROR="${2:?--error requires a value}"; shift 2 ;;
      --instance)         FLAG_INSTANCE="${2:?--instance requires a value}"; shift 2 ;;
      --flow)             FLAG_FLOW="${2:?--flow requires a value}"; shift 2 ;;
      --chain)            FLAG_CHAIN="${2:?--chain requires a value}"; shift 2 ;;
      --declared-by-task) FLAG_DECLARED_BY_TASK="${2:?--declared-by-task requires a value}"; shift 2 ;;
      --trigger-gate)     FLAG_TRIGGER_GATE="${2:?--trigger-gate requires a value}"; shift 2 ;;
      --flow-context)     FLAG_FLOW_CONTEXT="${2:?--flow-context requires a value}"; shift 2 ;;
      --continuation)     FLAG_CONTINUATION="${2:?--continuation requires a value}"; shift 2 ;;
      --result-manifest)  FLAG_RESULT_MANIFEST="${2:?--result-manifest requires a value}"; shift 2 ;;
      --freshness-token)  FLAG_FRESHNESS_TOKEN="${2:?--freshness-token requires a value}"; shift 2 ;;
      *)                  POSITIONAL+=("$1"); shift ;;
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

  # ── submit-task ────────────────────────────────────────────────────
  submit-task)
    _parse_flags "$@"
    submitted_by="${POSITIONAL[0]:?Missing submitted_by}"
    task_type="${POSITIONAL[1]:?Missing task_type}"

    result=$(python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
submitted_by = sys.argv[2]
task_type = sys.argv[3]
problem_id = sys.argv[4] or None
concern_scope = sys.argv[5] or None
payload_path = sys.argv[6] or None
priority = sys.argv[7] or 'normal'
depends_on = sys.argv[8] or None
instance_id = sys.argv[9] or None
flow_id = sys.argv[10] or None
chain_id = sys.argv[11] or None
declared_by_task_id = int(sys.argv[12]) if sys.argv[12] else None
trigger_gate_id = sys.argv[13] or None
flow_context_path = sys.argv[14] or None
continuation_path = sys.argv[15] or None
freshness_token = sys.argv[16] or None

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
status = 'pending'
if depends_on:
    dep_row = conn.execute('SELECT status FROM tasks WHERE id = ?', (int(depends_on),)).fetchone()
    if dep_row is None or dep_row[0] in ('failed', 'cancelled'):
        status = 'failed'
    elif dep_row[0] != 'complete':
        status = 'blocked'

completed_at = \"datetime('now')\" if status == 'failed' else None

cur.execute('''INSERT INTO tasks(submitted_by, task_type, problem_id, concern_scope,
               payload_path, priority, status,
               instance_id, flow_id, chain_id, declared_by_task_id,
               trigger_gate_id, flow_context_path, continuation_path,
               freshness_token, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))''',
            (submitted_by, task_type, problem_id, concern_scope,
             payload_path, priority, status,
             instance_id, flow_id, chain_id, declared_by_task_id,
             trigger_gate_id, flow_context_path, continuation_path,
             freshness_token))
conn.commit()
task_id = cur.lastrowid
if depends_on:
    satisfied = 1 if status == 'pending' else 0
    satisfied_at = 'datetime(\'now\')' if satisfied else None
    conn.execute(
        '''INSERT OR IGNORE INTO task_dependencies(
               task_id, depends_on_task_id, satisfied, satisfied_at
           ) VALUES(?, ?, ?, ?)''',
        (task_id, int(depends_on), satisfied, None if not satisfied else __import__('datetime').datetime.utcnow().isoformat())
    )
    conn.commit()
conn.close()
print(task_id)
" "$db" "$submitted_by" "$task_type" "$FLAG_PROBLEM" "$FLAG_SCOPE" "$FLAG_PAYLOAD" "$FLAG_PRIORITY" "$FLAG_DEPENDS_ON" "$FLAG_INSTANCE" "$FLAG_FLOW" "$FLAG_CHAIN" "$FLAG_DECLARED_BY_TASK" "$FLAG_TRIGGER_GATE" "$FLAG_FLOW_CONTEXT" "$FLAG_CONTINUATION" "$FLAG_FRESHNESS_TOKEN")
    echo "task:${result}"
    ;;

  # ── claim-task ─────────────────────────────────────────────────────
  claim-task)
    dispatcher="${1:?Missing dispatcher name}"
    task_id="${2:?Missing task ID}"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
dispatcher = sys.argv[2]
task_id = int(sys.argv[3])

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('''UPDATE tasks
               SET status='running', claimed_by=?, claimed_at=datetime('now')
               WHERE id=? AND status='pending' ''',
            (dispatcher, task_id))
if cur.rowcount == 0:
    conn.close()
    print('ERROR: task not claimable (not pending or not found)', file=sys.stderr)
    sys.exit(1)
conn.commit()
conn.close()
print(f'claimed:{task_id}')
" "$db" "$dispatcher" "$task_id"
    ;;

  # ── complete-task ──────────────────────────────────────────────────
  complete-task)
    _parse_flags "$@"
    task_id="${POSITIONAL[0]:?Missing task ID}"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
task_id = int(sys.argv[2])
output_path = sys.argv[3] or None
result_manifest = sys.argv[4] or None

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('''UPDATE tasks
               SET status='complete', output_path=?, result_manifest_path=?,
                   completed_at=datetime('now'), updated_at=datetime('now')
               WHERE id=? AND status='running' ''',
            (output_path, result_manifest, task_id))
if cur.rowcount == 0:
    conn.close()
    print('ERROR: task not completable (not running or not found)', file=sys.stderr)
    sys.exit(1)
conn.execute(
    '''UPDATE task_dependencies
       SET satisfied=1, satisfied_at=datetime('now')
       WHERE depends_on_task_id=? AND satisfied=0''',
    (task_id,),
)
conn.execute(
    '''UPDATE tasks
       SET status='pending', status_reason=NULL, updated_at=datetime('now')
       WHERE status='blocked'
         AND EXISTS (
           SELECT 1 FROM task_dependencies deps WHERE deps.task_id = tasks.id
         )
         AND NOT EXISTS (
           SELECT 1 FROM task_dependencies deps
           WHERE deps.task_id = tasks.id AND deps.satisfied = 0
         )'''
)
conn.commit()
conn.close()
print(f'completed:{task_id}')
" "$db" "$task_id" "$FLAG_OUTPUT" "$FLAG_RESULT_MANIFEST"
    ;;

  # ── fail-task ──────────────────────────────────────────────────────
  fail-task)
    _parse_flags "$@"
    task_id="${POSITIONAL[0]:?Missing task ID}"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
task_id = int(sys.argv[2])
error = sys.argv[3] or None
result_manifest = sys.argv[4] or None

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('''UPDATE tasks
               SET status='failed', error=?, result_manifest_path=?,
                   completed_at=datetime('now'), updated_at=datetime('now')
               WHERE id=? AND status='running' ''',
            (error, result_manifest, task_id))
if cur.rowcount == 0:
    conn.close()
    print('ERROR: task not failable (not running or not found)', file=sys.stderr)
    sys.exit(1)
queue = [task_id]
seen = set()
while queue:
    current = queue.pop(0)
    if current in seen:
        continue
    seen.add(current)
    rows = conn.execute(
        'SELECT DISTINCT task_id FROM task_dependencies WHERE depends_on_task_id=?',
        (current,),
    ).fetchall()
    for (downstream_id,) in rows:
        updated = conn.execute(
            \"\"\"UPDATE tasks
                   SET status='failed',
                       error=?,
                       status_reason='dependency_failed',
                       completed_at=COALESCE(completed_at, datetime('now')),
                       updated_at=datetime('now')
                   WHERE id=?
                     AND status IN ('pending', 'running', 'blocked', 'awaiting_input')\"\"\",
            (f'dependency_failed:{current}', downstream_id),
        )
        if updated.rowcount:
            queue.append(downstream_id)
conn.commit()
conn.close()
print(f'failed:{task_id}')
" "$db" "$task_id" "$FLAG_ERROR" "$FLAG_RESULT_MANIFEST"
    ;;

  # ── list-tasks ─────────────────────────────────────────────────────
  list-tasks)
    _parse_flags "$@"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
status_filter = sys.argv[2]
type_filter = sys.argv[3]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

clauses = []
params = []
if status_filter:
    clauses.append('status = ?')
    params.append(status_filter)
if type_filter:
    clauses.append('task_type = ?')
    params.append(type_filter)

where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''

cur.execute(f'''SELECT id, submitted_by, task_type, problem_id, concern_scope,
                       status, claimed_by, priority, created_at,
                       instance_id, flow_id, chain_id, declared_by_task_id,
                       trigger_gate_id, flow_context_path, continuation_path,
                       result_manifest_path
                FROM tasks {where}
                ORDER BY id ASC''', params)
rows = cur.fetchall()
conn.close()

if not rows:
    print('NO_TASKS')
else:
    for row in rows:
        (tid, by, ttype, pid, scope, st, claimed, prio, created,
         inst, flow, chain, declared_by, trig_gate, flow_ctx, cont,
         res_manifest) = row
        parts = [f'id={tid}', f'type={ttype}', f'status={st}', f'by={by}', f'prio={prio}']
        if pid:
            parts.append(f'problem={pid}')
        if scope:
            parts.append(f'scope={scope}')
        if claimed:
            parts.append(f'claimed_by={claimed}')
        if inst:
            parts.append(f'instance={inst}')
        if flow:
            parts.append(f'flow={flow}')
        if chain:
            parts.append(f'chain={chain}')
        if declared_by:
            parts.append(f'declared_by_task={declared_by}')
        if trig_gate:
            parts.append(f'trigger_gate={trig_gate}')
        if flow_ctx:
            parts.append(f'flow_context={flow_ctx}')
        if cont:
            parts.append(f'continuation={cont}')
        if res_manifest:
            parts.append(f'result_manifest={res_manifest}')
        parts.append(f'created={created}')
        print(' | '.join(parts))
" "$db" "$FLAG_STATUS" "$FLAG_TYPE"
    ;;

  # ── next-task ──────────────────────────────────────────────────────
  next-task)
    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

# Find pending tasks ordered by priority then id.
# Priority ordering: high > normal > low.
cur.execute('''SELECT id, task_type, problem_id, concern_scope, payload_path,
                      priority, submitted_by,
                      instance_id, flow_id, chain_id, declared_by_task_id,
                      trigger_gate_id, flow_context_path, continuation_path,
                      freshness_token
               FROM tasks
               WHERE status = 'pending'
               ORDER BY
                 CASE priority
                   WHEN 'high'   THEN 0
                   WHEN 'normal' THEN 1
                   WHEN 'low'    THEN 2
                   ELSE 3
                 END,
                 id ASC''')
rows = cur.fetchall()

for (tid, ttype, pid, scope, payload, prio, by,
     inst, flow, chain, declared_by, trig_gate, flow_ctx, cont,
     freshness) in rows:
    dep_row = conn.execute(
        'SELECT 1 FROM task_dependencies WHERE task_id=? AND satisfied=0 LIMIT 1',
        (tid,),
    ).fetchone()
    if dep_row:
        continue

    # This task is runnable.
    parts = [f'id={tid}', f'type={ttype}', f'by={by}', f'prio={prio}']
    if pid:
        parts.append(f'problem={pid}')
    if scope:
        parts.append(f'scope={scope}')
    if payload:
        parts.append(f'payload={payload}')
    if inst:
        parts.append(f'instance={inst}')
    if flow:
        parts.append(f'flow={flow}')
    if chain:
        parts.append(f'chain={chain}')
    if declared_by:
        parts.append(f'declared_by_task={declared_by}')
    if trig_gate:
        parts.append(f'trigger_gate={trig_gate}')
    if flow_ctx:
        parts.append(f'flow_context={flow_ctx}')
    if cont:
        parts.append(f'continuation={cont}')
    if freshness:
        parts.append(f'freshness={freshness}')
    conn.close()
    print(' | '.join(parts))
    sys.exit(0)

conn.close()
print('NO_RUNNABLE_TASKS')
" "$db"
    ;;

  # ── cancel-task ─────────────────────────────────────────────────────
  cancel-task)
    _parse_flags "$@"
    task_id="${POSITIONAL[0]:?Missing task ID}"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
task_id = int(sys.argv[2])
error = sys.argv[3] or None

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()
cur.execute('''UPDATE tasks
               SET status='cancelled', error=?, completed_at=datetime('now')
               WHERE id=? AND status='pending' ''',
            (error, task_id))
if cur.rowcount == 0:
    conn.close()
    print('ERROR: task not cancellable (not pending or not found)', file=sys.stderr)
    sys.exit(1)
conn.commit()
conn.close()
print(f'cancelled:{task_id}')
" "$db" "$task_id" "$FLAG_ERROR"
    ;;

  # ── gate-status ────────────────────────────────────────────────────
  gate-status)
    gate_id="${1:?Missing gate ID}"

    python3 -c "
import sqlite3, sys

db_path = sys.argv[1]
gate_id = sys.argv[2]

conn = sqlite3.connect(db_path, timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
cur = conn.cursor()

cur.execute('''SELECT gate_id, flow_id, created_by_task_id, parent_gate_id,
                      mode, failure_policy, status, expected_count,
                      synthesis_task_type, synthesis_problem_id,
                      synthesis_concern_scope, synthesis_payload_path,
                      synthesis_priority, aggregate_manifest_path,
                      fired_task_id, created_at, fired_at
               FROM gates WHERE gate_id = ?''', (gate_id,))
gate = cur.fetchone()
if not gate:
    conn.close()
    print(f'ERROR: gate not found: {gate_id}', file=sys.stderr)
    sys.exit(1)

(gid, flow, created_by, parent, mode, fail_pol, status, expected,
 syn_type, syn_pid, syn_scope, syn_payload, syn_prio,
 agg_manifest, fired_tid, created_at, fired_at) = gate

parts = [f'gate={gid}', f'flow={flow}', f'mode={mode}', f'status={status}',
         f'expected={expected}', f'failure_policy={fail_pol}']
if created_by is not None:
    parts.append(f'created_by_task={created_by}')
if parent:
    parts.append(f'parent_gate={parent}')
if syn_type:
    parts.append(f'synthesis_type={syn_type}')
if syn_pid:
    parts.append(f'synthesis_problem={syn_pid}')
if syn_scope:
    parts.append(f'synthesis_scope={syn_scope}')
if syn_payload:
    parts.append(f'synthesis_payload={syn_payload}')
if syn_prio:
    parts.append(f'synthesis_priority={syn_prio}')
if agg_manifest:
    parts.append(f'aggregate_manifest={agg_manifest}')
if fired_tid is not None:
    parts.append(f'fired_task={fired_tid}')
parts.append(f'created={created_at}')
if fired_at:
    parts.append(f'fired_at={fired_at}')
print(' | '.join(parts))

# Print members
cur.execute('''SELECT gate_id, chain_id, slot_label, leaf_task_id, status,
                      result_manifest_path, completed_at
               FROM gate_members WHERE gate_id = ?
               ORDER BY chain_id ASC''', (gate_id,))
members = cur.fetchall()
for gid, cid, slot, leaf_tid, mstatus, res_manifest, completed in members:
    mparts = [f'  chain={cid}', f'leaf_task={leaf_tid}', f'status={mstatus}']
    if slot:
        mparts.append(f'slot={slot}')
    if res_manifest:
        mparts.append(f'result_manifest={res_manifest}')
    if completed:
        mparts.append(f'completed={completed}')
    print(' | '.join(mparts))

conn.close()
" "$db" "$gate_id"
    ;;

  # ── unknown ───────────────────────────────────────────────────────
  *)
    echo "Unknown command: $cmd" >&2
    echo "Usage: db.sh init|send|recv|check|drain|register|unregister|agents|cleanup|log|tail|query|submit-task|claim-task|complete-task|fail-task|list-tasks|next-task|cancel-task|gate-status <db> ..." >&2
    exit 1
    ;;
esac
