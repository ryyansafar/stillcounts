"""Mutation testing for the referee: break the merge on purpose and demand that the referee notices every time.
Part of the referee, so agents must not edit it."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from oracle.oracle import OracleFailure, generate_ops, permutation_check

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "stillcounts" / "crdt.py"
VECTORS = ROOT / "tests" / "vectors.json"

MUTANTS = [
    {"name": "lww-picks-earliest", "old": "return max(ops, key=_order).value if ops else default", "new": "return min(ops, key=_order).value if ops else default"},
    {"name": "order-ignores-replica", "old": "return (op.lamport, op.replica)\n\n\ndef _total", "new": "return (op.lamport,)\n\n\ndef _total"},
    {"name": "order-swapped", "old": "return (op.lamport, op.replica)\n\n\ndef _total", "new": "return (op.replica, op.lamport)\n\n\ndef _total"},
    {"name": "dedupe-keeps-smallest", "old": "if cur is None or _total(op) > _total(cur):", "new": "if cur is None or _total(op) < _total(cur):"},
    {"name": "dedupe-last-arrival-wins", "old": "if cur is None or _total(op) > _total(cur):", "new": "if True:"},
    {"name": "merge-skips-dedupe", "old": "for op in dedupe(ops):\n        by_item", "new": "for op in ops:\n        by_item"},
    {"name": "quantity-counts-magnitudes", "old": "sum(o.value for o in group if o.kind == \"adjust\")", "new": "sum(abs(o.value) for o in group if o.kind == \"adjust\")"},
    {"name": "quantity-ignores-subtractions", "old": "sum(o.value for o in group if o.kind == \"adjust\")", "new": "sum(o.value for o in group if o.kind == \"adjust\" and o.value > 0)"},
    {"name": "delete-flag-inverted", "old": "\"alive\": not _lww(flags, False),", "new": "\"alive\": bool(_lww(flags, False)),"},
    {"name": "name-default-empty", "old": "\"name\": _lww(names, item),", "new": "\"name\": _lww(names, \"\"),"},
    {"name": "conflicts-count-winners", "old": "conflicts += sum(1 for o in g if o.replica != winner.replica)", "new": "conflicts += sum(1 for o in g if o.replica == winner.replica)"},
    {"name": "oversold-includes-zero", "old": "if s[\"qty\"] < 0 and s[\"alive\"]", "new": "if s[\"qty\"] <= 0 and s[\"alive\"]"},
    {"name": "validation-accepts-zero-adjust", "old": "or op.value == 0:\n            raise ValueError(\"adjust needs a non-zero integer\")", "new": ":\n            raise ValueError(\"adjust needs a non-zero integer\")"},
    {"name": "clock-does-not-observe", "old": "self.time = max(self.time, remote)", "new": "self.time = self.time"},
]


def _load(source: str) -> types.ModuleType:
    module = types.ModuleType("mutant_crdt")
    sys.modules["mutant_crdt"] = module          # dataclasses look the module up by name while the class is built
    exec(compile(source, "mutant_crdt.py", "exec"), module.__dict__)
    return module


def referee_failure(mod: types.ModuleType) -> str | None:
    """The full referee: golden answers, order independence and clock rules. Returns why it failed, or None if it passed."""
    try:
        for case in json.loads(VECTORS.read_text()):
            ops = [mod.Op(**o) for o in case["ops"]]
            if mod.merge(ops) != case["state"]:
                return f"merged state differs from the reference on '{case['name']}'"
            if mod.stats(ops) != case["stats"]:
                return f"statistics differ from the reference on '{case['name']}'"
        permutation_check(generate_ops(0xA3, 120, 3), perms=40, seed=3, merge_fn=mod.merge)
        c = mod.Clock("a")
        c.observe(10)
        if c.tick() != 11:
            return "the Lamport clock does not advance past an observed remote time"
        try:
            mod.merge([mod.Op("a:1", "a", 1, "adjust", "x", 0)])
        except ValueError:
            return None                                             # refused correctly: all checks passed
        return "a zero adjustment was accepted"
    except OracleFailure as exc:
        return str(exc)
    except Exception as exc:                                        # a crash is also a failure
        return f"crashed: {type(exc).__name__}"


def _killed(mod: types.ModuleType) -> bool:
    return referee_failure(mod) is not None


def run() -> dict:
    source = SOURCE.read_text()
    survivors, killed = [], 0
    for m in MUTANTS:
        mutated = source.replace(m["old"], m["new"], 1)
        if mutated == source:
            survivors.append(m["name"] + " (pattern not found)")
            continue
        if _killed(_load(mutated)):
            killed += 1
        else:
            survivors.append(m["name"])
    return {"total": len(MUTANTS), "killed": killed, "survivors": survivors,
            "score": round(100 * killed / len(MUTANTS), 1)}


def main() -> None:
    result = run()
    print(json.dumps(result))
    raise SystemExit(0 if not result["survivors"] else 1)


if __name__ == "__main__":
    main()
