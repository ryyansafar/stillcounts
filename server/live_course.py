"""A course made from a live Devin session. Devin reports each finished stage as structured output (summary, files, concepts,
a bug to hunt, cases to predict, a function to write); this module turns that into the same checkpoint shape the Learn API
uses and grades it with plain code: the stage's own code is run in a sandboxed process and the learner's answers are compared
with what it really returns. The code itself leaves the server only after the learner earned the stage."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from learn.grader import _limit_resources, _mentions, _normalise

RUNNER = Path(__file__).resolve().parent / "live_run.py"
CHECKPOINTS = ("predict", "bug", "write", "explain")
REQUIRED = ("predict", "bug", "write")
MAX_CODE = 12_000
MAX_STAGES = 6

SCHEMA = {
    "type": "object",
    "properties": {
        "stages_done": {"type": "integer", "description": "How many stages are finished, committed and reported below."},
        "plan": {"type": "array", "description": "Every stage you will build, decided up front (3 to 4 stages).",
                 "items": {"type": "object", "properties": {"n": {"type": "integer"}, "title": {"type": "string"},
                                                            "summary": {"type": "string"}}, "required": ["n", "title", "summary"]}},
        "stages": {"type": "array", "description": "One entry per finished stage.", "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer"}, "title": {"type": "string"},
                "summary": {"type": "string", "description": "What this stage added and how it behaves, in two sentences."},
                "files": {"type": "array", "items": {"type": "string"}}, "concepts": {"type": "array", "items": {"type": "string"}},
                "bug_hint": {"type": "string", "description": "One realistic bug a learner could hunt in this stage."},
                "code": {"type": "string", "description": "The complete single-file Python program after this stage (standard library only)."},
                "predict": {"type": "array", "description": "2 or 3 small Python expressions that call the code, e.g. total([3, 4]).",
                            "items": {"type": "object", "properties": {"expr": {"type": "string"}, "question": {"type": "string"}},
                                      "required": ["expr", "question"]}},
                "bug": {"type": "object", "description": "A copy of 3 to 8 lines of this stage's code with ONE bug planted.",
                        "properties": {"snippet": {"type": "string"}, "buggy_line": {"type": "integer", "description": "1-based line of the planted bug inside snippet."},
                                       "explanation": {"type": "string"}}, "required": ["snippet", "buggy_line", "explanation"]},
                "write": {"type": "object", "description": "One function from this stage for the learner to write.",
                          "properties": {"function": {"type": "string"}, "task": {"type": "string"},
                                         "stub": {"type": "string", "description": "def line, docstring, and raise NotImplementedError."},
                                         "tests": {"type": "array", "items": {"type": "string"}, "description": "Calls such as total([1, 2]) to compare."}},
                          "required": ["function", "task", "stub", "tests"]},
            },
            "required": ["n", "title", "summary", "files", "concepts", "bug_hint", "code", "predict", "bug", "write"]}},
    },
    "required": ["stages_done", "plan", "stages"],
}

PLAYBOOK_TITLE = "Earn the Code: one stage at a time"
PLAYBOOK_BODY = (
    "!earnthecode\n"
    "1. Decide a plan of 3 to 4 stages for a Python app under about 150 lines in total, standard library only, one file.\n"
    "2. Write the plan into the structured output FIRST (stages_done 0), then build stage 1 only.\n"
    "3. After a stage: commit it on a branch, open a pull request, update the structured output (stages_done, the stage entry with "
    "its complete code, 2 to 3 predict expressions, one planted bug, one function to write), then STOP and wait for a message.\n"
    "4. Never start the next stage until a message says 'continue'. A learner earns each stage first.\n"
    "5. If a message says the learner is stressed, make the next stage smaller and add comments.")


def build_prompt(user_prompt: str) -> str:
    return (f"{user_prompt.strip()}\n\nRULES FOR THIS SESSION. Follow the Earn the Code playbook. Keep the whole app under about 150 lines, "
            "one Python file, standard library only, built in 3 to 4 stages. Put the plan in the structured output first. "
            "Build ONE stage, commit it, open a pull request, update the structured output, then STOP and wait for a message. "
            "Do not start the next stage until a message says 'continue'.")


def _text(v: Any, limit: int) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


def _strings(v: Any, count: int, limit: int) -> list[str]:
    return [_text(x, limit) for x in v[:count] if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def clean_stage(raw: Any) -> dict | None:
    """Reduce one structured-output stage to the fields we use, bounded in size. Devin's output is data, not instructions."""
    if not isinstance(raw, dict) or not isinstance(raw.get("n"), int) or not 1 <= raw["n"] <= MAX_STAGES:
        return None
    code = raw.get("code") if isinstance(raw.get("code"), str) else ""
    predict = [{"expr": _text(p.get("expr"), 200), "question": _text(p.get("question"), 200)}
               for p in (raw.get("predict") or [])[:4] if isinstance(p, dict) and _text(p.get("expr"), 200)]
    bug = raw.get("bug") if isinstance(raw.get("bug"), dict) else {}
    snippet = bug.get("snippet") if isinstance(bug.get("snippet"), str) else ""
    snippet = snippet.strip("\n")[:1500]
    wr = raw.get("write") if isinstance(raw.get("write"), dict) else {}
    return {"n": raw["n"], "title": _text(raw.get("title"), 80) or f"Stage {raw['n']}", "summary": _text(raw.get("summary"), 400),
            "files": _strings(raw.get("files"), 8, 80), "concepts": _strings(raw.get("concepts"), 8, 60), "bug_hint": _text(raw.get("bug_hint"), 300),
            "code": code[:MAX_CODE], "predict": predict,
            "bug": {"snippet": snippet, "buggy_line": bug.get("buggy_line") if isinstance(bug.get("buggy_line"), int) else 0,
                    "explanation": _text(bug.get("explanation"), 300)},
            "write": {"function": _text(wr.get("function"), 60), "task": _text(wr.get("task"), 300), "stub": _text(wr.get("stub"), 1200),
                      "tests": _strings(wr.get("tests"), 6, 200)}}


