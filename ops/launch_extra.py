"""Two more Devin sessions: a Track 4 play game and a Testing session that drives the real app."""
import json
import sys
from pathlib import Path

from ops.devin_client import DevinClient, load_env

ROOT = Path(__file__).resolve().parent.parent
RULES = (
    "REPO: https://github.com/ryyansafar/stillcounts (main). Still Counts is an offline-first CRDT ledger for field teams. "
    "RULES: do NOT edit oracle/, stillcounts/crdt.py or CODEOWNERS; tests first; keep `.venv/bin/python -m pytest -q` and `node --test client/test` green; "
    "one pull request on your own branch; no AI or Anthropic attribution in code, docs or commits. Return only the structured JSON."
)
JOBS = {
    "PLAY-RACE": (
        "BUILD (Track 4, play): 'Referee Race', a game across everyone's phones. Page client/race.html + race.js + race.css and server/race_api.py "
        "(include from create_app). The host starts a round; the server sends every phone the SAME 8 to 12 operations (adjust/create/delete across 3 fake "
        "volunteers) but in a DIFFERENT shuffled order per phone, with a few duplicates. Each player taps what the final stock of each item will be "
        "(computed by the real merge in client/crdt.js, works offline), fastest correct answer scores most, wrong answers show the first op that "
        "tricked them. Live leaderboard on client/board.html. The payoff line shown on screen: every phone saw a different order and the merge gave the "
        "same answer. Cache files in client/sw.js (bump cache name). Tests in tests/test_race_api.py and a node test. Branch devin/play-race."),
    "QA-RECORD": (
        "TEST (Devin Testing, no product code changes except fixing bugs you find in client/ or server/): run the app (see scripts/run.sh), drive it in a real "
        "browser like a volunteer on a phone: join, add items, tap +/-, go offline (devtools offline), keep tapping, reload offline, reconnect, "
        "confirm board numbers, two tabs, oversell flag, CSV export, names and activity feed. Record the session. File every real bug as a GitHub issue "
        "with steps and a screenshot, fix small ones on branch devin/qa-fixes with a regression test each, open ONE pull request. Put a short QA report in "
        "docs/QA.md (what was tested, pass/fail table, honest list of anything not tested). Branch devin/qa-fixes."),
}


def main(env_path: str) -> None:
    env = load_env(env_path)
    schema = json.loads((ROOT / "devin/writer.schema.json").read_text())
    client = DevinClient(env["DEVIN_TOKEN"], env["DEVIN_ORG"])
    out = {}
    for role, job in JOBS.items():
        res = client.create_session(f"{RULES}\n{job}", title=role, tags=["stillcounts", role.lower()],
                                    max_acu_limit=5, structured_output_schema=schema)
        out[role] = {"session_id": res.get("session_id"), "url": res.get("url")}
        print(role, "->", res.get("url"))
    (ROOT / ".sessions_extra.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
