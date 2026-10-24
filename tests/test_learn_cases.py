"""Per-learner predict cases: different numbers for every learner, the same lesson."""
import pytest

from learn.cases import answer, example_numbers, personalise, render, roll, seed_for
from learn.grader import load_stages

STAGES = load_stages()
CASES = [(s, c) for s in STAGES for c in s["predict"]["cases"]]


def ids(pairs):
    return [f"{s['id']}/{c['id']}" for s, c in pairs]


def test_every_shipped_case_is_templated_and_its_stock_prompt_is_the_rendered_template():
    for s, c in CASES:
        assert c["template"] and c["reroll"] in ("free", "scale")
        assert render(c["template"], c["ops"]) == c["prompt"]


def test_the_same_learner_gets_the_same_cases_and_a_new_round_gets_new_ones():
    stage = STAGES[0]
    a, b = personalise(stage, "learner-a", {}), personalise(stage, "learner-a", {})
    assert a == b
    assert personalise(stage, "learner-a", {"predict:p1": 1})[0]["ops"] != a[0]["ops"]
    assert personalise(stage, "learner-a", {"predict:p1": 1})[1:] == a[1:]        # only the wrong case is replaced
    assert personalise(stage, "learner-b", {}) != a


def test_seeds_depend_on_learner_stage_case_and_round():
    base = seed_for("l", "s", "p1", 0)
    assert len({base, seed_for("m", "s", "p1", 0), seed_for("l", "t", "p1", 0), seed_for("l", "s", "p2", 0),
                seed_for("l", "s", "p1", 1)}) == 5


@pytest.mark.parametrize("stage,case", CASES, ids=ids(CASES))
def test_the_structure_of_a_case_survives_every_reroll(stage, case):
    stock = answer(case)
    seen_ops = set()
    for n in range(60):
        ops = roll(case, seed_for(f"learner-{n}", stage["id"], case["id"], n % 3))
        seen_ops.add(repr(ops))
        assert [(o["id"], o["kind"], o["item"]) for o in ops] == [(o["id"], o["kind"], o["item"]) for o in case["ops"]]
        new = answer(case, ops)
        if isinstance(stock, bool):
            assert new == stock                                          # yes stays yes, flagged stays flagged
        elif isinstance(stock, int):
            assert (new > 0) - (new < 0) == (stock > 0) - (stock < 0)    # exactly zero stays zero, negative stays negative
        stock_lamports = [o["lamport"] for o in case["ops"]]
        for i in range(len(ops)):
            for j in range(len(ops)):                                    # the order of Lamport times is kept, ties included
                assert (ops[i]["lamport"] < ops[j]["lamport"]) == (stock_lamports[i] < stock_lamports[j])
                assert (ops[i]["lamport"] == ops[j]["lamport"]) == (stock_lamports[i] == stock_lamports[j])
    assert len(seen_ops) > 20                                            # numbers really differ from learner to learner


def test_retried_copies_stay_identical_and_conflicting_copies_stay_different():
    dup = next(c for s, c in CASES if s["id"] == "duplicate-ids" and c["id"] == "p2")
    for n in range(30):
        ops = roll(dup, seed_for(f"x{n}", "duplicate-ids", "p2", 0))
        retried = [o for o in ops if o["id"] == "ph-a:2"]
        assert len(retried) == 3 and all(o == retried[0] for o in retried)
    clash = next(c for s, c in CASES if s["id"] == "duplicate-ids" and c["id"] == "p3")
    for n in range(30):
        a, b = roll(clash, seed_for(f"y{n}", "duplicate-ids", "p3", 0))
        assert a["id"] == b["id"] and a["value"] != b["value"] and a["lamport"] < b["lamport"]


def test_the_prompt_only_quotes_numbers_of_the_learners_own_ops():
    import re
    for s in STAGES:
        for c in personalise(s, "someone", {}):
            assert "expected" not in c and "template" not in c and "reroll" not in c
            own = {o["lamport"] for o in c["ops"]} | {abs(o["value"]) for o in c["ops"] if o["kind"] == "adjust"}
            own |= {int(n) for o in c["ops"] if isinstance(o["value"], str) for n in re.findall(r"#(\d+)", o["value"])}
            quoted = {int(n) for n in re.findall(r"(?<![\w:-])\d+", c["prompt"])}
            assert quoted <= own, (s["id"], c["id"], quoted - own)
            for o in c["ops"]:
                if o["kind"] in ("create", "note") and c["id"] == "p1":
                    assert o["value"] in c["prompt"]


def test_example_numbers_come_from_the_learners_case():
    stage = STAGES[0]
    mine = next(c for c in personalise(stage, "learner-z", {}) if c["id"] == "p1")
    stock = next(c for c in stage["predict"]["cases"] if c["id"] == "p1")
    assert example_numbers(mine) != example_numbers(stock)
    assert answer(mine) in example_numbers(mine)


def test_a_stage_without_templated_cases_is_left_alone():
    assert personalise({"id": "x", "predict": {"prompt": "p"}}, "l") is None
    assert personalise({"id": "x", "predict": {"cases": [{"id": "p1", "ops": []}]}}, "l") is None
