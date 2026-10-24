"""A tiny real repository (its own code and its own tests) that the course engine tests build courses from."""
from __future__ import annotations

import subprocess
from pathlib import Path

RULES = '''"""Small rules for a shop's stock sheet."""

MAX_QTY = 100
MAX_NAME = 12


def clamp_qty(qty, limit=MAX_QTY):
    """Clamp a quantity into 0..limit. A negative quantity becomes 0 and a quantity above the limit becomes the limit."""
    if qty < 0:
        return 0
    if qty > limit:
        return limit
    return qty


def check_name(name):
    """Return a reason if the name is unusable, else None.

    A name must be 1 to 12 characters. It must not start with a space.
    """
    if not name or len(name) > MAX_NAME:
        return "name must be 1 to 12 characters"
    if name.startswith(" "):
        return "name must not start with a space"
    return None
'''

TESTS = '''from shop.rules import check_name, clamp_qty


def test_clamp_keeps_values_in_range():
    assert clamp_qty(5) == 5
    assert clamp_qty(100) == 100
    assert clamp_qty(0) == 0


def test_clamp_floors_negatives_and_caps_large_values():
    assert clamp_qty(-3) == 0
    assert clamp_qty(101) == 100
    assert clamp_qty(50, limit=10) == 10


def test_check_name_accepts_and_rejects():
    assert check_name("rice") is None
    assert check_name("a" * 12) is None
    assert check_name("") == "name must be 1 to 12 characters"
    assert check_name("a" * 13) == "name must be 1 to 12 characters"
    assert check_name(" rice") == "name must not start with a space"
'''

SPEC = {
    "shop/rules.py:clamp_qty": {
        "title": "Keep a quantity in range",
        "summary": "A stock count may never be negative or above the limit.",
        "inputs": ["5", "-3", "0", "100", "101", "250", "50, 10", "-1, 10", "10, 10"],
        "views": [{"id": "value", "question": "What does it return?", "observe": "result"}],
    },
    "shop/rules.py:check_name": {
        "title": "Reject unusable names",
        "summary": "A name is rejected with a reason, or accepted with None.",
        "inputs": ['"rice"', '""', '"a" * 12', '"a" * 13', '" rice"', '"rice "'],
        "views": [{"id": "ok", "question": "Is the name accepted? (yes or no)", "observe": "result is None"}],
    },
}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def make_repo(root: Path) -> Path:
    repo = Path(root) / "shopdemo"
    (repo / "shop").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "shop" / "__init__.py").write_text("")
    (repo / "shop" / "rules.py").write_text(RULES)
    (repo / "tests" / "test_rules.py").write_text(TESTS)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo
