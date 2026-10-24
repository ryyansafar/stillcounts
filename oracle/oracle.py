"""The referee for the ledger merge. Plain code. Devin's chaos harness may call it but must never edit it."""
from __future__ import annotations

import random
from typing import Callable, Iterable

from stillcounts.crdt import Op, canonical, merge

ITEMS = ["blankets", "rice", "water", "paracetamol", "tarpaulin", "baby-food"]
KINDS = ["adjust", "adjust", "adjust", "create", "note", "delete"]


class OracleFailure(AssertionError):
    pass


def generate_ops(seed: int, n: int, replicas: int) -> list[Op]:
    """A reproducible set of operations written by several replicas, with each replica's own Lamport clock."""
    rng = random.Random(seed)
    names = [f"ph-{chr(97 + i)}" for i in range(replicas)]
    clocks = {r: 0 for r in names}
    counters = {r: 0 for r in names}
    ops: list[Op] = []
    for _ in range(n):
        r = rng.choice(names)
        if rng.random() < 0.2:                       # sometimes a replica has seen another's time
            clocks[r] = max(clocks[r], max(clocks.values()) - rng.randint(0, 3))
        clocks[r] += 1
        counters[r] += 1
        kind = rng.choice(KINDS)
        item = rng.choice(ITEMS)
        if kind == "adjust":
            value: object = rng.choice([-1, 1]) * rng.randint(1, 40)
        elif kind == "delete":
            value = rng.random() < 0.5
        else:
            value = rng.choice(["Alpha", "Bravo", "Charlie", "Delta"])
        ops.append(Op(id=f"{r}:{counters[r]}", replica=r, lamport=clocks[r], kind=kind, item=item, value=value))
    return ops


def permutation_check(ops: Iterable[Op], perms: int = 200, seed: int = 0,
                      merge_fn: Callable = merge) -> dict:
    """Replay the same operations in many random orders, with random duplicates, and demand identical state."""
    ops = list(ops)
    expected = canonical(merge_fn(ops))
    rng = random.Random(seed)
    for i in range(perms):
        order = ops + rng.sample(ops, k=rng.randint(0, min(10, len(ops))))
        rng.shuffle(order)
        got = canonical(merge_fn(order))
        if got != expected:
            raise OracleFailure(f"permutation {i} differs from the reference state (seed {seed}, {len(ops)} ops)")
    return {"ok": True, "ops": len(ops), "permutations": perms, "seed": seed}


def check_delivery(created: Iterable[Op], deliveries: dict[str, list[Op]]) -> dict:
    """Every replica's final received log must contain every created operation, and merge to the same state."""
    created = list(created)
    ids = {o.id for o in created}
    expected = canonical(merge(created))
    lost_total = 0
    for replica, received in deliveries.items():
        have = {o.id for o in received}
        lost = len(ids - have)
        if lost:
            raise OracleFailure(f"replica {replica} lost {lost} of {len(ids)} operations")
        if canonical(merge(received)) != expected:
            raise OracleFailure(f"replica {replica} state differs from the reference after delivery")
    return {"ok": True, "replicas": len(deliveries), "ops": len(ids), "lost": lost_total}


def main() -> None:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Permutation oracle for the ledger merge")
    p.add_argument("--seed", type=lambda s: int(s, 0), default=0xA3)
    p.add_argument("--ops", type=int, default=240)
    p.add_argument("--replicas", type=int, default=3)
    p.add_argument("--perms", type=int, default=200)
    a = p.parse_args()
    try:
        print(json.dumps(permutation_check(generate_ops(a.seed, a.ops, a.replicas), a.perms, a.seed)))
    except OracleFailure as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
