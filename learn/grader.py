"""Plain-code graders for Earn the Code. No model is ever called.

grade_predict  checks a guess against the real merge, run live.
grade_bug      checks which description of a real mutant the learner chose.
grade_write    runs the learner's function in a subprocess with a timeout, plugs it into the real
               ledger code and judges it with the referee in oracle/, returning the first counterexample.
grade_explain  checks rubric keyword coverage.
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
        for key in ("mutant", "answer", "explanation", "counterexample"):
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


def grade_predict(stage_id: int | str, answer, guess=_MISSING) -> dict:
    """Run the real merge on a case's operations and compare the learner's guess with it.

    grade_predict(stage, case_id, guess) grades one case.
    grade_predict(stage, {case_id: guess, ...}) grades the whole checkpoint: it passes only when every case is right.
    """
    stage = get_stage(stage_id)
    if guess is not _MISSING:
        return _grade_case(_case(stage, answer), guess)
    if not isinstance(answer, dict):
        return {"passed": False, "cases": {}, "error": "answer every case: {case id: guess}"}
    cases = {}
    for c in stage["predict"]["cases"]:
        if c["id"] in answer:
            cases[c["id"]] = _grade_case(c, answer[c["id"]])
        else:
            cases[c["id"]] = {"passed": False, "error": "no answer for this case"}
    return {"passed": all(r["passed"] for r in cases.values()), "cases": cases, "error": None}


# ---- find the bug ----

def grade_bug(stage_id: int | str, choice: str) -> dict:
    bug = get_stage(stage_id)["bug"]
    picked = str(choice).strip().lower()
    passed = picked in (bug["answer"], bug["mutant"])
    return {"passed": passed, "answer": bug["answer"], "mutant": bug["mutant"],
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


def grade_explain(stage_id: int | str, text: str) -> dict:
    """A rubric point is covered when the answer mentions any of its keywords (a trailing * matches a stem)."""
    explain = get_stage(stage_id)["explain"]
    norm = _normalise(text or "")
    covered, missed = [], []
    for p in explain["rubric"]:
        entry = {"id": p["id"], "point": p["point"]}
        (covered if any(_mentions(norm, k) for k in p["keywords"]) else missed).append(entry)
    total = len(explain["rubric"])
    return {"passed": len(covered) >= explain["min_points"], "covered": covered, "missed": missed,
            "score": round(len(covered) / total, 2) if total else 0.0, "min_points": explain["min_points"]}


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