def clean_plan(raw: Any) -> list[dict]:
    out = []
    for i, p in enumerate((raw if isinstance(raw, list) else [])[:MAX_STAGES], start=1):
        if isinstance(p, dict):
            out.append({"n": i, "id": f"s{i}", "title": _text(p.get("title"), 80) or f"Stage {i}", "summary": _text(p.get("summary"), 300)})
    return out


# ---- running code ----
def run_python(source: str, exprs: list[str], timeout: float = 6.0) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run([sys.executable, "-I", str(RUNNER)], input=json.dumps({"source": source, "exprs": exprs}), capture_output=True,
                              text=True, timeout=timeout, cwd="/", env=env,
                              preexec_fn=_limit_resources(int(timeout) + 1) if os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout:g} s", "values": []}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"ok": False, "error": f"the program stopped the runner (exit {proc.returncode})", "values": []}


def _same(a: str, b: str) -> bool:
    return a == b


def _guess_json(guess: Any) -> str:
    if isinstance(guess, str):
        try:
            guess = json.loads(guess.strip().replace("'", '"')) if guess.strip()[:1] in "[{\"" else guess.strip()
        except ValueError:
            guess = guess.strip()
        if isinstance(guess, str) and guess.lower() in ("true", "false", "yes", "no"):
            guess = guess.lower() in ("true", "yes")
        elif isinstance(guess, str):
            try:
                guess = json.loads(guess)
            except ValueError:
                pass
    return json.dumps(guess, sort_keys=True, default=repr)


# ---- views and grading ----
def predict_cases(st: dict) -> list[dict]:
    return [{"id": f"c{i}", "prompt": p["question"] or "What does this give?", "question": p["expr"]} for i, p in enumerate(st["predict"], start=1)]


