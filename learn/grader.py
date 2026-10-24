"""Plain-code graders for Earn the Code. No model is ever called.

grade_predict  checks guesses against the real merge, run live on the learner's own cases (learn/cases.py).
grade_bug      checks that the learner clicked the changed line AND named its cause, for a real mutant.
grade_write    runs the learner's function in a subprocess with a timeout, plugs it into the real
               ledger code and judges it with the referee in oracle/, returning the first counterexample.
grade_explain  checks length, one sentence per rubric point and the learner's own counterexample numbers. It is
               practice: server/learn_api.py never lets it unlock a stage.
"""
from __future__ import annotations

import ast
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import tracemalloc
import types
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGES = Path(__file__).resolve().parent / "stages.json"
SOURCE = ROOT / "stillcounts" / "crdt.py"
VECTORS = ROOT / "tests" / "vectors.json"
# The "write" checkpoint runs a stranger's code, so it is boxed in five ways (none is a substitute for running the
# whole server in a container or as an unprivileged user, which the README says to do for a public deployment):
#   1. the source is checked before it runs: no imports, no names or attributes that start with an underscore
#      (except the ledger's own helpers like _order), no string-format attribute tricks, no I/O builtins;
#   2. it runs with a short whitelist of builtins, so `open`, `__import__`, `eval` and friends do not exist;
#   3. the function runs in a fresh namespace holding only ledger symbols (no imported module is reachable);
#   4. the worker process has hard limits on CPU time, written file size, open files and new processes, and on Linux
#      RLIMIT_AS caps its address space at 1 GB. macOS ignores RLIMIT_AS, so that cap is Linux-only;
#   5. on every platform the worker aborts past 256 MB of Python allocations (a tracemalloc watchdog thread) and the
#      parent kills it if its resident memory passes 448 MB (a single huge allocation can finish before a thread in
#      the worker gets to run), plus a wall-clock timeout of 5 s.
SAFE_BUILTINS = ("abs all any bool dict divmod enumerate filter float frozenset int isinstance len list map max min "
                 "next iter print range repr reversed round set slice sorted str sum tuple zip "
                 "ArithmeticError AssertionError Exception IndexError KeyError NotImplementedError "
                 "StopIteration TypeError ValueError ZeroDivisionError").split()
FORBIDDEN_NAMES = frozenset("open exec eval compile input breakpoint globals locals vars dir getattr setattr delattr "
                            "hasattr type object super classmethod staticmethod property memoryview bytearray "
                            "exit quit help license credits copyright id hash callable".split())
FORBIDDEN_ATTRS = frozenset("format format_map mro".split())
FORBIDDEN_ATTR_PREFIXES = ("_", "gi_", "cr_", "ag_", "f_", "tb_", "co_", "func_")
# The only ledger names a learner's function can see. No imported module is among them.
LEARNER_SYMBOLS = ("Op", "merge", "dedupe", "_order", "_total", "_lww", "_validate")
MEMORY_LIMIT = 1024 * 1024 * 1024          # RLIMIT_AS, Linux only
ALLOCATION_LIMIT = 256 * 1024 * 1024       # tracemalloc guard inside the worker, every platform
RSS_LIMIT = 448 * 1024 * 1024              # parent-side kill on resident memory, every platform
DEFAULT_TIMEOUT = 5.0
POLL_SECONDS = 0.02
MEMORY_ERROR = "used more than 256 MB of memory"
TRUE_WORDS = {"true", "yes", "y", "1"}
FALSE_WORDS = {"false", "no", "n", "0"}
_MISSING = object()


@lru_cache(maxsize=1)
def _stages() -> str:
    return STAGES.read_text()


def load_stages() -> list[dict]:
    return json.loads(_stages())


def get_stage(stage_id: int | str) -> dict:
    """A stage by its id ("lamport-lww") or its position (3 or "3")."""
    for s in load_stages():
        if stage_id == s["id"] or str(stage_id) == str(s["order"]):
            return s
    raise KeyError(f"no stage {stage_id!r}")


