"""Runs a small piece of Devin-written Python in a fresh process and evaluates expressions against it.

Reads one JSON object from stdin: {"source": str, "exprs": [str]}. Writes one JSON line: {"ok": bool, "error": str|None,
"values": [{"ok": bool, "json": str}]}. Standard library only, started with `python -I` by server/live_course.py, which also
applies the CPU, memory and file limits. Plain code, no model in the loop: this is what grades a live stage."""
import ast
import builtins
import json
import sys

ALLOWED_IMPORTS = {"math", "re", "json", "collections", "itertools", "functools", "dataclasses", "typing", "string", "statistics",
                   "decimal", "fractions", "datetime", "operator", "copy", "heapq", "bisect", "enum", "abc", "textwrap"}
BANNED_NAMES = {"__import__", "open", "exec", "eval", "compile", "input", "breakpoint", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "memoryview", "exit", "quit", "help", "dir", "type", "super"}


def problem(source: str, mode: str = "exec") -> str | None:
    try:
        tree = ast.parse(source, mode=mode)
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    return f"import {a.name} is not allowed here"
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                return f"import from {node.module} is not allowed here"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"attribute {node.attr} is not allowed here"
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or (node.id.startswith("__") and node.id != "__name__")):
            return f"name {node.id} is not allowed here"
    return None


def safe_builtins() -> dict:
    out = {k: v for k, v in vars(builtins).items() if k not in BANNED_NAMES}
    real = builtins.__import__

    def limited(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split(".")[0] not in ALLOWED_IMPORTS:
            raise ImportError(f"import {name} is not allowed here")
        return real(name, globals, locals, fromlist, level)

    out["__import__"] = limited
    return out


def dump(value) -> str:
    return json.dumps(value, sort_keys=True, default=repr)


def main() -> None:
    req = json.loads(sys.stdin.read())
    source, exprs = str(req.get("source", "")), [str(e) for e in req.get("exprs", [])]
    bad = problem(source)
    if bad:
        print(json.dumps({"ok": False, "error": bad, "values": []}))
        return
    ns: dict = {"__name__": "stage", "__builtins__": safe_builtins()}
    try:
        exec(compile(source, "<stage>", "exec"), ns)
    except BaseException as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "values": []}))
        return
    values = []
    for e in exprs:
        bad = problem(e, "eval")
        if bad:
            values.append({"ok": False, "json": bad})
            continue
        try:
            values.append({"ok": True, "json": dump(eval(compile(e, "<expr>", "eval"), ns))})
        except BaseException as exc:
            values.append({"ok": False, "json": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"ok": True, "error": None, "values": values}))


if __name__ == "__main__":
    main()
