#!/usr/bin/env python3
"""
Agent Monitor: Watch a single agent's mailbox for signs of looping or repetition.
Lightweight pattern matcher — does not investigate files or fix issues.
Only detects loops and logs signal events to the database.
"""

import subprocess
import time
import re
import sys
from pathlib import Path

# Configuration from task
Planspace = Path("/home/nes/.claude/workspaces/pulseplan")
Database = str(Planspace / "run.db")
DB_SH = "/home/nes/projects/agent-implementation-skill/src/scripts/db.sh"
AGENT_MAILBOX = "setup-03"
MONITOR_MAILBOX = "setup-03-monitor"

# Track plan messages for loop detection
plan_messages = []
done_messages = []

# Timers
last_message_time = time.time()
STALL_TIMEOUT = 300  # 5 minutes in seconds
LOOP_INTERVAL = 10  # seconds between checks


def run_db_command(args):
    """Execute db.sh command and return stdout"""
    result = subprocess.run([DB_SH] + args, capture_output=True, text=True, check=False)
    return result.stdout.strip(), result.returncode


def drain_messages(mailbox):
    """Get all pending messages from a mailbox"""
    stdout, _ = run_db_command(["drain", Database, mailbox])
    if not stdout:
        return []
    return stdout.split("---")


def normalize_plan_message(msg):
    """
    Extract key information from a plan message for comparison.
    Returns tuple of (file_path, action_verb)
    """
    # Look for patterns like "reading foo.py", "writing bar.txt", etc.
    # Match common verbs followed by file paths

    # Pattern: "reading/writing/editing/creating/fixing <file>"
    match = re.search(
        r"(reading|writing|editing|creating|fixing|modifying|updating|analyzing|examining)\s+([^\s,;\.]+(?:\.[^\s,;\.]+)?)",
        msg,
        re.IGNORECASE,
    )
    if match:
        verb = match.group(1).lower()
        file_path = match.group(2).lower()
        return (file_path, verb)

    # Fallback: extract any file-looking pattern
    file_match = re.search(r"([a-zA-Z0-9_/][a-zA-Z0-9_\-./]*\.[a-zA-Z0-9_\-]+)", msg)
    if file_match:
        file_path = file_match.group(1).lower()
        # Try to find a verb before the file
        verb_match = re.search(
            r"(reading|writing|editing|creating|fixing|modifying|updating|analyzing|examining)\s+[^\s]*?"
            + re.escape(file_path),
            msg,
            re.IGNORECASE,
        )
        if verb_match:
            verb = verb_match.group(1).lower()
            return (file_path, verb)
        return (file_path, "unknown_action")

    # No clear file/action found
    return None


def is_loop_detected(new_msg):
    """
    Check if a new message indicates a loop.
    Returns (is_loop, description)
    """
    normalized = normalize_plan_message(new_msg)

    if not normalized:
        return False, None

    file_path, action = normalized

    # Check against all previous plan messages
    for i, prev_msg in enumerate(plan_messages):
        prev_normalized = normalize_plan_message(prev_msg)
        if not prev_normalized:
            continue

        prev_file, prev_action = prev_normalized

        # Same file and same action = loop
        if file_path == prev_file and action == prev_action:
            return True, f"{action} {file_path}"

    return False, None


def is_done_repeated(new_msg):
    """Check if a done message repeats"""
    for prev in done_messages:
        if new_msg == prev:
            return True
    return False


def log_signal(signal_msg):
    """Log a signal event to the database"""
    run_db_command(
        [
            "log",
            Database,
            "signal",
            AGENT_MAILBOX,
            signal_msg,
            "--agent",
            MONITOR_MAILBOX,
        ]
    )
    print(f"[SIGNAL] Logged: {signal_msg}")


def main():
    print(f"[STARTING] Agent monitor for {AGENT_MAILBOX}")
    print(f"[REGISTERING] {MONITOR_MAILBOX}")

    # Register monitor
    stdout, _ = run_db_command(["register", Database, MONITOR_MAILBOX])
    print(f"[REGISTERED] {stdout}")

    print(f"[MONITORING] Watching {AGENT_MAILBOX} mailbox...")
    print(f"[SIGNAL] Will log to `dbsh query {Database} signal --tag {AGENT_MAILBOX}`")
    print(f"[LOOP] Will detect repeated plan messages (same file + same action)")
    print(f"[STALL] Will timeout after {STALL_TIMEOUT}s with no messages")
    print(f"[EXIT] On `agent-finished` in {MONITOR_MAILBOX} mailbox\n")

    global last_message_time

    try:
        while True:
            # Drain all messages from agent mailbox
            messages = drain_messages(AGENT_MAILBOX)

            if messages:
                last_message_time = time.time()
                print(
                    f"[{time.strftime('%H:%M:%S')}] Drained {len(messages)} message(s)"
                )

                for msg in messages:
                    print(f"  → {msg[:100]}...")

                    # Check for self-reported loop
                    if msg.startswith("LOOP_DETECTED:"):
                        loop_payload = msg[len("LOOP_DETECTED:") :]
                        log_signal(f"LOOP_DETECTED:{AGENT_MAILBOX}:{loop_payload}")
                        print(f"\n[LOOP] Agent self-reported loop: {loop_payload}")
                        print("[EXITING]\n")
                        return

                    # Track plan messages
                    if msg.startswith("plan:"):
                        plan_messages.append(msg)
                        print(f"  [PLAN] Tracked (total: {len(plan_messages)})")

                        # Check for loop
                        is_loop, description = is_loop_detected(msg)
                        if is_loop and description:
                            log_signal(f"LOOP_DETECTED:{AGENT_MAILBOX}:{description}")
                            print(f"\n[LOOP] Detected: {description}")
                            print("[EXITING]\n")
                            return

                    # Track done messages
                    if msg.startswith("done:"):
                        if is_done_repeated(msg):
                            log_signal(
                                f"LOOP_DETECTED:{AGENT_MAILBOX}:repeated done: {msg}"
                            )
                            print(f"\n[LOOP] Repeated done message")
                            print("[EXITING]\n")
                            return
                        done_messages.append(msg)

            # Check for agent-finished signal
            control_messages = drain_messages(MONITOR_MAILBOX)
            if control_messages:
                print(
                    f"[{time.strftime('%H:%M:%S')}] Got {len(control_messages)} control message(s)"
                )
                for msg in control_messages:
                    print(f"  ← {msg[:100]}...")
                    if msg.strip() == "agent-finished":
                        print(f"\n[FINISHED] Agent {AGENT_MAILBOX} completed normally")
                        print("[EXITING]\n")
                        return

            # Check for stall (no messages for 5 minutes)
            elapsed = time.time() - last_message_time
            if elapsed >= STALL_TIMEOUT:
                log_signal(
                    f"STALLED:{AGENT_MAILBOX}:no messages for {int(elapsed)} seconds"
                )
                print(f"\n[STALLED] No messages for {int(elapsed)} seconds")
                print("[EXITING]\n")
                return

            # Wait before next iteration
            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Monitor stopped by user")
    finally:
        # Unregister monitor
        print(f"[UNREGISTERING] {MONITOR_MAILBOX}")
        run_db_command(["unregister", Database, MONITOR_MAILBOX])
        print("[DONE")


if __name__ == "__main__":
    main()
