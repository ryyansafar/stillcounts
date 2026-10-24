"""The course engine. Point it at a repository and some functions (or a commit range) and it builds Earn the Code stages:

  predict  example calls are RUN against the real function; the answers are what it really returned
  bug      the function is mutated automatically; a mutant that the repository's own tests catch becomes the bug, and the
           options are real behaviours observed by running the other mutants
  write    the body is removed; the learner's version is put into the real module and graded by the repository's own tests
  explain  a rubric made from the docstring, the comments, the reasons it returns and the commit range's diff

    python -m learn.make_course --spec learn/courses/validate.spec.json --out learn/courses/validate.json --repo .

Everything the engine claims is checked by running code. A function whose mutants the tests never catch is refused, because
a write task graded by tests that cannot fail would pass anything."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from learn import course_grader, course_inputs, course_rubric, mutate

POOL_SIZE = 9
QUIZ_SIZE = 6
MIN_CASES = 8
SHOW = 3
MAX_TESTED = 16
BUILD_TIMEOUT = 120.0
KIND_RANK = {"compare": 0, "boolean": 1, "negation": 2, "constant": 3, "arithmetic": 4, "identity": 5, "membership": 6}
DEFAULT_VIEW = {"id": "value", "question": "What does it return?", "observe": "result"}


class CourseError(ValueError):
    pass


# ---- finding code ----

def parse_target(target: str) -> tuple[str, str]:
    path, sep, name = str(target).partition(":")
    if not sep or not path.endswith(".py") or not name.isidentifier():
        raise CourseError(f"a function is named like path/to/module.py:function, not {target!r}")
    return path, name


def module_name(path: str) -> str:
    return course_grader.make_module(path)


def _function_node(source: str, name: str) -> ast.FunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise CourseError(f"no module-level function {name!r} here")


def function_source(source: str, name: str) -> str:
    node = _function_node(source, name)
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    return "\n".join(source.splitlines()[first - 1:node.end_lineno]) + "\n"


def free_names(node: ast.FunctionDef) -> list[str]:
    """Names the function reads that it does not bind itself: the module-level values it depends on."""
    import builtins
    bound = {a.arg for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs, node.args.vararg, node.args.kwarg] if a}
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    loaded = [n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
    return sorted({n for n in loaded if n not in bound and not hasattr(builtins, n)})


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if done.returncode:
        raise CourseError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def functions_from_range(repo: Path, commit_range: str) -> list[dict]:
    """Module-level functions of non-test Python files that a commit range touches, each with the diff hunks that hit it."""
    repo = Path(repo)
    end = commit_range.split("..")[-1] or "HEAD"
    files = [f for f in _git(repo, "diff", "--name-only", commit_range, "--", "*.py").split() if not re.search(r"(^|/)(tests?/|test_)", f)]
    out = []
    for rel in files:
        try:
            tree = ast.parse(_git(repo, "show", f"{end}:{rel}"))
        except CourseError:
            continue
        hunks = re.split(r"(?m)^(?=@@)", _git(repo, "diff", "-U0", commit_range, "--", rel))[1:]
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            mine = []
            for hunk in hunks:
                m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", hunk)
                start, count = int(m.group(1)), int(m.group(2) or 1)
                if start <= node.end_lineno and start + max(count, 1) - 1 >= node.lineno:
                    mine.append(hunk)
            if mine:
                out.append({"target": f"{rel}:{node.name}", "diff": "".join(mine)})
    return out


# ---- running code ----

def run_tests(repo: Path, path: str, function: str, source: str | None, tests: list[str], restricted: bool = True,
              names: list[str] | None = None, timeout: float = BUILD_TIMEOUT) -> dict:
    return course_grader.run_worker({"mode": "tests", "module": module_name(path), "function": function, "source": source,
                                     "restricted": restricted, "names": names or [], "tests": tests}, repo, timeout, guard=False)


def run_calls(repo: Path, path: str, function: str, variants: dict, calls: list[str], views: list[dict]) -> dict:
    got = course_grader.run_worker({"mode": "calls", "module": module_name(path), "function": function, "variants": variants,
                                    "calls": calls, "views": views}, repo, BUILD_TIMEOUT, guard=False)
    if "results" not in got:
        raise CourseError(f"could not run {function}: {got.get('error')}")
    return got["results"]


# ---- predict ----

def _kind_of(values: list) -> str:
    if all(isinstance(v, bool) for v in values):
        return "bool"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "int"
    if all(isinstance(v, str) for v in values):
        return "text"
    raise CourseError("a view must observe yes/no, whole-number or text answers: add a view with an `observe` expression")


def _spread(groups: dict[str, list], want: int) -> list:
    """Round-robin across groups of cases that share an answer, so the pool has variety."""
    out = []
    while len(out) < want and any(groups.values()):
        for key in list(groups):
            if groups[key] and len(out) < want:
                out.append(groups[key].pop(0))
    return out


def _cases(name: str, calls: list[str], base: list, views: list[dict]) -> list[dict]:
    """One candidate per (view, call) where the real function answered: the answer is what it returned."""
    cases = []
    for view in views:
        rows = [(c, r["views"][view["id"]]) for c, r in zip(calls, base) if r["error"] is None and view["id"] in r["views"]]
        try:
            values = [ast.literal_eval(v) for _, v in rows]
        except (ValueError, SyntaxError):
            raise CourseError(f"view {view['id']!r} of {name} must observe a plain value (yes/no, number or text)") from None
        if not values:
            continue
        kind = _kind_of(values)
        for (call, shown), value in zip(rows, values):
            case = {"call": f"{name}({call})", "prompt": f"Run {name}({call}).", "question": view["question"],
                    "answer_kind": kind, "expected": shown, "expected_value": value, "view": view["id"]}
            if view.get("choices"):
                case["choices"] = list(view["choices"])
            cases.append(case)
    return cases


def _predict(name: str, calls: list[str], base: list, views: list[dict]) -> dict:
    cases = _cases(name, calls, base, views)
    if len(cases) < MIN_CASES:
        raise CourseError(f"only {len(cases)} usable example calls for {name}; list more inputs")
    groups: dict[str, list] = {}
    for c in cases:                       # listed inputs come before perturbations, so they are preferred inside a group
        groups.setdefault(f"{c['view']}|{json.dumps(c['expected_value'])}", []).append(c)
    picked = _spread(groups, POOL_SIZE + QUIZ_SIZE)
    split = min(POOL_SIZE, len(picked) - 2)              # at least two quiz questions are left over
    pool = [{"id": f"c{i + 1}", **c} for i, c in enumerate(picked[:split])]
    quiz = [{"id": f"q{i + 1}", **c} for i, c in enumerate(picked[split:])]
    stock = [{**c, "id": f"p{i + 1}"} for i, c in enumerate(pool[:SHOW])]
    return {"prompt": f"Work out what the real {name} returns for each call. The engine ran the code to get the answers.",
            "show": SHOW, "pool": pool, "quiz_pool": quiz, "cases": stock}


# ---- find the bug ----

def _same(a: dict, b: dict) -> bool:
    return a["error"] == b["error"] and a["raw"] == b["raw"] and a["views"] == b["views"]


def _show(row: dict) -> str:
    if row["error"]:
        return "fails with " + row["error"]
    return (row["raw"] if row["raw"] is not None else json.dumps(row["views"]))[:110]


def _difference(call_src: str, name: str, real: dict, mutant: dict) -> dict:
    return {"args": call_src, "call": f"{name}({call_src})", "expected": _show(real), "got": _show(mutant),
            "crashes": bool(mutant["error"])}


def _differences(name: str, calls: list[str], base: list, other: list) -> list[dict]:
    """Every call on which a variant behaves differently from the real function; value changes before crashes."""
    diffs = [{**_difference(c, name, a, b), "at": i} for i, (c, a, b) in enumerate(zip(calls, base, other)) if not _same(a, b)]
    return [d for d in diffs if not d["crashes"]] + [d for d in diffs if d["crashes"]]


def _spread_indices(n: int, k: int) -> list[int]:
    return list(range(n)) if n <= k else sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _excerpt(source: str, line: int) -> tuple[list[str], int]:
    """Two code lines either side of the changed line, never the whole function. Returns (lines, index of the changed line)."""
    import textwrap
    lines = source.splitlines()
    code = [i for i, t in enumerate(lines) if t.strip()]
    at = code.index(line)
    picked = code[max(0, at - 2):at + 3]
    return textwrap.dedent("\n".join(lines[i] for i in picked)).splitlines(), picked.index(line)


def _option(d: dict) -> str:
    return f"`{d['call']}` gives {d['got']}, but the real code gives {d['expected']}"


def _bug(repo: Path, path: str, name: str, source: str, tests: list[str], calls: list[str], base: list, results: dict,
         mutants: list[mutate.Mutant]) -> tuple[dict, dict]:
    diffs = {m.id: _differences(name, calls, base, results[m.id]) for m in mutants}
    changing = [m for m in mutants if diffs[m.id]]
    tested = [changing[i] for i in _spread_indices(len(changing), MAX_TESTED)]
    caught = [m for m in tested if not run_tests(repo, path, name, m.source, tests, restricted=False)["passed"]]
    stats = {"tests": tests, "mutants": len(mutants), "behaviour_changing": len(changing), "tested": len(tested),
             "caught_by_tests": len(caught)}
    if not caught:
        raise CourseError(f"{name}: the repository's tests do not catch any of its {len(tested)} behaviour-changing mutants, "
                          "so they cannot grade a rewrite")
    chosen = sorted(caught, key=lambda m: (diffs[m.id][0]["crashes"], KIND_RANK.get(m.kind, 9), m.line, m.id))[0]
    right = diffs[chosen.id][0]
    wrong, seen = [], {_option(right)}
    for m in sorted((m for m in changing if m.id != chosen.id), key=lambda m: m.line == chosen.line):
        taken = 0
        for d in diffs[m.id]:      # behaviours the chosen code does not show, so each option is false for it
            if not d["crashes"] and not _same(results[chosen.id][d["at"]], results[m.id][d["at"]]) and _option(d) not in seen:
                wrong.append(d)
                seen.add(_option(d))
                taken += 1
                if taken == 2:
                    break
    wrong = [wrong[i] for i in _spread_indices(len(wrong), 3)] if len(wrong) > 3 else wrong
    if len(wrong) < 2:
        raise CourseError(f"{name} has too few distinct behaviours for find-the-bug options")
    texts = sorted([_option(right), *(_option(d) for d in wrong)], key=lambda t: hashlib.sha256(t.encode()).hexdigest())
    options = [{"id": "abcd"[i], "text": t} for i, t in enumerate(texts)]
    answer = next(o["id"] for o in options if o["text"] == _option(right))
    lines = source.splitlines()
    shown, index = _excerpt(chosen.source, chosen.line)
    mutated_line = chosen.source.splitlines()[chosen.line]
    bug = {"mutant": chosen.id, "prompt": f"One line of {name} was changed. Click it, then pick what it now does wrong.",
           "excerpt": shown, "line_index": index, "line_number": chosen.line + 1, "options": options, "answer": answer,
           "original_line": lines[chosen.line], "mutated_line": mutated_line, "kind": chosen.kind, "detected_by": ", ".join(tests),
           "explanation": (f"Line {chosen.line + 1} was changed from `{lines[chosen.line].strip()}` to `{mutated_line.strip()}`: "
                           f"{chosen.description}. {_option(right)}. The repository's own tests catch it."),
           "counterexample": {k: right[k] for k in ("call", "expected", "got")}, "mutant_code": chosen.source}
    return bug, stats


# ---- write and explain ----

def _stub(fsrc: str) -> str:
    """The signature and the docstring of a function, then `raise NotImplementedError` where its body was."""
    node = ast.parse(fsrc).body[0]
    first = node.body[0]
    has_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
    head = fsrc.splitlines()[start:first.end_lineno if has_doc else first.lineno - 1]
    return "\n".join(head) + "\n" + " " * first.col_offset + "raise NotImplementedError\n"


def module_names(tree: ast.Module) -> set[str]:
    """Plain values a module defines at the top level (not modules): constants, functions, classes, imported names."""
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, ast.ImportFrom):
            names.update(a.asname or a.name for a in stmt.names)
    return names


def _write(module_tree: ast.Module, name: str, node: ast.FunctionDef, tests: list[str], fsrc: str) -> dict:
    prompt = (f"Write {name} so the repository's own tests ({', '.join(tests)}) pass with it in place of the real one. "
              "You get the signature and the docstring.")
    return {"function": name, "prompt": prompt, "stub": _stub(fsrc), "tests": tests,
            "names": [n for n in free_names(node) if n in module_names(module_tree)],
            "reasons": course_rubric.literal_reasons(node),
            "hint": "Read the docstring literally, then the failing test's message: it names what is missing."}


def _subjects(repo: Path, commit_range: str | None, path: str) -> list[str]:
    if not commit_range:
        return []
    return [s for s in _git(repo, "log", "--format=%s", commit_range, "--", path).splitlines() if s.strip()]


def build_stage(repo: Path, target: str, *, order: int, tests: list[str], spec: dict | None = None, diff: str | None = None,
                commit_range: str | None = None) -> dict:
    repo = Path(repo)
    spec = spec or {}
    path, name = parse_target(target)
    module_text = (repo / path).read_text(encoding="utf-8")
    node, fsrc = _function_node(module_text, name), function_source(module_text, name)
    tests = spec.get("tests") or tests
    inputs = spec.get("inputs") or course_inputs.from_tests(repo, tests, name)
    if len(inputs) < 3:
        raise CourseError(f"{name}: list at least three example calls under `inputs`")
    views = spec.get("views") or [DEFAULT_VIEW]
    more = course_inputs.extras(inputs)
    calls = list(dict.fromkeys([*inputs, *more]))
    mutants = mutate.generate(fsrc)
    variants = {"real": None, **{m.id: m.source for m in mutants}}
    results = run_calls(repo, path, name, variants, calls, views)
    base = results["real"]
    bug, strength = _bug(repo, path, name, fsrc, tests, calls, base, results, mutants)
    predict = _predict(name, calls, base, views)
    write = _write(ast.parse(module_text), name, node, tests, fsrc)
    stub_check = run_tests(repo, path, name, write["stub"], tests)
    real_check = run_tests(repo, path, name, fsrc, tests, restricted=False)
    if stub_check["passed"] or not real_check["passed"]:
        raise CourseError(f"{name}: the tests must pass on the real function and fail on the stub "
                          f"(real passes: {real_check['passed']}, stub fails: {not stub_check['passed']})")
    rubric = course_rubric.build(node, fsrc, name, diff, _subjects(repo, commit_range, path))
    if len(rubric) < 2:
        raise CourseError(f"{name}: not enough docstring, comments or reasons to make an explain rubric")
    title = spec.get("title") or name.strip("_").replace("_", " ").capitalize()
    return {"id": spec.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "order": order, "engine": "call",
            "title": title, "summary": spec.get("summary") or (ast.get_docstring(node) or "").split("\n\n")[0].replace("\n", " "),
            "source": {"path": path, "function": name, "sha256": hashlib.sha256(fsrc.encode()).hexdigest()[:16],
                       "lines": len(fsrc.splitlines())},
            "predict": predict, "bug": bug, "strength": strength,
            "write": {**write, "verified": {"real_passes": True, "stub_fails": True}},
            "explain": {"prompt": f"In your own words: what does {name} do, what does it refuse and why? Quote one of your own cases above with its call and what it returns.",
                        "min_points": course_rubric.min_points(len(rubric)), "min_words": 25, "rubric": rubric,
                        "example_case": "p1"},
            "code": fsrc}


def build_course(repo: Path, targets: list[str] | None = None, commit_range: str | None = None, *, course_id: str, title: str,
                 tests: list[str], specs: dict | None = None, blurb: str = "") -> dict:
    repo, specs = Path(repo), specs or {}
    found = [{"target": t, "diff": None} for t in targets or []]
    if commit_range:
        found += [f for f in functions_from_range(repo, commit_range) if f["target"] not in (targets or [])]
    if not found:
        raise CourseError("name some functions or a commit range that touches a function")
    stages = [build_stage(repo, f["target"], order=i, tests=tests, spec=specs.get(f["target"]), diff=f["diff"],
                          commit_range=commit_range) for i, f in enumerate(found, 1)]
    source = {"commit_range": commit_range} if commit_range else {}
    return {"id": course_id, "title": title, "blurb": blurb, "engine": "call", "source": source, "stages": stages}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    course = build_course(args.repo, list(spec["functions"]), spec.get("commit_range"), course_id=spec["id"], title=spec["title"],
                          tests=spec["tests"], specs=spec["functions"], blurb=spec.get("blurb", ""))
    args.out.write_text(json.dumps(course, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(course['stages'])} stages", file=sys.stderr)


if __name__ == "__main__":
    main()
