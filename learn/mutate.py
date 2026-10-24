"""Automatic mutants of one function. Each mutant changes exactly one line by one small token edit (an operator, a
constant, a negation), so "which line was changed, and what does it break?" always has one answer. Pure text work: the
mutants are only built here; learn/make_course.py runs them against the repository's own tests."""
from __future__ import annotations

import ast
import bisect
import re
from dataclasses import dataclass

COMPARE = {ast.Lt: ("<", rb"<(?![=<])", ("<=", ">")), ast.LtE: ("<=", rb"<=", ("<",)),
           ast.Gt: (">", rb"(?<![=-])>(?![=>])", (">=", "<")), ast.GtE: (">=", rb">=", (">",)),
           ast.Eq: ("==", rb"==", ("!=",)), ast.NotEq: ("!=", rb"!=", ("==",))}
WORDS = {ast.Is: ("is", rb"\bis\b(?!\s+not\b)", "is not", "identity"), ast.IsNot: ("is not", rb"\bis\s+not\b", "is", "identity"),
         ast.In: ("in", rb"\bin\b", "not in", "membership"), ast.NotIn: ("not in", rb"\bnot\s+in\b", "in", "membership")}
ARITHMETIC = {ast.Add: ("+", rb"\+", "-"), ast.Sub: ("-", rb"-", "+")}
BOOLEAN = {ast.And: ("and", rb"\band\b", "or"), ast.Or: ("or", rb"\bor\b", "and")}
EXPLAIN = {"compare": "the comparison `{o}` became `{n}`", "identity": "`{o}` became `{n}`", "membership": "`{o}` became `{n}`",
           "arithmetic": "the operator `{o}` became `{n}`", "boolean": "`{o}` became `{n}` between two conditions",
           "negation": "a `not` was removed", "constant": "the constant `{o}` became `{n}`"}


@dataclass(frozen=True)
class Mutant:
    id: str
    kind: str
    line: int            # 0-based index of the changed line in the function source
    original: str        # the text that was replaced
    replacement: str
    source: str          # the whole mutated function
    description: str


def _starts(raw: bytes) -> list[int]:
    out, pos = [0], 0
    for line in raw.split(b"\n")[:-1]:
        pos += len(line) + 1
        out.append(pos)
    return out


class _Collector:
    def __init__(self, raw: bytes) -> None:
        self.raw, self.starts = raw, _starts(raw)
        self.edits: list[tuple[int, int, str, str]] = []          # (start, end, replacement, kind)

    def at(self, lineno: int, col: int) -> int:
        return self.starts[lineno - 1] + col

    def end(self, node: ast.AST) -> int:
        return self.at(node.end_lineno, node.end_col_offset)

    def begin(self, node: ast.AST) -> int:
        return self.at(node.lineno, node.col_offset)

    def between(self, left: ast.AST, right: ast.AST, pattern: bytes, replacement: str, kind: str) -> None:
        lo, hi = self.end(left), self.begin(right)
        found = list(re.finditer(pattern, self.raw[lo:hi]))
        if len(found) == 1:
            self.edits.append((lo + found[0].start(), lo + found[0].end(), replacement, kind))


def _is_text(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)))


def _collect(tree: ast.AST, c: _Collector) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            parts = [node.left, *node.comparators]
            for i, op in enumerate(node.ops):
                if type(op) in COMPARE:
                    _, pattern, swaps = COMPARE[type(op)]
                    for new in swaps:
                        c.between(parts[i], parts[i + 1], pattern, new, "compare")
                elif type(op) in WORDS:
                    _, pattern, new, kind = WORDS[type(op)]
                    c.between(parts[i], parts[i + 1], pattern, new, kind)
        elif isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC and not (_is_text(node.left) or _is_text(node.right)):
            _, pattern, new = ARITHMETIC[type(node.op)]
            c.between(node.left, node.right, pattern, new, "arithmetic")
        elif isinstance(node, ast.BoolOp):
            _, pattern, new = BOOLEAN[type(node.op)]
            for left, right in zip(node.values, node.values[1:]):
                c.between(left, right, pattern, new, "boolean")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            start = c.begin(node)
            m = re.match(rb"not\s+", c.raw[start:])
            if m:
                c.edits.append((start, start + m.end(), "", "negation"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            c.edits.append((c.begin(node), c.end(node), str(not node.value), "constant"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, int):
            for new in {node.value + 1, node.value - 1} if node.value >= 1 else {node.value + 1}:
                c.edits.append((c.begin(node), c.end(node), str(new), "constant"))


def generate(source: str) -> list[Mutant]:
    """Every mutant of source (one module-level function), in a stable order, without duplicates."""
    raw = source.encode("utf-8")
    tree = ast.parse(source)
    c = _Collector(raw)
    _collect(tree, c)
    out, seen = [], set()
    for start, end, new, kind in sorted(set(c.edits), key=lambda e: (e[0], e[1], e[2])):
        if (start, end, new) in seen:
            continue
        seen.add((start, end, new))
        mutated = (raw[:start] + new.encode() + raw[end:]).decode("utf-8")
        try:
            ast.parse(mutated)
        except SyntaxError:
            continue
        if mutated == source:
            continue
        line = bisect.bisect_right(c.starts, start) - 1
        old = raw[start:end].decode("utf-8")
        label = EXPLAIN[kind].format(o=re.sub(r"\s+", " ", old), n=new)
        out.append(Mutant(id=f"{kind}-{line + 1}-{len(out) + 1}", kind=kind, line=line, original=old, replacement=new,
                          source=mutated, description=label))
    return out
