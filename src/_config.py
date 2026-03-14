"""Shared configuration constants for the section-loop agent."""

import os
from pathlib import Path

WORKFLOW_HOME = Path(
    os.environ.get(
        "WORKFLOW_HOME",
        Path(__file__).resolve().parent,
    ),
)
DB_SH = WORKFLOW_HOME / "scripts" / "db.sh"
AGENT_NAME = "section-loop"