def public_stages() -> list[dict]:
    """Stages with every answer, rubric and the locked real code removed, safe to send to a learner."""
    out = []
    for s in load_stages():
        s.pop("code")
        for case in s["predict"]["cases"]:
            case.pop("expected")
            case.pop("reroll", None)
        for key in ("mutant", "answer", "explanation", "counterexample", "line_index"):
            s["bug"].pop(key)
        s["write"].pop("hook")
        s["explain"].pop("rubric")
        out.append(s)
    return out


# ---- predict ----

def _case(stage: dict, case_id: str) -> dict:
    for c in stage["predict"]["cases"]:
        if c["id"] == case_id:
            return c
    raise KeyError(f"no predict case {case_id!r} in stage {stage['id']}")


def _coerce(field: str, guess):
    if field == "qty":
        if isinstance(guess, bool):
            raise ValueError("a quantity is a whole number")
        if isinstance(guess, int):
            return guess
        if isinstance(guess, str) and re.fullmatch(r"\s*[-+]?\d+\s*", guess):
            return int(guess)
        raise ValueError("a quantity is a whole number")
    if field in ("alive", "oversold"):
        if isinstance(guess, bool):
            return guess
        if isinstance(guess, str) and guess.strip().lower() in TRUE_WORDS | FALSE_WORDS:
            return guess.strip().lower() in TRUE_WORDS
        raise ValueError("answer yes or no")
    if not isinstance(guess, str):
        raise ValueError("answer with text")
    return guess.strip()


def _grade_case(case: dict, guess) -> dict:
    from stillcounts.crdt import Op, merge, stats

    ops = [Op(**o) for o in case["ops"]]
    state = merge(ops)
    item, field = case["item"], case["field"]
    expected = item in stats(ops)["oversold"] if field == "oversold" else state[item][field]
    result = {"passed": False, "expected": expected, "guess": guess, "state": state, "error": None}
    try:
        result["passed"] = _coerce(field, guess) == expected
    except ValueError as exc:
        result["error"] = str(exc)
    return result


def grade_predict(stage_id: int | str, answer, guess=_MISSING, cases: list[dict] | None = None,
                  reveal: bool = False) -> dict:
    """Run the real merge on a case's operations and compare the learner's guess with it.

    grade_predict(stage, {case_id: guess, ...}, cases=mine) grades the whole checkpoint against this learner's own
    cases (the server passes them; the stock cases are used when none are given). It passes only when every case is
    right and says which cases were wrong, never what the right answer was.
    grade_predict(stage, case_id, guess, reveal=True) grades one stock case and, only because reveal is asked for,
    includes the real answer and merged state. The API never asks.
    """
    stage = get_stage(stage_id)
    if guess is not _MISSING:
        graded = _grade_case(_case(stage, answer), guess)
        if not reveal:
            graded = {k: graded[k] for k in ("passed", "error")}
        return graded
    if not isinstance(answer, dict):
        return {"passed": False, "cases": {}, "wrong": [], "error": "answer every case: {case id: guess}"}
    results, wrong = {}, []
    for c in cases if cases is not None else stage["predict"]["cases"]:
        if c["id"] not in answer:
            results[c["id"]] = {"passed": False, "error": "no answer for this case"}
            continue
        graded = _grade_case(c, answer[c["id"]])
        results[c["id"]] = {"passed": graded["passed"], "error": graded["error"]}
        if not graded["passed"] and graded["error"] is None:
            wrong.append(c["id"])
    return {"passed": all(r["passed"] for r in results.values()), "cases": results, "wrong": wrong, "error": None}


# ---- find the bug ----

def grade_bug(stage_id: int | str, answer) -> dict:
    """The learner must click the changed line AND name the cause: answer = {"option": id, "line": index}.

    A wrong answer says nothing about which half was wrong and reveals nothing; the explanation, the mutant and the
    counterexample come back only with a pass."""
    bug = get_stage(stage_id)["bug"]
    if not isinstance(answer, dict):
        return {"passed": False, "error": "click the changed line and pick the cause"}
    option = str(answer.get("option", "")).strip().lower()
    line = answer.get("line")
    line_ok = isinstance(line, int) and not isinstance(line, bool) and line == bug["line_index"]
    option_ok = option in (bug["answer"], bug["mutant"])
    if not (line_ok and option_ok):
        return {"passed": False, "error": "not the changed line and its cause together"}
    return {"passed": True, "answer": bug["answer"], "mutant": bug["mutant"],
            "explanation": bug["explanation"], "counterexample": bug["counterexample"]}


