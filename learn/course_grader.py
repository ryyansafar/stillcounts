"""Plain-code graders for generated courses (stages with "engine": "call", built by learn/make_course.py). No model.

grade_predict  compares each guess with the value the real function returned when the engine ran it.
grade_bug      needs the changed line AND the behaviour it breaks, for a real mutant the repository's tests catch.
grade_write    puts the learner's function into the real module and runs the repository's own tests, in a boxed worker.
grade_explain  checks length, one sentence per rubric point (each needs two of its keywords) and a sentence that quotes the
               learner's own case. It is practice and never unlocks anything.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from learn import grader

ROOT = grader.ROOT
TEST_TIMEOUT = 40.0                     # importing the app and running its tests takes a few seconds
ALLOCATION_GUARD = 1024 * 1024 * 1024   # tracemalloc guard in the worker: pytest and the app import well under this
RSS_LIMIT = 2 * 1024 * 1024 * 1024      # the parent kills the worker past this resident size
FILE_LIMIT = 16 * 1024 * 1024
YES, NO = grader.TRUE_WORDS, grader.FALSE_WORDS


def _limits(seconds: int):
    def apply() -> None:
        try:
            import resource
        except ImportError:
            return
        wanted = [("RLIMIT_CPU", (seconds, seconds + 1)), ("RLIMIT_FSIZE", (FILE_LIMIT, FILE_LIMIT)),
                  ("RLIMIT_NOFILE", (512, 512)), ("RLIMIT_CORE", (0, 0))]
        if sys.platform.startswith("linux"):
            wanted.append(("RLIMIT_AS", (4 * 1024 ** 3, 4 * 1024 ** 3)))
        for name, value in wanted:
            try:
                resource.setrlimit(getattr(resource, name), value)
            except (AttributeError, ValueError, OSError):
                pass
    return apply


def run_worker(req: dict, repo: Path, timeout: float, guard: bool = True) -> dict:
    """Run learn/course_runner.py on req. The result is a dict; a timeout or a memory kill is a failed result."""
    repo = Path(repo).resolve()
    payload = json.dumps({**req, "repo": str(repo), "guard": ALLOCATION_GUARD if guard else 0})
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join([str(ROOT), str(repo)]),
           "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir(),
           "HOME": tempfile.gettempdir()}
    stdout, _, code, status = grader._run_worker([sys.executable, "-m", "learn.course_runner"], payload, timeout, env,
                                                 _limits(int(timeout) + 2) if os.name == "posix" else None, RSS_LIMIT)
    if status == "timeout":
        return {"passed": False, "error": f"timed out after {timeout:g} s", "failed": []}
    if status == "memory":
        return {"passed": False, "error": grader.MEMORY_ERROR, "failed": []}
    lines = stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return {"passed": False, "error": f"the worker stopped without a result (exit {code})", "failed": []}


# ---- predict ----

def coerce(kind: str, guess, choices: list | None = None):
    """The learner's guess as the kind of value the case expects, or ValueError with a plain message."""
    if kind == "bool":
        if isinstance(guess, bool):
            return guess
        if isinstance(guess, str) and guess.strip().lower() in YES | NO:
            return guess.strip().lower() in YES
        raise ValueError("answer yes or no")
    if kind == "int":
        if isinstance(guess, bool):
            raise ValueError("the answer is a whole number")
        if isinstance(guess, int):
            return guess
        if isinstance(guess, str) and re.fullmatch(r"\s*[-+]?\d+\s*", guess):
            return int(guess)
        raise ValueError("the answer is a whole number")
    if not isinstance(guess, str) or not guess.strip():
        raise ValueError("answer with text")
    text = guess.strip().strip("'\"").strip().lower()
    if choices and text not in [str(c).lower() for c in choices]:
        raise ValueError("answer with one of: " + ", ".join(str(c) for c in choices))
    return text


def expected_of(case: dict):
    value = case["expected_value"]
    return value.lower() if case["answer_kind"] == "text" and isinstance(value, str) else value


def grade_case(case: dict, guess) -> dict:
    out = {"passed": False, "error": None}
    try:
        out["passed"] = coerce(case["answer_kind"], guess, case.get("choices")) == expected_of(case)
    except ValueError as exc:
        out["error"] = str(exc)
    return out


def grade_predict(stage: dict, answer, cases: list[dict] | None = None) -> dict:
    if not isinstance(answer, dict):
        return {"passed": False, "cases": {}, "wrong": [], "error": "answer every case: {case id: guess}"}
    results, wrong = {}, []
    for case in cases if cases is not None else stage["predict"]["cases"]:
        if case["id"] not in answer:
            results[case["id"]] = {"passed": False, "error": "no answer for this case"}
            continue
        graded = grade_case(case, answer[case["id"]])
        results[case["id"]] = graded
        if not graded["passed"] and graded["error"] is None:
            wrong.append(case["id"])
    return {"passed": all(r["passed"] for r in results.values()), "cases": results, "wrong": wrong, "error": None}


