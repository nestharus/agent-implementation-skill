#!/usr/bin/env python3
import time
import subprocess
from pathlib import Path
from typing import List


class AgentMonitor:
    def __init__(
        self, planspace_path: str, db_path: str, agent_mailbox: str, my_mailbox: str
    ):
        self.planspace_path = Path(planspace_path)
        self.db_path = db_path
        self.agent_mailbox = agent_mailbox
        self.my_mailbox = my_mailbox
        self.plan_messages: List[str] = []
        self.done_messages: List[str] = []
        self.last_message_time = time.time()
        self.db_sh_path = str(
            self.planspace_path.parent / "workflow" / "scripts" / "db.sh"
        )

    def register_mailbox(self):
        result = subprocess.run(
            [self.db_sh_path, "register", self.db_path, self.my_mailbox],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Failed to register mailbox: {result.stderr}")
            raise RuntimeError(f"Mailbox registration failed: {result.stderr}")

    def drain_messages(self, mailbox: str) -> List[str]:
        result = subprocess.run(
            [self.db_sh_path, "drain", self.db_path, mailbox],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []

        messages = result.stdout.strip().split("\n") if result.stdout.strip() else []
        return [m for m in messages if m]

    def check_agent_mailbox(self) -> bool:
        messages = self.drain_messages(self.agent_mailbox)

        if messages:
            self.last_message_time = time.time()

        for msg in messages:
            if msg.startswith("LOOP_DETECTED:"):
                self.log_signal(msg)
                print(f"Agent self-reported loop: {msg}")
                return False

            if msg.startswith("plan:"):
                self.plan_messages.append(msg)
                self.check_for_loop(msg)

            if msg.startswith("done:"):
                self.done_messages.append(msg)
                self.check_for_done_loop(msg)

        return True

    def check_for_loop(self, new_plan: str):
        for prev_plan in self.plan_messages[:-1]:
            if self.plans_match(new_plan, prev_plan):
                action = self.extract_action(new_plan)
                self.log_signal(f"LOOP_DETECTED:{self.agent_mailbox}:{action}")
                print(f"Loop detected: {action}")
                self.exit_loop_detected()

    def check_for_done_loop(self, new_done: str):
        action = new_done.removeprefix("done:").strip()
        for prev_done in self.done_messages[:-1]:
            prev_action = prev_done.removeprefix("done:").strip()
            if action == prev_action:
                self.log_signal(
                    f"LOOP_DETECTED:{self.agent_mailbox}:duplicate done: {action}"
                )
                print(f"Loop detected (duplicate done): {action}")
                self.exit_loop_detected()

    def plans_match(self, plan1: str, plan2: str) -> bool:
        action1 = self.extract_action(plan1)
        action2 = self.extract_action(plan2)

        parts1 = action1.lower().split()
        parts2 = action2.lower().split()

        if len(parts1) < 2 or len(parts2) < 2:
            return False

        verb_matches = parts1[0] == parts2[0]
        file_matches = any(
            p1 in p2 or p2 in p1
            for p1 in parts1[1:]
            for p2 in parts2[1:]
            if "." in p1 and "." in p2
        )

        return verb_matches and file_matches

    def extract_action(self, plan_msg: str) -> str:
        return plan_msg.removeprefix("plan:").strip()

    def check_my_mailbox(self) -> bool:
        messages = self.drain_messages(self.my_mailbox)
        return "agent-finished" in messages

    def log_signal(self, payload: str):
        subprocess.run(
            [
                self.db_sh_path,
                "log",
                self.db_path,
                "signal",
                self.agent_mailbox,
                payload,
                "--agent",
                self.my_mailbox,
            ],
            capture_output=True,
        )

    def check_stalled(self) -> bool:
        elapsed = time.time() - self.last_message_time
        return elapsed >= 300

    def log_stalled(self):
        payload = f"STALLED:{self.agent_mailbox}:no messages for 5 minutes"
        self.log_signal(payload)
        print(f"Agent stalled: {payload}")

    def exit_loop_detected(self):
        print(f"Loop detected - exiting monitor")
        raise SystemExit(1)

    def run(self):
        print(f"Starting monitor for {self.agent_mailbox}")

        try:
            self.register_mailbox()
        except RuntimeError as e:
            print(f"Failed to start monitor: {e}")
            return

        while True:
            try:
                if not self.check_agent_mailbox():
                    break

                if self.check_my_mailbox():
                    print(f"Agent {self.agent_mailbox} finished")
                    break

                if self.check_stalled():
                    self.log_stalled()
                    break

                time.sleep(10)

            except KeyboardInterrupt:
                print("\nMonitor stopped by user")
                break
            except SystemExit:
                break
            except Exception as e:
                print(f"Error in monitor loop: {e}")
                time.sleep(5)

        print("Monitor stopped")


if __name__ == "__main__":
    planspace = "/home/nes/.claude/workspaces/pulseplan"
    db_path = "/home/nes/.claude/workspaces/pulseplan/run.db"
    agent = "impl-08"
    monitor = "impl-08-monitor"

    monitor = AgentMonitor(planspace, db_path, agent, monitor)
    monitor.run()
