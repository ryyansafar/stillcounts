"""Per-learner predict cases.

Every learner gets the same four stages but their own numbers: the quantities, the Lamport times and the text values
of each predict case are re-rolled from a seed built from the learner, the stage, the case and a round counter. The
structure of the case (which ops are retries, which write wins, whether the stock ends exactly at zero) is kept, so
the lesson is the same while the answer is not something a classmate can pass along.

A wrong guess on one case bumps that case's round, so the next attempt is a different case. The round lives on the
server, so a page cannot choose its own numbers. Nothing here is secret in the cryptographic sense: the learner is
shown every operation, and anyone can run the public merge on them. The point is that the answer is not in the page
and is not shared between learners.
"""
from __future__ import annotations

import hashlib
import random
import re

PLACEHOLDER = re.compile(r"\{([vls])(\d+)\}")
SCALE_RANGE = (2, 12)
MAGNITUDE_RANGE = (1, 40)
LAMPORT_RANGE = (1, 60)
TRIES = 300
PUBLIC_CASE_FIELDS = ("id", "name", "question", "item", "field")


def seed_for(learner: str, stage_id: str, case_id: str, round_no: int) -> int:
    digest = hashlib.sha256(f"{learner}|{stage_id}|{case_id}|{round_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def render(template: str, ops: list[dict]) -> str:
    """Fill {v0} (absolute value of op 0), {l1} (Lamport time of op 1) and {s2} (text value of op 2)."""
    def fill(m: re.Match) -> str:
        kind, i = m.group(1), int(m.group(2))
        op = ops[i]
        return str(abs(op["value"]) if kind == "v" else op["lamport"] if kind == "l" else op["value"])
    return PLACEHOLDER.sub(fill, template)


def is_personalisable(case) -> bool:
    return (isinstance(case, dict) and isinstance(case.get("template"), str) and isinstance(case.get("ops"), list)
            and bool(case.get("item")) and bool(case.get("field")))


def stage_is_personalisable(stage: dict) -> bool:
    predict = stage.get("predict")
    cases = predict.get("cases") if isinstance(predict, dict) else None
    return isinstance(cases, list) and bool(cases) and all(is_personalisable(c) for c in cases)


def answer(case: dict, ops: list[dict] | None = None):
    """The real merge's answer to a case (its own ops unless others are given)."""
    ops = case["ops"] if ops is None else ops
    from stillcounts.crdt import Op, merge, stats
    mops = [Op(**o) for o in ops]
    if case["field"] == "oversold":
        return case["item"] in stats(mops)["oversold"]
    return merge(mops)[case["item"]][case["field"]]


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def _attempt(case: dict, rng: random.Random) -> list[dict]:
    ops = case["ops"]
    lamports = sorted({o["lamport"] for o in ops})
    fresh = sorted(rng.sample(range(*LAMPORT_RANGE), len(lamports)))
    lamport_of = dict(zip(lamports, fresh))
    scale = rng.randint(*SCALE_RANGE) if case.get("reroll", "free") == "scale" else None
    values: dict[tuple, object] = {}
    used: set = set()
    out = []
    for o in ops:
        key = (o["id"], o["lamport"], o["kind"], repr(o["value"]))
        if key not in values:
            values[key] = _fresh_value(o, scale, rng, used)
        out.append({**o, "lamport": lamport_of[o["lamport"]], "value": values[key]})
    return out


def _fresh_value(op: dict, scale: int | None, rng: random.Random, used: set):
    if op["kind"] == "adjust":
        if scale is not None:
            return op["value"] * scale
        while True:
            magnitude = rng.randint(*MAGNITUDE_RANGE)
            if magnitude not in used:
                used.add(magnitude)
                return magnitude if op["value"] > 0 else -magnitude
    if op["kind"] in ("create", "note"):
        while True:
            tag = rng.randint(10, 99)
            if ("tag", tag) not in used:
                used.add(("tag", tag))
                return f"{op['value']} #{tag}"
    return op["value"]


def _same_shape(case: dict, ops: list[dict]) -> bool:
    before, after = answer(case), answer(case, ops)
    if isinstance(before, bool):
        return before == after
    if isinstance(before, int):
        return _sign(before) == _sign(after)
    return True


def roll(case: dict, seed: int) -> list[dict]:
    """New operations for a case: same structure, different numbers. Falls back to the stock ops only if no draw
    keeps the structure (which the tests rule out for the shipped stages)."""
    rng = random.Random(seed)
    for _ in range(TRIES):
        ops = _attempt(case, rng)
        if _same_shape(case, ops):
            return ops
    return [dict(o) for o in case["ops"]]


def personal_case(case: dict, learner: str, stage_id: str, round_no: int) -> dict:
    ops = roll(case, seed_for(learner, stage_id, case["id"], round_no))
    out = {k: case[k] for k in PUBLIC_CASE_FIELDS if k in case}
    out.update(ops=ops, prompt=render(case["template"], ops))
    return out


def personalise(stage: dict, learner: str, rounds: dict | None = None) -> list[dict] | None:
    """This learner's predict cases for a stage (no expected answers), or None when the stage has no templated cases."""
    if not stage_is_personalisable(stage):
        return None
    rounds = rounds or {}
    return [personal_case(c, learner, stage["id"], int(rounds.get(f"predict:{c['id']}", 0)))
            for c in stage["predict"]["cases"]]


def example_numbers(case: dict) -> set[int]:
    """The numbers a learner's own counterexample should quote: the values and Lamport times of this learner's case
    and the merged answer when it is a number."""
    numbers: set[int] = set()
    for o in case["ops"]:
        numbers.add(o["lamport"])
        if o["kind"] == "adjust":
            numbers.add(abs(o["value"]))
    merged = answer(case)
    if isinstance(merged, int) and not isinstance(merged, bool):
        numbers.add(abs(merged))
    return numbers