# ---- write ----

def _unsafe(tree: ast.AST) -> str | None:
    """Return a reason if the source reaches for anything outside the box, else None."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "imports are not allowed: the ledger code you need is already loaded"
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Await, ast.AsyncFunctionDef, ast.Yield, ast.YieldFrom)):
            return "global, nonlocal, async and yield are not allowed here"
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in FORBIDDEN_NAMES):
            return f"the name {node.id!r} is not allowed here"
        if isinstance(node, ast.Attribute) and (node.attr in FORBIDDEN_ATTRS or node.attr.startswith(FORBIDDEN_ATTR_PREFIXES)):
            return f"the attribute {node.attr!r} is not allowed here"
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            return "strings that contain a double underscore are not allowed here"
    return None


def _check_code(code: str, function: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    if [n.name for n in tree.body if isinstance(n, ast.FunctionDef)] != [function] or len(tree.body) != 1 \
            or not isinstance(tree.body[0], ast.FunctionDef):
        return f"write exactly one function named {function}"
    return _unsafe(tree)


def _module(code: str, hook: dict | None) -> types.ModuleType:
    source = SOURCE.read_text()
    if hook:
        if hook["old"] not in source:
            raise RuntimeError("the ledger code changed: the write hook no longer applies")
        source = source.replace(hook["old"], hook["new"], 1)
    module = types.ModuleType("learner_crdt")
    sys.modules["learner_crdt"] = module
    exec(compile(source, "learner_crdt.py", "exec"), module.__dict__)
    import builtins
    # The learner's function runs in a FRESH namespace that holds only the ledger symbols it may call. It never sees the
    # ledger module's own globals (which import json, dataclasses and typing, and through them os and sys), so there is
    # no module object to walk from. The finished function is then plugged into the ledger module by name.
    namespace = {"__builtins__": {name: getattr(builtins, name) for name in SAFE_BUILTINS}}
    for name in LEARNER_SYMBOLS:
        namespace[name] = module.__dict__[name]
    exec(compile(code, "learner.py", "exec"), namespace)
    for name, value in namespace.items():
        if name != "__builtins__" and module.__dict__.get(name) is not value:
            module.__dict__[name] = value
    return module


def _outcome(mod, ops: list[dict]):
    try:
        mops = [mod.Op(**o) for o in ops]
        return {"state": mod.merge(mops), "stats": mod.stats(mops)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _candidates(stage: dict):
    """Small, readable operation lists first, so the first counterexample is easy to follow."""
    from oracle.oracle import generate_ops
    for case in stage["predict"]["cases"]:
        yield case["ops"]
    for s in load_stages():
        if s["id"] != stage["id"]:
            for case in s["predict"]["cases"]:
                yield case["ops"]
    for seed in range(200):
        rng = random.Random(seed)
        ops = [o.__dict__ for o in generate_ops(seed, rng.randint(2, 10), 2)]
        delivered = ops + rng.sample(ops, k=rng.randint(0, len(ops)))
        rng.shuffle(delivered)
        yield [dict(o) for o in delivered]
    for case in json.loads(VECTORS.read_text()):
        yield case["ops"]


def _counterexample(mod, stage: dict) -> dict | None:
    import stillcounts.crdt as real
    for ops in _candidates(stage):
        expected, got = _outcome(real, ops), _outcome(mod, ops)
        if expected != got:
            return {"ops": ops, "expected": expected, "got": got}
    return None


def _write_worker(stage_id: int | str, code: str) -> dict:
    from oracle.mutation import referee_failure

    stage = get_stage(stage_id)
    try:
        mod = _module(code, stage["write"]["hook"])
    except Exception as exc:
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}", "counterexample": None}
    counterexample = _counterexample(mod, stage)
    failure = referee_failure(mod)
    if counterexample and not failure:
        failure = "the result differs from the real merge"
    if counterexample and "error" in counterexample["got"] and failure.startswith("crashed"):
        failure = counterexample["got"]["error"]
    return {"passed": failure is None, "error": failure, "counterexample": counterexample}


def _limit_resources(seconds: int):
    """Hard limits for the worker process (POSIX only). Each limit is applied on its own: one the OS refuses must not
    stop the others."""
    def apply() -> None:
        try:
            import resource
        except ImportError:
            return
        limits = [("RLIMIT_CPU", (seconds, seconds + 1)), ("RLIMIT_AS", (MEMORY_LIMIT, MEMORY_LIMIT)),
                  ("RLIMIT_FSIZE", (0, 0)), ("RLIMIT_NPROC", (0, 0)), ("RLIMIT_NOFILE", (32, 32)),
                  ("RLIMIT_CORE", (0, 0))]
        for name, value in limits:
            try:
                resource.setrlimit(getattr(resource, name), value)
            except (AttributeError, ValueError, OSError):
                pass
    return apply


def _rss_bytes(pid: int) -> int:
    """Resident memory of a process, or 0 when it cannot be read (it has exited, or the platform hides it)."""
    try:
        with open(f"/proc/{pid}/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout
        return int(out.strip() or 0) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _run_worker(cmd: list[str], payload: str, timeout: float, env: dict, preexec) -> tuple[str, str, int, str]:
    """Run the worker and watch it: kill it on a timeout or when its resident memory passes RSS_LIMIT.
    Returns (stdout, stderr, returncode, status) where status is "ok", "timeout" or "memory"."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=ROOT, env=env, preexec_fn=preexec)
    deadline = time.monotonic() + timeout
    data = payload
    while True:
        try:
            out, err = proc.communicate(data, timeout=POLL_SECONDS)
            return out, err, proc.returncode, "ok"
        except subprocess.TimeoutExpired:
            data = None
        status = "memory" if _rss_bytes(proc.pid) > RSS_LIMIT else "timeout" if time.monotonic() > deadline else None
        if status:
            proc.kill()
            out, err = proc.communicate()
            return out or "", err or "", proc.returncode, status


