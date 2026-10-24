"""Where a course's example calls come from: the ones an author lists, the literal calls the repository's own tests make, and
deterministic perturbations of those (a number one up or down, a string shorter or longer, a key dropped). Argument lists
are kept as source text such as `5, limit=10` so a learner reads exactly what was run."""
from __future__ import annotations

import ast
from pathlib import Path

MAX_EXTRA = 40
LONG = 201


def parse_args(src: str) -> tuple[list[ast.expr], list[ast.keyword]]:
    call = ast.parse(f"f({src})", mode="eval").body
    return list(call.args), list(call.keywords)


def render(args: list[ast.expr], keywords: list[ast.keyword]) -> str:
    call = ast.Call(func=ast.Name(id="f", ctx=ast.Load()), args=args, keywords=keywords)
    return ast.unparse(call)[2:-1]


def _is_literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def from_tests(repo: Path, tests: list[str], function: str) -> list[str]:
    """Argument lists of literal calls to `function` found in the repository's own test files."""
    found: list[str] = []
    for rel in tests:
        path = Path(repo) / rel
        if not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == function:
                if all(_is_literal(a) for a in node.args) and all(_is_literal(k.value) for k in node.keywords):
                    text = render(node.args, node.keywords)
                    if text not in found:
                        found.append(text)
    return found


def _variants(node: ast.expr) -> list[ast.expr]:
    """Small edits of one literal argument."""
    if not _is_literal(node):
        return []
    value = ast.literal_eval(node)
    edits: list = []
    if isinstance(value, bool):
        edits = [not value]
    elif isinstance(value, int):
        edits = [value + 1, value - 1, 0, -value if value else 1]
    elif isinstance(value, str):
        edits = ["", value + "x", value[:-1], " " + value, value + " ", "x" * LONG, "x" * (LONG - 1), value + "\x01"]
    elif isinstance(value, dict):
        for key in list(value):
            edits.append({k: v for k, v in value.items() if k != key})
            for alt in (0, "", None, True, -1, "x"):
                edits.append({**value, key: alt})
        edits.append({**value, "extra": 1})
    return [ast.parse(repr(e), mode="eval").body for e in edits if e != value]


def extras(inputs: list[str]) -> list[str]:
    """Perturbations of the given calls, spread across them, without repeats."""
    seen, out = set(inputs), []
    for round_no in range(8):
        for src in inputs:
            args, kws = parse_args(src)
            slots = [(args, i) for i in range(len(args))] + [(kws, i) for i in range(len(kws))]
            for pool, i in slots:
                node = pool[i] if pool is args else pool[i].value
                options = _variants(node)
                if round_no >= len(options):
                    continue
                new = options[round_no]
                a, k = list(args), list(kws)
                if pool is args:
                    a[i] = new
                else:
                    k[i] = ast.keyword(arg=kws[i].arg, value=new)
                text = render(a, k)
                if text not in seen:
                    seen.add(text)
                    out.append(text)
                    if len(out) >= MAX_EXTRA:
                        return out
    return out
