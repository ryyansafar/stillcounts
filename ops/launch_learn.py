"""Start three parallel Devin writer sessions that build the Earn the Code Learn mode."""
import json
import sys
from pathlib import Path

from ops.devin_client import DevinClient, load_env

ROOT = Path(__file__).resolve().parent.parent
COMMON = (
    "REPO: https://github.com/ryyansafar/stillcounts (main). PRODUCT: Still Counts, an offline-first CRDT ledger. "
    "FEATURE: 'Earn the Code' Learn mode. Devin-built code of the real merge stays LOCKED until the learner earns each stage "
    "with checkpoints: PREDICT (guess the merged result; the real merge runs and checks), FIND THE BUG (a mutant of the real merge "
    "from oracle/mutation.py is shown; the learner names what is wrong), WRITE (learner completes one small function; the existing referee "
    "grades it), EXPLAIN (own words vs a hidden rubric, shown after). Plain code grades predict, bug and write; never a model. "
    "HARD RULES: do NOT edit oracle/ or stillcounts/crdt.py or CODEOWNERS; write tests first and keep all existing tests green "
    "(`.venv/bin/python -m pytest -q` and `node --test client/test`); open ONE pull request on your own branch with a clear description; "
    "no AI or Anthropic attribution anywhere in code, docs or commits. Return only the structured JSON."
)
JOBS = {
    "LEARN-CONTENT": (
        "YOUR PART (content+grader, Python): create learn/stages.json (4 stages: 1 duplicate ids, 2 signed adjust sum, 3 Lamport last-writer-wins, "
        "4 oversell flag) each with predict cases (op lists + expected result computed by importing stillcounts.crdt.merge), a find-the-bug item "
        "built from a real mutant in oracle/mutation.py MUTANTS, a write-task (function stub + docstring), and an explain rubric (list of key points). "
        "Create learn/grader.py: grade_predict, grade_bug, grade_write (run the learner's function in a subprocess with a timeout, plug it into the "
        "referee in oracle/, return pass/fail and the first counterexample), grade_explain (rubric keyword coverage, returns covered and missed points; "
        "no model call). Add tests/test_learn_*.py. Branch devin/learn-content."),
    "LEARN-API": (
        "YOUR PART (server): add a learn router to server/app.py or server/learn_api.py (include it from create_app): GET /api/learn/stages (locked code "
        "hidden until unlocked), POST /api/learn/{stage}/predict|bug|write|explain which calls learn/grader.py (another Devin session is writing it; "
        "code against this interface: grade_predict(stage, answer)->dict, grade_bug(stage, answer)->dict, grade_write(stage, source)->dict, "
        "grade_explain(stage, text)->dict, and load stages from learn/stages.json; add a tiny stub of each if missing so your tests pass, clearly marked). "
        "Progress is kept per learner id in a JSON file, a stage unlocks only after predict+bug+write pass, code is revealed only on unlock, writes are "
        "rate limited and the executed learner source is size capped. Tests in tests/test_learn_api.py. Branch devin/learn-api."),
    "LEARN-UI": (
        "YOUR PART (front end, no build step): create client/learn.html, client/learn.js, client/learn.css as a Learn tab linked from the phone app and "
        "the board: stage list with lock icons and an earn-it progress bar, a predict card (user enters the merged quantity; compute the real answer in the "
        "browser using client/crdt.js so it works offline), find-the-bug, write (textarea, run via the API), explain with the rubric shown after, "
        "'reveal the real code' on unlock. Dark theme matching client/style.css, big touch targets, works offline (add files to the service worker "
        "cache list in client/sw.js and bump the cache name). Code against GET /api/learn/stages and POST /api/learn/{stage}/predict|bug|write|explain "
        "(other Devin sessions are building them; mock with a fetch stub in client/test if missing). Add a node test. Branch devin/learn-ui."),
}


def main(env_path: str) -> None:
    env = load_env(env_path)
    ids = json.loads((ROOT / ".devin_ids.json").read_text())
    schema = json.loads((ROOT / "devin/writer.schema.json").read_text())
    client = DevinClient(env["DEVIN_TOKEN"], env["DEVIN_ORG"])
    out = {}
    for role, job in JOBS.items():
        res = client.create_session(f"{COMMON}\n{job}", title=role, tags=["stillcounts", "writer", role.lower()],
                                    max_acu_limit=5, structured_output_schema=schema)
        out[role] = {"session_id": res.get("session_id"), "url": res.get("url")}
        print(role, "->", res.get("url"))
    (ROOT / ".sessions_learn.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
