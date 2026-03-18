#!/usr/bin/env python3
"""Agent Monitor - watches a mailbox for loop detection."""

import subprocess
import time
import sys
from pathlib import Path


class AgentMonitor:
    def __init__(self, planspace, db_path, agent_mailbox, monitor_mailbox, db_sh):
        self.planspace = Path(planspace)
        self.db_path = Path(db_path)
        self.agent_mailbox = agent_mailbox
        self.monitor_mailbox = monitor_mailbox
        self.db_sh = db_sh
        self.plan_messages = []
        self.done_messages = []
        self.last_message_time = time.time()
        self.running = True

    def drain_mailbox(self, mailbox):
        """Drain and return all messages from a mailbox."""
        cmd = [self.db_sh, "log", str(self.db_path), "drain", mailbox]
        result = subprocess.run(cmd, capture_output=True, text=True)
        messages = []
        if result.stdout:
            messages = [line for line in result.stdout.strip().split("\n") if line]
        return messages

    def log_signal(self, signal_msg):
        """Log a signal event to the database."""
        cmd = [
            self.db_sh,
            "log",
            str(self.db_path),
            "signal",
            self.agent_mailbox,
            signal_msg,
            "--agent",
            self.monitor_mailbox,
        ]
        subprocess.run(cmd, capture_output=True)

    def detect_loop(self, new_plan):
        """Check if new_plan is a repetition of previous plans."""
        for prev_plan in self.plan_messages:
            if self.similar_action(prev_plan, new_plan):
                return prev_plan
        return None

    def similar_action(self, msg1, msg2):
        """Check if two messages describe similar actions (same file + verb)."""
        if not msg1 or not msg2:
            return False
        msg1_lower = msg1.lower()
        msg2_lower = msg2.lower()

        common_verbs = [
            "read",
            "read reading",
            "reading",
            "write",
            "writing",
            "write written",
            "analyze",
            "analyzing",
            "analyze analyzed",
            "fix",
            "fixing",
            "fix fixed",
        ]
        for verb in common_verbs:
            if verb in msg1_lower and verb in msg2_lower:
                return True

        return msg1_lower == msg2_lower

    def check_self_reported_loop(self, messages):
        """Check if any message is agent-reported loop."""
        for msg in messages:
            if msg.startswith("LOOP_DETECTED:"):
                return msg
        return None

    def run(self):
        """Main monitoring loop."""
        print(f"[Monitor] Watching mailbox {self.agent_mailbox}...", flush=True)

        stall_timeout = 300  # 5 minutes
        iteration = 0

        while self.running:
            iteration += 1

            # Drain agent mailbox
            agent_messages = self.drain_mailbox(self.agent_mailbox)

            if agent_messages:
                self.last_message_time = time.time()

                # Check for self-reported loop
                self_reported = self.check_self_reported_loop(agent_messages)
                if self_reported:
                    print(
                        f"[Monitor] Agent self-reported loop: {self_reported}",
                        flush=True,
                    )
                    self.log_signal(self_reported)
                    return

                # Track plan: and done: messages
                new_plans = [msg for msg in agent_messages if msg.startswith("plan:")]
                new_dones = [msg for msg in agent_messages if msg.startswith("done:")]

                # Check for duplicate done: messages
                for new_done in new_dones:
                    if new_done in self.done_messages:
                        loop_msg = f"LOOP_DETECTED:{self.agent_mailbox}:done message repeated: {new_done}"
                        print(f"[Monitor] Detected repeated done message", flush=True)
                        self.log_signal(loop_msg)
                        return
                    self.done_messages.append(new_done)

                # Check for plan loops
                for new_plan in new_plans:
                    repeated_plan = self.detect_loop(new_plan)
                    if repeated_plan:
                        loop_msg = f"LOOP_DETECTED:{self.agent_mailbox}:repeated action: {new_plan}"
                        print(
                            f"[Monitor] Detected loop: {new_plan} matches {repeated_plan}",
                            flush=True,
                        )
                        self.log_signal(loop_msg)
                        return
                    self.plan_messages.append(new_plan)

            # Check monitor mailbox for agent-finished
            monitor_messages = self.drain_mailbox(self.monitor_mailbox)
            if "agent-finished" in monitor_messages:
                print("[Monitor] Agent finished signal received", flush=True)
                return

            # Check for stall
            time_since_last = time.time() - self.last_message_time
            if time_since_last >= stall_timeout:
                stall_msg = f"STALLED:{self.agent_mailbox}:no messages for 5 minutes"
                print(
                    f"[Monitor] Agent stalled (no messages for {time_since_last:.0f}s)",
                    flush=True,
                )
                self.log_signal(stall_msg)
                return

            # Wait before next iteration
            time.sleep(10)


if __name__ == "__main__":
    planspace = "/home/nes/.claude/workspaces/pulseplan"
    db_path = "/home/nes/.claude/workspaces/pulseplan/run.db"
    agent_mailbox = "intg-proposal-09"
    monitor_mailbox = "intg-proposal-09-monitor"
    db_sh = "/home/nes/projects/agent-implementation-skill/src/scripts/db.sh"

    monitor = AgentMonitor(planspace, db_path, agent_mailbox, monitor_mailbox, db_sh)
    monitor.run()
