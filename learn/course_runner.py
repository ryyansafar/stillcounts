"""The worker process of the course engine. One JSON request on stdin, one JSON result on the last line of stdout.

  mode "calls"  run a function (the real one and any number of replacements of it) on a list of argument lists and
                report what each call returned or raised, plus what each "view" expression observes in the result.
  mode "tests"  put a replacement of the function into the real module and run the repository's own tests against it.

The replacement is either trusted (a mutant the engine made itself, run with the module's full globals) or restricted
(a learner's code, run with a short builtin whitelist and only the plain values the original function itself uses, never a
module). The caller is learn/course_grader.py, which boxes the process in (limits, timeout, a source check)."""
from __future__ import annotations

import ast
import importlib
import json
import os
import signal
import sys
import types

from learn.grader import SAFE_BUILTINS, _start_memory_guard

CALL_SECONDS = 1.0
EXTRA_BUILTINS = ("ord", "chr", "bytes", "pow", "hex", "bin")


def _safe_builtins() -> dict:
    import builtins
    return {name: getattr(builtins, name) for name in (*SAFE_BUILTINS, *EXTRA_BUILTINS)}


def _load(repo: str, module: str) -> types.ModuleType:
    os.chdir(repo)
    sys.path.insert(0, repo)
    return importlib.import_module(module)


def _install(mod: types.ModuleType, function: str, source: str | None, restricted: bool, names: list[str]) -> None:
    if source is None:
        return
    if restricted:
        space: dict = {"__builtins__": _safe_builtins()}
        for name in names:
            if name in mod.__dict__ and not isinstance(mod.__dict__[name], types.ModuleType):
                space[name] = mod.__dict__[name]
        exec(compile(source, "learner.py", "exec"), space)
        mod.__dict__[function] = space[function]
    else:
        exec(compile(source, f"{function}_variant.py", "exec"), mod.__dict__)


class _Timeout(Exception):
    pass


def _alarm(_signum, _frame) -> None:
    raise _Timeout("took longer than one second")


def _run_one(fn, args_src: str, views: list[dict]) -> dict:
    out: dict = {"call": args_src, "raw": None, "error": None, "views": {}}
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, CALL_SECONDS)
    try:
        args = eval(f"({args_src},)", {"__builtins__": {}})
        result = fn(*args)
        out["raw"] = repr(result)
        if " at 0x" in out["raw"]:
            out["raw"] = None
        for view in views:
            out["views"][view["id"]] = repr(eval(view["observe"], {"__builtins__": __builtins__}, {"result": result}))
    except BaseException as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return out


def run_calls(req: dict) -> dict:
    mod = _load(req["repo"], req["module"])
    original = mod.__dict__[req["function"]]
    results = {}
    for name, source in req["variants"].items():
        try:
            _install(mod, req["function"], source, False, [])
            results[name] = [_run_one(mod.__dict__[req["function"]], a, req["views"]) for a in req["calls"]]
        except BaseException as exc:
            results[name] = [{"call": a, "raw": None, "error": f"{type(exc).__name__}: {exc}"[:200], "views": {}}
                             for a in req["calls"]]
        finally:
            mod.__dict__[req["function"]] = original
    return {"results": results}


class _Collector:
    def __init__(self) -> None:
        self.failed: list[dict] = []

    def pytest_runtest_logreport(self, report) -> None:
        if report.failed and len(self.failed) < 3:
            text = report.longreprtext.strip().splitlines()
            reason = next((ln.strip() for ln in reversed(text) if ln.strip().startswith(("E ", "E\t"))), text[-1] if text else "")
            self.failed.append({"test": report.nodeid, "reason": reason[:240]})


def run_tests(req: dict) -> dict:
    import pytest
    mod = _load(req["repo"], req["module"])
    try:
        _install(mod, req["function"], req.get("source"), bool(req.get("restricted")), req.get("names", []))
    except BaseException as exc:
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}", "failed": []}
    collector = _Collector()
    code = pytest.main(["-q", "-x", "--tb=short", "-p", "no:cacheprovider", "-p", "no:randomly", *req["tests"]],
                       plugins=[collector])
    if code == 0:
        return {"passed": True, "error": None, "failed": []}
    first = collector.failed[0] if collector.failed else None
    error = f"{first['test'].split('::')[-1]}: {first['reason']}" if first else f"the tests did not run cleanly (pytest exit {int(code)})"
    return {"passed": False, "error": error, "failed": collector.failed}


def main() -> None:
    req = json.loads(sys.stdin.read())
    if "file" in req:
        with open(req["file"], encoding="utf-8") as fh:
            req = json.load(fh)
    out = sys.stdout
    sys.stdout = sys.stderr                      # nothing the code under test prints may corrupt the result line

    def emit(result: dict) -> None:
        out.write("\n" + json.dumps(result, default=repr) + "\n")
        out.flush()

    if req.get("guard"):
        _start_memory_guard(lambda r: emit({**r, "failed": []}), limit=int(req["guard"]))
    emit(run_calls(req) if req["mode"] == "calls" else run_tests(req))


if __name__ == "__main__":
    main()