# ---- find the bug ----

def grade_bug(stage: dict, answer) -> dict:
    bug = stage["bug"]
    if not isinstance(answer, dict):
        return {"passed": False, "error": "click the changed line and pick the cause"}
    line, option = answer.get("line"), str(answer.get("option", "")).strip().lower()
    line_ok = isinstance(line, int) and not isinstance(line, bool) and line == bug["line_index"]
    if not (line_ok and option in (bug["answer"], bug["mutant"])):
        return {"passed": False, "error": "not the changed line and its cause together"}
    return {"passed": True, "answer": bug["answer"], "mutant": bug["mutant"], "explanation": bug["explanation"],
            "counterexample": bug["counterexample"]}


# ---- write ----

def grade_write(stage: dict, code: str, repo: Path = ROOT, timeout: float = TEST_TIMEOUT) -> dict:
    write, src = stage["write"], stage["source"]
    problem = grader._check_code(code, write["function"])
    if problem:
        return {"passed": False, "error": problem, "counterexample": None}
    got = run_worker({"mode": "tests", "module": make_module(src["path"]), "function": write["function"], "source": code,
                      "restricted": True, "names": write["names"], "tests": write["tests"]}, repo, timeout)
    return {"passed": got.get("passed") is True, "error": got.get("error"), "counterexample": None,
            "failed": got.get("failed", []), "graded_by": ", ".join(write["tests"])}


def make_module(path: str) -> str:
    return re.sub(r"\.py$", "", path).replace("/", ".")


# ---- explain ----

def _example_tokens(case: dict) -> set[str]:
    name = case["call"].split("(", 1)[0]
    args = case["call"][len(name):]
    tokens = set(re.findall(r"[a-z0-9]+", args.lower())) | set(re.findall(r"[a-z0-9]+", str(case["expected_value"]).lower()))
    return {t for t in tokens if t not in {"none", "true", "false"} or t == str(case["expected_value"]).lower()}


def _quotes_example(sentence: str, tokens: set[str]) -> bool:
    spoken = set(grader._normalise(sentence).split())
    return len(tokens & spoken) >= min(2, len(tokens))


def _mentioned(sentence: str, keywords: list[str]) -> int:
    text = grader._normalise(sentence)
    return sum(1 for k in keywords if grader._mentions(text, k))


def grade_explain(stage: dict, text: str, cases: list[dict] | None = None) -> dict:
    explain = stage["explain"]
    rubric = explain["rubric"]
    min_words = explain.get("min_words", 25)
    words = len(re.findall(r"[A-Za-z0-9']+", text or ""))
    sentences = grader._sentences(text)
    candidates = {p["id"]: [i for i, s in enumerate(sentences) if _mentioned(s, p["keywords"]) >= min(2, len(p["keywords"]))]
                  for p in rubric}
    pool = cases if cases is not None else stage["predict"].get("cases", [])
    example = next((c for c in pool if c["id"] == explain.get("example_case")), None)
    if example:
        tokens = _example_tokens(example)
        candidates["_example"] = [i for i, s in enumerate(sentences) if _quotes_example(s, tokens)]
    owned = grader._match({k: candidates[k] for k in sorted(candidates, key=lambda k: k != "_example")})
    covered = [{"id": p["id"], "point": p["point"]} for p in rubric if p["id"] in owned]
    missed = [{"id": p["id"], "point": p["point"]} for p in rubric if p["id"] not in owned]
    reasons = []
    if words < min_words:
        reasons.append(f"write at least {min_words} words (you wrote {words})")
    if len(covered) < explain["min_points"]:
        reasons.append(f"cover at least {explain['min_points']} points, each in its own sentence of "
                       f"{grader.MIN_SENTENCE_WORDS} or more words that uses two of its key words (you covered {len(covered)})")
    if example and "_example" not in owned:
        reasons.append(f"give a worked example in its own sentence that quotes your own case {example['id']}: its call and what it returns")
    return {"passed": not reasons, "covered": covered, "missed": missed, "reasons": reasons, "words": words,
            "min_words": min_words, "min_points": explain["min_points"], "score": round(len(covered) / len(rubric), 2) if rubric else 0.0}


GRADERS = {"predict": grade_predict, "bug": grade_bug, "write": grade_write, "explain": grade_explain}


def grade(checkpoint: str, stage: dict, value, cases: list[dict] | None = None, repo: Path = ROOT) -> dict:
    """One entry point for the API: dispatch on the checkpoint."""
    if checkpoint in ("predict", "explain"):
        return GRADERS[checkpoint](stage, value, cases=cases)
    if checkpoint == "write":
        return grade_write(stage, value, repo=repo)
    return grade_bug(stage, value)

