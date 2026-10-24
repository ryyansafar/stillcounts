"""Renders docs/devin.md from data/devin_evidence.json, so the page and the board's Devin panel share one source.

    python -m ops.devin_evidence          # rewrite docs/devin.md
    python -m ops.devin_evidence --check  # exit 1 if docs/devin.md is out of date
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "data" / "devin_evidence.json"
PAGE = ROOT / "docs" / "devin.md"
REPO = "https://github.com/ryyansafar/stillcounts"


def load(path: Path = EVIDENCE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def counts(evidence: dict) -> dict:
    sessions = evidence["sessions"]
    return {"sessions": len(sessions), "prs": sum(1 for s in sessions if s["pr"]),
            "issues": sum(len(s["issues"]) for s in sessions), "source": "data/devin_evidence.json"}


def render(evidence: dict) -> str:
    c = counts(evidence)
    rows = []
    for s in evidence["sessions"]:
        links = []
        if s["pr"]:
            links.append(f"[PR #{s['pr']}]({REPO}/pull/{s['pr']}) (merged)")
        links += [f"[issue #{n}]({REPO}/issues/{n})" for n in s["issues"]]
        rows.append(f"| {s['role']} | {s['kind']} | [{s['session_id'][:8]}](https://app.devin.ai/sessions/{s['session_id']}) "
                    f"| {s['outcome']} | {', '.join(links) or 'none'} |")
    overridden = "\n".join(f"- {line}" for line in evidence["rejected_or_overridden"])
    return f"""# Devin evidence

Generated from `data/devin_evidence.json` by `python -m ops.devin_evidence`. Do not edit by hand. {evidence['note']}

**{c['sessions']} sessions, {c['prs']} pull requests (all merged by a human), {c['issues']} issues filed.**

| Role | Kind | Session | Outcome | Links |
|---|---|---|---|---|
{chr(10).join(rows)}

## Who did what

{evidence['share_of_work']}

## ACU use

{evidence['acu_note']}

## Rejected or overridden work

{overridden}

## What Devin did not do

- It did not write the merge function (`stillcounts/crdt.py`) or the referee (`oracle/`); those are never edited by agents (`.github/CODEOWNERS`).
- The three scouts are read-only: they proposed scenarios and changed nothing.
- No session opened a pull request that fixes a bug the harness found, and no Devin session was run on one afterwards: the open scenarios were left to a human.
"""


def main(argv: list[str]) -> int:
    text = render(load())
    if argv == ["--check"]:
        return 0 if PAGE.read_text(encoding="utf-8") == text else 1
    PAGE.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
