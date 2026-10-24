"""Differential fuzzing: the Python reference and the JavaScript client must agree on every random input."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from stillcounts.crdt import Op, merge, stats

RUNNER = Path(__file__).resolve().parent.parent / "client" / "diff_runner.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

TEXTS = ["Alpha", "", "അടൂർ", "Camp 🏕", "line break", "a‍b"]


def random_op(rng, i):
    kind = rng.choice(["adjust", "adjust", "create", "note", "delete", "bad-kind", "zero-adjust"])
    replica = rng.choice(["ph-a", "ph-b", "ph-c"])
    item = rng.choice(["rice", "water", "x"])
    lamport = rng.choice([1, 2, 3, 5, 8, 13, 2 ** 31])
    value = {"adjust": rng.choice([-9, -1, 1, 7, 100]), "create": rng.choice(TEXTS[:4]), "note": rng.choice(TEXTS),
             "delete": rng.random() < 0.5, "bad-kind": 1, "zero-adjust": 0}[kind]
    kind = {"bad-kind": "explode", "zero-adjust": "adjust"}.get(kind, kind)
    oid = f"{replica}:{rng.randint(1, max(3, i))}"            # ids collide on purpose, with different content
    return {"id": oid, "replica": replica, "lamport": lamport, "kind": kind, "item": item, "value": value}


def python_result(ops):
    try:
        objs = [Op(**o) for o in ops]
        return {"state": merge(objs), "stats": stats(objs)}
    except ValueError:
        return {"error": True}


def test_python_and_javascript_agree_on_500_random_operation_sets():
    rng = random.Random(2026)
    sets = [[random_op(rng, i) for i in range(rng.randint(0, 14))] for _ in range(500)]
    out = subprocess.run(["node", str(RUNNER)], input=json.dumps(sets), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    mismatches = []
    for i, (ops, got) in enumerate(zip(sets, js)):
        want = python_result(ops)
        if "error" in want or "error" in got:
            if ("error" in want) != ("error" in got):
                mismatches.append((i, "one side rejected the input, the other accepted it"))
            continue
        if got != want:
            mismatches.append((i, "different merged state"))
    assert not mismatches, mismatches[:3]
