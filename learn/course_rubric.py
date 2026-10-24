"""An explain rubric made from what the repository already says: the function's docstring, the comments in its body, the
reasons it returns or raises, and the comments added by a commit range. Each point is one plain sentence plus up to four
key words; learn/course_grader.py covers a point when one learner sentence uses two of them."""
from __future__ import annotations

import ast
import math
import re

MAX_POINTS = 6
MAX_KEYWORDS = 4
STOP = frozenset("""a an the is are was were be been being it its this that these those of to in on at by for from with as and or but
not no if else then than so also any all each every one two when where which who what why how can may must should will would shall
does do did done has have had into onto out up down over under again only just more most such their there they them you your
returns return raises raise value values function call calls given else otherwise whether""".split())


def sentences(text: str) -> list[str]:
    """Docstring text as plain sentences: wrapped lines joined, one entry per sentence."""
    out = []
    for para in re.split(r"\n\s*\n", text or ""):
        flat = " ".join(para.split())
        out.extend(s.strip() for s in re.split(r"(?<=[.!?:])\s+(?=[A-Z(`\"'])", flat) if len(s.split()) >= 4)
    return out


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\d+|[a-z][a-z0-9']*", text.lower()) if t not in STOP]


def stem(token: str) -> str:
    if token.isdigit() or len(token) < 5:
        return token
    return re.sub(r"(ing|ed|es|s)$", "", token) + "*"


def keywords(text: str, common: set[str], frequency: dict[str, int]) -> list[str]:
    """Up to four stemmed key words, the rarest across the rubric first. Words every point shares are left out unless that
    would leave the point with fewer than two."""
    def pick(skip: set[str]) -> list[str]:
        seen, ranked = set(), []
        for t in tokens(text):
            s = stem(t)
            if s in seen or t in skip:
                continue
            seen.add(s)
            ranked.append((frequency.get(t, 0), -len(t), s))
        return [s for _, _, s in sorted(ranked)[:MAX_KEYWORDS]]
    return pick(common) if len(pick(common)) >= 2 else pick(set())


def comments(source: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"#\s*(.+)$", source, re.M) if len(m.group(1).split()) >= 3]


def literal_reasons(node: ast.AST) -> list[str]:
    """The human-readable strings a function returns or raises: its reasons."""
    out = []
    for n in ast.walk(node):
        values = []
        if isinstance(n, ast.Return) and n.value is not None:
            values = list(ast.walk(n.value))
        elif isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
            values = [a for arg in n.exc.args for a in ast.walk(arg)]
        out.extend(v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str) and len(v.value.split()) >= 3)
    return list(dict.fromkeys(out))


def candidate_points(node: ast.FunctionDef, source: str, diff: str | None, subjects: list[str]) -> list[str]:
    docs = sentences(ast.get_docstring(node) or "")[:3]
    notes = comments(source)[:2]
    changed = comments("\n".join(l[1:] for l in (diff or "").splitlines() if l.startswith("+") and not l.startswith("+++")))[:2]
    why = [s for s in subjects if len(tokens(s)) >= 2][:1]
    reasons = [f"It rejects with the reason: {r}" for r in literal_reasons(node)]
    points = [*docs, *(n[0].upper() + n[1:] for n in [*changed, *notes]), *(f"This was changed because: {s}" for s in why), *reasons]
    return points


def build(node: ast.FunctionDef, source: str, name: str, diff: str | None = None, subjects: list[str] | None = None) -> list[dict]:
    """Rubric points: {"id", "point", "keywords"}, with near-duplicates removed."""
    texts: list[str] = []
    for text in candidate_points(node, source, diff, subjects or []):
        have = set(tokens(text))
        if len(have) >= 2 and not any(len(have & set(tokens(t))) / len(have | set(tokens(t))) > 0.5 for t in texts):
            texts.append(text)
    texts = texts[:MAX_POINTS]
    frequency: dict[str, int] = {}
    for t in texts:
        for w in set(tokens(t)):
            frequency[w] = frequency.get(w, 0) + 1
    common = {w for w, n in frequency.items() if len(texts) >= 3 and n >= len(texts) - (len(texts) > 4)} | set(tokens(name))
    points = []
    for i, text in enumerate(texts, 1):
        words = keywords(text, common, frequency)
        if words:
            points.append({"id": f"r{i}", "point": text if text.endswith((".", "!", "?")) else text + ".", "keywords": words})
    return points


def min_points(count: int) -> int:
    return max(1, min(3, math.ceil(count * 0.6)))
