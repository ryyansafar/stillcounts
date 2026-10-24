"""Writes data/devin_counts.json (sessions, pull requests, structured outputs) from the Devin API for the board.
The token stays in this local script; the web server only serves the finished file."""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from ops.devin_client import DevinClient, load_env

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "devin_counts.json"


def summarise(sessions: list[dict]) -> dict:
    return {
        "sessions": len(sessions),
        "prs": sum(len(s.get("pull_requests") or []) for s in sessions),
        "structured_outputs": sum(1 for s in sessions if isinstance(s.get("structured_output"), dict)),
        "by_status": dict(Counter(s.get("status", "unknown") for s in sessions)),
        "acus_reported": round(sum(float(s.get("acus_consumed") or 0) for s in sessions), 2),
    }


def collect(env_path: str) -> dict:
    env = load_env(env_path)
    client = DevinClient(env["DEVIN_TOKEN"], env["DEVIN_ORG"])
    ids = [v["session_id"] for v in json.loads((ROOT / ".sessions.json").read_text()).values()]
    sessions = [client.get_session(i) for i in ids]
    out = summarise(sessions)
    out["updated"] = time.strftime("%H:%M:%S")
    return out


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    while True:
        try:
            OUT.write_text(json.dumps(collect(sys.argv[1])))
        except Exception as exc:       # keep the last good file; never crash the loop
            print("devin counts failed:", type(exc).__name__)
        time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 30)
