"""A tiny operation-based CRDT for a relief-camp ledger.

State is a pure function of the SET of operations (deduplicated by id), so any delivery order,
any duplication and any split-and-merge gives byte-identical state.
- quantity: a sum of signed integer adjustments (a PN-counter in operation form)
- name, note, alive: last-writer-wins registers ordered by (lamport, replica)
- delete is a tombstone that a later write can reverse
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

KINDS = {"adjust", "create", "note", "delete"}


@dataclass(frozen=True)
class Op:
    id: str
    replica: str
    lamport: int
    kind: str
    item: str
    value: Any = None


class Clock:
    """A Lamport clock: ordering that never depends on the phone's wall clock."""

    def __init__(self, replica: str, start: int = 0) -> None:
        self.replica = replica
        self.time = start

    def tick(self) -> int:
        self.time += 1
        return self.time

    def observe(self, remote: int) -> None:
        self.time = max(self.time, remote)


def _validate(op: Op) -> None:
    if op.kind not in KINDS:
        raise ValueError(f"unknown op kind: {op.kind!r}")
    if not isinstance(op.lamport, int) or isinstance(op.lamport, bool) or op.lamport < 1:
        raise ValueError("lamport must be a positive integer")
    if op.kind == "adjust":
        if not isinstance(op.value, int) or isinstance(op.value, bool) or op.value == 0:
            raise ValueError("adjust needs a non-zero integer")
    elif op.kind == "delete":
        if not isinstance(op.value, bool):
            raise ValueError("delete needs a boolean")
    elif not isinstance(op.value, str):
        raise ValueError(f"{op.kind} needs a string")


def _order(op: Op) -> tuple:
    return (op.lamport, op.replica)


def _total(op: Op) -> tuple:
    """A total order over op content, so duplicate ids resolve deterministically."""
    return (op.lamport, op.replica, op.kind, op.item, repr(op.value))


def dedupe(ops: Iterable[Op]) -> list[Op]:
    best: dict[str, Op] = {}
    for op in ops:
        _validate(op)
        cur = best.get(op.id)
        if cur is None or _total(op) > _total(cur):
            best[op.id] = op
    return [best[k] for k in sorted(best)]


def _lww(ops: list[Op], default: Any) -> Any:
    return max(ops, key=_order).value if ops else default


def merge(ops: Iterable[Op]) -> dict[str, dict]:
    by_item: dict[str, list[Op]] = {}
    for op in dedupe(ops):
        by_item.setdefault(op.item, []).append(op)
    state: dict[str, dict] = {}
    for item in sorted(by_item):
        group = by_item[item]
        names = [o for o in group if o.kind == "create"]
        notes = [o for o in group if o.kind == "note"]
        flags = [o for o in group if o.kind == "delete"]
        state[item] = {
            "name": _lww(names, item),
            "qty": sum(o.value for o in group if o.kind == "adjust"),
            "note": _lww(notes, ""),
            "alive": not _lww(flags, False),
        }
    return state


def canonical(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stats(ops: Iterable[Op]) -> dict:
    unique = dedupe(ops)
    state = merge(unique)
    conflicts = 0
    groups: dict[tuple[str, str], list[Op]] = {}
    for o in unique:
        if o.kind in ("create", "note", "delete"):
            groups.setdefault((o.item, o.kind), []).append(o)
    for g in groups.values():
        winner = max(g, key=_order)
        conflicts += sum(1 for o in g if o.replica != winner.replica)
    oversold = [i for i, s in state.items() if s["qty"] < 0 and s["alive"]]
    return {"ops": len(unique), "items": len(state), "conflicts": conflicts, "oversold": oversold}
