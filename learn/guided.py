"""Guided answers for the Write checkpoint. When the learner is stressed (difficulty level 1) the Write card turns into a
multiple choice: four candidate function bodies, exactly one right. The choice is NOT marked here. The chosen body is sent to
the same grader as typed code, so a pick is still graded by running it (tests/test_learn_guided.py proves exactly one passes)."""

PROMPT = "Pick the body that does what the docstring says. It is graded by running it, like any other answer."

BODIES = {
    "duplicate-ids": [
        "    best = {}\n    for op in ops:\n        _validate(op)\n        best.setdefault(op.id, op)\n    return [best[k] for k in sorted(best)]\n",
        "    best = {}\n    for op in ops:\n        _validate(op)\n        cur = best.get(op.id)\n        if cur is None or _total(op) > _total(cur):\n            best[op.id] = op\n    return [best[k] for k in sorted(best)]\n",
        "    best = {}\n    for op in ops:\n        cur = best.get(op.id)\n        if cur is None or _total(op) > _total(cur):\n            best[op.id] = op\n    return [best[k] for k in sorted(best)]\n",
        "    return sorted(ops, key=lambda op: op.id)\n"],
    "signed-adjust-sum": [
        "    return sum(abs(o.value) for o in ops if o.kind == \"adjust\")\n",
        "    return sum(o.value for o in ops if o.kind == \"adjust\")\n",
        "    return sum(o.value for o in ops if o.kind == \"adjust\" and o.value > 0)\n",
        "    return len([o for o in ops if o.kind == \"adjust\"])\n"],
    "lamport-lww": [
        "    return min(ops, key=_order).value if ops else default\n",
        "    return max(ops, key=lambda o: o.lamport).value if ops else default\n",
        "    return max(ops, key=_order).value if ops else default\n",
        "    return ops[-1].value if ops else default\n"],
    "oversell-flag": [
        "    return [i for i, s in state.items() if s[\"qty\"] <= 0 and s[\"alive\"]]\n",
        "    return [i for i, s in state.items() if s[\"qty\"] < 0]\n",
        "    return [i for i, s in state.items() if s[\"qty\"] < 0 and s[\"alive\"]]\n",
        "    return [i for i, s in state.items() if s[\"qty\"] < 0 and s[\"alive\"]][:1]\n"],
}


def for_stage(stage_id: str) -> dict:
    return {"prompt": PROMPT, "choices": [{"id": "abcd"[i], "body": b} for i, b in enumerate(BODIES[stage_id])]}