def grade_write(stage_id: int | str, code: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Plug the learner's function into the real ledger code and judge it with the referee, in a subprocess."""
    stage = get_stage(stage_id)
    problem = _check_code(code, stage["write"]["function"])
    if problem:
        return {"passed": False, "error": problem, "counterexample": None}
    stdout, _, returncode, status = _run_worker(
        [sys.executable, "-m", "learn.grader", "--write-worker", str(stage["id"])], json.dumps({"code": code}), timeout,
        {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        _limit_resources(int(timeout) + 1) if os.name == "posix" else None)
    if status == "timeout":
        return {"passed": False, "error": f"timed out after {timeout:g} s", "counterexample": None}
    if status == "memory":
        return {"passed": False, "error": MEMORY_ERROR, "counterexample": None}
    lines = stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        reason = "the function stopped the grader" if returncode else "no result"
        return {"passed": False, "error": f"{reason} (exit {returncode})", "counterexample": None}


# ---- explain ----

MIN_SENTENCE_WORDS = 5          # a sentence this short cannot carry a rubric point
SENTENCE_BREAK = re.compile(r"[.!?;\n]+")


def _normalise(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def _mentions(text: str, keyword: str) -> bool:
    phrase = _normalise(keyword.rstrip("*")).strip()
    if not phrase:
        return False
    if keyword.endswith("*"):
        return f" {phrase}" in text
    return f" {phrase} " in text


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in SENTENCE_BREAK.split(text or "") if len(re.findall(r"[A-Za-z0-9]+", p)) >= MIN_SENTENCE_WORDS]


def _match(candidates: dict[str, list[int]]) -> dict[str, int]:
    """Give each rubric point its own sentence (augmenting paths; there are at most a handful of points)."""
    owner: dict[int, str] = {}

    def place(point: str, seen: set[int]) -> bool:
        for i in candidates[point]:
            if i in seen:
                continue
            seen.add(i)
            if i not in owner or place(owner[i], seen):
                owner[i] = point
                return True
        return False

    for point in candidates:
        place(point, set())
    return {point: i for i, point in owner.items()}


def _example_case(stage: dict, cases: list[dict] | None) -> dict | None:
    wanted = stage["explain"].get("example_case")
    pool = cases if cases is not None else stage["predict"]["cases"]
    return next((c for c in pool if c["id"] == wanted), None)


def _quotes_example(sentence: str, numbers: set[int]) -> bool:
    quoted = {int(n) for n in re.findall(r"\d+", sentence)}
    return len(quoted & numbers) >= min(2, len(numbers))


def grade_explain(stage_id: int | str, text: str, cases: list[dict] | None = None) -> dict:
    """A rubric point is covered when one sentence of its own mentions any of its keywords (a trailing * matches a
    stem). The answer must also be long enough, and where the rubric asks for a worked example the same sentence must
    quote numbers from the learner's own case. cases are this learner's predict cases; the stock ones are used when
    none are given. Explain is practice: nothing here unlocks a stage."""
    from learn.cases import example_numbers

    stage = get_stage(stage_id)
    explain = stage["explain"]
    min_words = explain.get("min_words", 25)
    words = len(re.findall(r"[A-Za-z0-9']+", text or ""))
    example = _example_case(stage, cases)
    numbers = example_numbers(example) if example else set()
    sentences = _sentences(text)
    candidates = {}
    for p in explain["rubric"]:
        hits = [i for i, sent in enumerate(sentences)
                if any(_mentions(_normalise(sent), k) for k in p["keywords"])
                and (not p.get("example") or not numbers or _quotes_example(sent, numbers))]
        candidates[p["id"]] = hits
    required_first = {p["id"]: candidates[p["id"]] for p in sorted(explain["rubric"], key=lambda p: not p.get("example"))}
    owned = _match(required_first)
    covered, missed, reasons = [], [], []
    for p in explain["rubric"]:
        entry = {"id": p["id"], "point": p["point"]}
        if p.get("example") and example:
            entry["needs"] = f"quote numbers from your own case {example['id']}"
        (covered if p["id"] in owned else missed).append(entry)
    total = len(explain["rubric"])
    if words < min_words:
        reasons.append(f"write at least {min_words} words (you wrote {words})")
    if len(covered) < explain["min_points"]:
        reasons.append(f"cover at least {explain['min_points']} points, each in its own sentence of {MIN_SENTENCE_WORDS} "
                       f"or more words (you covered {len(covered)})")
    for p in explain["rubric"]:
        if p.get("example") and p["id"] not in owned:
            reasons.append(f"give a worked example with numbers from your own case {explain.get('example_case')}")
    return {"passed": not reasons, "covered": covered, "missed": missed, "reasons": reasons, "words": words,
            "min_words": min_words, "score": round(len(covered) / total, 2) if total else 0.0,
            "min_points": explain["min_points"]}


def _start_memory_guard(emit, limit: int = ALLOCATION_LIMIT) -> threading.Thread:
    """Abort the worker, with a clean failed result, once Python has allocated more than limit bytes. A thread can only
    run between bytecodes, so one giant allocation can slip past it: the parent's RSS check is the backstop."""
    tracemalloc.start()

    def watch() -> None:
        while True:
            time.sleep(0.005)
            if tracemalloc.get_traced_memory()[0] > limit:
                emit({"passed": False, "error": MEMORY_ERROR, "counterexample": None})
                os._exit(0)

    thread = threading.Thread(target=watch, daemon=True, name="memory-guard")
    thread.start()
    return thread


def _main(argv: list[str]) -> None:
    if len(argv) == 2 and argv[0] == "--write-worker":
        code = json.loads(sys.stdin.read())["code"]
        out = sys.stdout
        sys.stdout = sys.stderr                       # the learner's prints must not corrupt the result

        def emit(result: dict) -> None:
            out.write("\n" + json.dumps(result, default=repr) + "\n")
            out.flush()

        _start_memory_guard(emit)
        emit(_write_worker(argv[1], code))
        return
    print(json.dumps(public_stages(), indent=1))


if __name__ == "__main__":
    _main(sys.argv[1:])