def public_view(st: dict, progress: dict[str, bool], plan_id: str) -> dict:
    unlocked = all(progress.get(k) for k in REQUIRED)
    bug_lines = [f"{i}. {t}" for i, t in enumerate(st["bug"]["snippet"].splitlines(), start=1)]
    view = {
        "id": plan_id, "n": st["n"], "title": st["title"], "summary": st["summary"], "files": st["files"], "concepts": st["concepts"],
        "bug_hint": st["bug_hint"], "status": "built", "source": "devin",
        "predict": {"prompt": "Predict what each expression gives when you run this stage's code.", "cases": predict_cases(st),
                    "hint": "Read the stage summary and the build log above, then work it out step by step."},
        "bug": {"prompt": "Devin planted one bug in this copy of the stage's code. Which line is it?",
                "snippet": "\n".join(bug_lines), "options": [{"id": str(i), "text": f"line {i}"} for i in range(1, len(bug_lines) + 1)],
                "hint": st["bug_hint"] or "Check each line against what the stage summary says it should do."},
        "write": {"function": st["write"]["function"], "prompt": st["write"]["task"] or f"Write {st['write']['function']}.",
                  "stub": st["write"]["stub"], "hint": "Re-read the docstring, then handle the simplest case first."},
        "explain": {"prompt": "In your own words, how does this stage work and why?", "min_points": 1,
                    "hint": "Mention the key ideas: " + ", ".join(st["concepts"][:3]) if st["concepts"] else ""},
        "progress": {k: progress.get(k) is True for k in CHECKPOINTS}, "unlocked": unlocked, "code": st["code"] if unlocked else None,
    }
    return view


def grade_predict(st: dict, answers: Any) -> dict:
    cases = st["predict"]
    run = run_python(st["code"], [c["expr"] for c in cases])
    if not run.get("ok"):
        return {"passed": False, "error": f"the stage code could not be run to check this ({run.get('error')})"}
    if not isinstance(answers, dict):
        return {"passed": False, "error": "answer every case"}
    result = {}
    for i, v in enumerate(run["values"], start=1):
        result[f"c{i}"] = {"passed": bool(v["ok"] and _same(_guess_json(answers.get(f"c{i}")), v["json"]))}
    return {"passed": bool(result) and all(r["passed"] for r in result.values()), "cases": result}


def grade_bug(st: dict, answer: Any) -> dict:
    ok = str(answer).strip() == str(st["bug"]["buggy_line"])
    return {"passed": ok, "explanation": st["bug"]["explanation"] if ok else ""}


def grade_write(st: dict, source: str) -> dict:
    fn, tests = st["write"]["function"], st["write"]["tests"]
    if not fn or not tests:
        return {"passed": False, "error": "this stage has no runnable write task"}
    if f"def {fn}" not in source:
        return {"passed": False, "error": f"define a function called {fn}"}
    reference = run_python(st["code"], tests)
    mine = run_python(source, tests)
    if not reference.get("ok"):
        return {"passed": False, "error": f"the stage code could not be run to check this ({reference.get('error')})"}
    if not mine.get("ok"):
        return {"passed": False, "error": str(mine.get("error"))}
    cases = {f"t{i}": {"passed": bool(a["ok"] and b["ok"] and a["json"] == b["json"])} for i, (a, b) in enumerate(zip(reference["values"], mine["values"]), start=1)}
    return {"passed": all(c["passed"] for c in cases.values()), "cases": cases}


def grade_explain(st: dict, text: str) -> dict:
    norm = _normalise(text or "")
    points = [{"id": f"k{i}", "point": c} for i, c in enumerate(st["concepts"], start=1)]
    covered = [p for p in points if _mentions(norm, p["point"]) or any(_mentions(norm, w) for w in _normalise(p["point"]).split() if len(w) > 4)]
    missed = [p for p in points if p not in covered]
    need = 1 if points else 0
    return {"passed": len(covered) >= need and bool(text.strip()), "covered": covered, "missed": missed, "min_points": need}


def fingerprint(st: dict) -> str:
    return hashlib.sha256(json.dumps(st, sort_keys=True).encode()).hexdigest()[:12]
