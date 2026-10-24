"""Write golden test vectors from the Python reference merge so the JavaScript client can be checked against it."""
import json
from pathlib import Path

from oracle.oracle import generate_ops
from stillcounts.crdt import Op, merge, stats

OUT = Path(__file__).resolve().parent.parent / "tests" / "vectors.json"


def case(name, ops):
    return {"name": name, "ops": [o.__dict__ for o in ops], "state": merge(ops), "stats": stats(ops)}


def build() -> list[dict]:
    cases = [case(f"seed-{seed}-n{n}", generate_ops(seed, n, r)) for seed, n, r in
             [(1, 20, 2), (2, 60, 3), (0xA3, 240, 3), (7, 100, 4), (99, 500, 5)]]
    o = lambda i, rep, lam, kind, item, val: Op(f"{rep}:{i}", rep, lam, kind, item, val)
    cases += [
        case("tie-breaks-on-replica", [o(1, "a", 3, "create", "x", "Old"), o(1, "b", 3, "create", "x", "New")]),
        case("duplicates", [o(1, "a", 1, "adjust", "rice", 5)] * 4),
        case("unicode-text", [o(1, "a", 1, "note", "x", "അടൂർ ✓"), o(2, "b", 2, "create", "x", "Camp 🏕")]),
        case("restore-after-delete", [o(1, "a", 1, "delete", "x", True), o(2, "b", 2, "delete", "x", False)]),
        case("oversell", [o(1, "a", 1, "adjust", "b", 10), o(1, "b", 2, "adjust", "b", -7), o(2, "c", 3, "adjust", "b", -6)]),
        case("same-id-different-value", [o(1, "a", 1, "adjust", "rice", 5), Op("a:1", "a", 1, "adjust", "rice", -30)]),
        case("same-id-different-value-reversed", [Op("a:1", "a", 1, "adjust", "rice", -30), o(1, "a", 1, "adjust", "rice", 5)]),
        case("same-id-different-lamport", [o(1, "a", 1, "note", "x", "early"), Op("a:1", "a", 9, "note", "x", "late")]),
        case("same-id-different-lamport-reversed", [Op("a:1", "a", 9, "note", "x", "late"), o(1, "a", 1, "note", "x", "early")]),
        case("empty", []),
    ]
    return cases


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True))
    print("wrote", OUT)
