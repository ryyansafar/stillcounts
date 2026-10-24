import pytest

from learn.grader import grade_bug, grade_explain, grade_predict, grade_write, load_stages

REAL = {
    1: '''
def dedupe(ops):
    best = {}
    for op in ops:
        _validate(op)
        cur = best.get(op.id)
        if cur is None or _total(op) > _total(cur):
            best[op.id] = op
    return [best[k] for k in sorted(best)]
''',
    2: '''
def quantity(ops):
    return sum(o.value for o in ops if o.kind == "adjust")
''',
    3: '''
def _lww(ops, default):
    return max(ops, key=_order).value if ops else default
''',
    4: '''
def oversold_items(state):
    return [i for i, s in state.items() if s["qty"] < 0 and s["alive"]]
''',
}

WRONG = {
    1: '''
def dedupe(ops):
    ops = list(ops)
    for op in ops:
        _validate(op)
    return ops
''',
    2: '''
def quantity(ops):
    return sum(abs(o.value) for o in ops if o.kind == "adjust")
''',
    3: '''
def _lww(ops, default):
    return min(ops, key=_order).value if ops else default
''',
    4: '''
def oversold_items(state):
    return [i for i, s in state.items() if s["qty"] <= 0 and s["alive"]]
''',
}


IDS = {1: "duplicate-ids", 2: "signed-adjust-sum", 3: "lamport-lww", 4: "oversell-flag"}


def _stage(i):
    return next(s for s in load_stages() if s["order"] == i)


# ---- predict ----

def test_predict_correct_and_wrong_guesses():
    for s in load_stages():
        for case in s["predict"]["cases"]:
            ok = grade_predict(s["id"], case["id"], case["expected"], reveal=True)
            assert ok["passed"] and ok["expected"] == case["expected"]
            assert "state" in ok
            bad = case["expected"] + 1 if isinstance(case["expected"], int) and not isinstance(case["expected"], bool) \
                else (not case["expected"] if isinstance(case["expected"], bool) else case["expected"] + "x")
            assert grade_predict(s["id"], case["id"], bad)["passed"] is False


def test_a_single_case_never_reveals_the_answer_unless_asked():
    case = _stage(1)["predict"]["cases"][0]
    wrong = grade_predict(1, case["id"], 999)
    assert wrong == {"passed": False, "error": None}
    assert set(grade_predict(1, case["id"], case["expected"])) == {"passed", "error"}


def test_a_wrong_whole_checkpoint_names_the_wrong_cases_and_nothing_else():
    stage = _stage(1)
    answers = {c["id"]: c["expected"] for c in stage["predict"]["cases"]}
    answers["p2"] = 12345
    r = grade_predict(1, answers)
    assert r["passed"] is False and r["wrong"] == ["p2"]
    assert r["cases"]["p1"] == {"passed": True, "error": None} and r["cases"]["p2"] == {"passed": False, "error": None}
    text = repr(r)
    assert "expected" not in text and "state" not in text
    for c in stage["predict"]["cases"]:
        assert f"'{c['expected']}'" not in text


def test_an_unparseable_guess_is_not_counted_as_a_wrong_case():
    r = grade_predict(1, {"p1": "lots", "p2": 16, "p3": 7})
    assert r["wrong"] == [] and r["cases"]["p1"]["error"]


def test_predict_grades_the_learners_own_cases_when_given():
    from learn.cases import personalise
    stage = _stage(1)
    mine = personalise(stage, "learner-x", {})
    from stillcounts.crdt import Op, merge
    answers = {c["id"]: merge([Op(**o) for o in c["ops"]])[c["item"]][c["field"]] for c in mine}
    assert grade_predict(1, answers, cases=mine)["passed"] is True
    stock = {c["id"]: c["expected"] for c in stage["predict"]["cases"]}
    assert grade_predict(1, stock, cases=mine)["passed"] is False            # the stock answers are not this learner's


def test_predict_accepts_text_input_from_a_form():
    s1 = _stage(1)["predict"]["cases"][0]
    assert grade_predict(1, s1["id"], f" {s1['expected']} ")["passed"]
    s4 = _stage(4)["predict"]["cases"][0]
    assert grade_predict(4, s4["id"], "yes" if s4["expected"] else "no")["passed"]


def test_predict_rejects_unparseable_guesses():
    s1 = _stage(1)["predict"]["cases"][0]
    r = grade_predict(1, s1["id"], "lots")
    assert r["passed"] is False and r["error"]
    assert grade_predict(1, s1["id"], True)["passed"] is False


def test_stage_can_be_named_by_id_order_or_order_text():
    case = _stage(2)["predict"]["cases"][0]
    for ref in (2, "2", IDS[2]):
        assert grade_predict(ref, case["id"], case["expected"])["passed"]


def test_predict_whole_checkpoint_needs_every_case_right():
    stage = _stage(3)
    answers = {c["id"]: c["expected"] for c in stage["predict"]["cases"]}
    r = grade_predict(IDS[3], answers)
    assert r["passed"] and all(c["passed"] for c in r["cases"].values())
    first = stage["predict"]["cases"][0]["id"]
    r = grade_predict(IDS[3], {**answers, first: "nope"})
    assert r["passed"] is False and r["cases"][first]["passed"] is False
    missing = dict(answers)
    missing.pop(first)
    r = grade_predict(IDS[3], missing)
    assert r["passed"] is False and r["cases"][first]["error"]
    r = grade_predict(IDS[3], 42)
    assert r["passed"] is False and r["error"]


def test_predict_unknown_ids_raise():
    with pytest.raises(KeyError):
        grade_predict(9, "p1", 1)
    with pytest.raises(KeyError):
        grade_predict("nope", "p1", 1)
    with pytest.raises(KeyError):
        grade_predict(1, "nope", 1)


# ---- find the bug ----

def _right(bug):
    return {"option": bug["answer"], "line": bug["line_index"]}


def test_bug_needs_the_changed_line_and_the_cause_together():
    for s in load_stages():
        bug = s["bug"]
        ok = grade_bug(s["id"], _right(bug))
        assert ok["passed"] and ok["mutant"] == bug["mutant"] and ok["explanation"]
        assert ok["counterexample"] == bug["counterexample"]
        wrong = next(o["id"] for o in bug["options"] if o["id"] != bug["answer"])
        other_line = (bug["line_index"] + 1) % len(bug["excerpt"])
        assert grade_bug(s["id"], {"option": wrong, "line": bug["line_index"]})["passed"] is False
        assert grade_bug(s["id"], {"option": bug["answer"], "line": other_line})["passed"] is False
        assert grade_bug(s["id"], bug["answer"])["passed"] is False         # a bare option is not enough


def test_a_wrong_bug_answer_reveals_nothing():
    for s in load_stages():
        bug = s["bug"]
        r = grade_bug(s["id"], {"option": "zz", "line": 99})
        assert set(r) == {"passed", "error"}
        assert bug["answer"] not in repr(r).split() and bug["explanation"] not in repr(r)


def test_bug_accepts_the_mutant_name_with_the_right_line():
    bug = _stage(1)["bug"]
    assert grade_bug(1, {"option": "merge-skips-dedupe", "line": bug["line_index"]})["passed"]
    assert grade_bug(1, {"option": "merge-skips-dedupe", "line": True})["passed"] is False


# ---- write ----

@pytest.mark.parametrize("stage_id", [1, 2, 3, 4])
def test_write_real_function_passes_the_referee(stage_id):
    r = grade_write(stage_id, REAL[stage_id])
    assert r["passed"], r
    assert r["counterexample"] is None and r["error"] is None


@pytest.mark.parametrize("stage_id", [1, 2, 3, 4])
def test_write_wrong_function_fails_with_a_counterexample(stage_id):
    r = grade_write(stage_id, WRONG[stage_id])
    assert r["passed"] is False
    cx = r["counterexample"]
    assert cx["ops"] and cx["expected"] != cx["got"]
    assert r["error"]


def test_write_the_untouched_stub_fails_cleanly():
    for s in load_stages():
        r = grade_write(s["id"], s["write"]["stub"])
        assert r["passed"] is False and "NotImplementedError" in r["error"]


def test_write_missing_validation_is_caught_by_the_referee():
    code = '''
def dedupe(ops):
    best = {}
    for op in ops:
        cur = best.get(op.id)
        if cur is None or _total(op) > _total(cur):
            best[op.id] = op
    return [best[k] for k in sorted(best)]
'''
    r = grade_write(1, code)
    assert r["passed"] is False and "zero adjustment" in r["error"]


def test_write_infinite_loop_times_out():
    r = grade_write(2, "def quantity(ops):\n    while True:\n        pass\n", timeout=2)
    assert r["passed"] is False and "timed out" in r["error"]


def test_write_syntax_error():
    r = grade_write(2, "def quantity(ops)\n    return 0\n")
    assert r["passed"] is False and "SyntaxError" in r["error"]


def test_write_wrong_function_name():
    r = grade_write(2, "def total(ops):\n    return 0\n")
    assert r["passed"] is False and "quantity" in r["error"]


def test_write_may_not_import_the_real_merge():
    code = "def quantity(ops):\n    import stillcounts.crdt\n    return 0\n"
    r = grade_write(2, code)
    assert r["passed"] is False and "import" in r["error"]
    code = "from stillcounts import crdt\n\ndef quantity(ops):\n    return 0\n"
    assert grade_write(2, code)["passed"] is False


def test_write_printing_does_not_break_the_grader():
    code = 'def quantity(ops):\n    print("hello")\n    return sum(o.value for o in ops if o.kind == "adjust")\n'
    assert grade_write(2, code)["passed"]


# ---- explain ----

def _numbers(stage_id):
    from learn.cases import example_numbers
    stage = next(x for x in load_stages() if x["id"] == stage_id or x["order"] == stage_id)
    case = next(c for c in stage["predict"]["cases"] if c["id"] == stage["explain"]["example_case"])
    return sorted(example_numbers(case))


MODEL = {
    1: ("Every operation has a unique id. A flaky network makes the phone retry, so the same op can arrive twice, and "
        "counting both copies of {a} and {b} blankets would give the wrong total. The merge dedupes by id and keeps "
        "each once. So the result is the same in any order."),
    2: ("Each op is a signed change such as {a} minus {b}, not an absolute count. Addition is commutative so any order "
        "works. When two phones are offline both changes count instead of one overwriting the other."),
    3: ("We use a Lamport logical clock because the phone wall clock can be skewed. The highest lamport wins, so {a} "
        "beats {b}. A tie is broken by the replica id so every phone agrees."),
    4: ("Two phones offline both handed out the last blankets, and both handouts really happened. Merging them makes "
        "the stock negative, for example {a} less {b} below zero. We flag it for a human instead of hiding it. "
        "Zero or deleted items are not flagged."),
}


def _model(stage_id):
    nums = _numbers(stage_id)
    return MODEL[stage_id].format(a=nums[0], b=nums[1])


@pytest.mark.parametrize("stage_id", [1, 2, 3, 4])
def test_explain_model_answers_pass(stage_id):
    r = grade_explain(stage_id, _model(stage_id))
    assert r["passed"] is True, r
    assert r["words"] >= r["min_words"] == 25 and r["reasons"] == []


def test_explain_full_answer_covers_every_point():
    r = grade_explain(1, _model(1))
    assert r["missed"] == [] and r["score"] == 1.0
    assert len(r["covered"]) == len(_stage(1)["explain"]["rubric"])


def test_explain_keyword_soup_does_not_pass():
    soup = "id retry once order"
    r = grade_explain(1, soup)
    assert r["passed"] is False and r["covered"] == []
    spaced = ". ".join(soup.split())
    assert grade_explain(1, spaced)["covered"] == []                             # one word per sentence is no sentence
    stuffed = "id unique retry duplicates once dedupe order idempotent converge " * 4
    r = grade_explain(1, stuffed)
    assert r["passed"] is False and len(r["covered"]) <= 1                       # one sentence can carry one point


def test_explain_needs_25_words_even_when_every_point_is_covered():
    short = "Each op has an id. Retries resend it. We keep one. Order is irrelevant."
    r = grade_explain(1, short)
    assert r["passed"] is False and any("at least 25 words" in why for why in r["reasons"])


def test_explain_counts_one_sentence_per_point():
    one_sentence = ("Every operation has a unique id and a retry delivers it twice but the merge keeps one copy so the "
                    "order never matters and the total stays the same whatever happens on the network today.")
    r = grade_explain(1, one_sentence)
    assert len(r["covered"]) == 1 and r["passed"] is False


def test_explain_wants_the_learners_own_numbers_where_the_rubric_asks():
    nums = _numbers(1)
    without = ("Every operation has a unique id. A flaky network makes the phone retry, so the same op can arrive "
               "twice and would be counted twice. The merge dedupes by id and keeps each once. So the result is the "
               "same in any order, however many phones send it.")
    r = grade_explain(1, without)
    assert r["passed"] is False
    assert any("worked example" in why for why in r["reasons"])
    assert [m["id"] for m in r["missed"]] == ["duplicates"] and "your own case p1" in r["missed"][0]["needs"]
    wrong_numbers = without.replace("counted twice", "counted twice, 777 and 888")
    assert grade_explain(1, wrong_numbers)["passed"] is False
    one_number = without.replace("counted twice", f"counted twice, {nums[0]}")
    assert grade_explain(1, one_number)["passed"] is False
    two = without.replace("counted twice", f"counted twice, {nums[0]} and {nums[1]}")
    assert grade_explain(1, two)["passed"] is True


def test_explain_numbers_are_checked_against_the_learners_own_cases():
    from learn.cases import example_numbers, personalise
    mine = personalise(_stage(1), "learner-y", {})
    nums = sorted(example_numbers(next(c for c in mine if c["id"] == "p1")))
    stock = _numbers(1)
    assert nums != stock
    text = MODEL[1].format(a=nums[0], b=nums[1])
    assert grade_explain(1, text, cases=mine)["passed"] is True
    assert grade_explain(1, MODEL[1].format(a=stock[0], b=stock[1]), cases=mine)["passed"] is False


def test_explain_empty_answer():
    r = grade_explain(3, "")
    assert r["covered"] == [] and r["passed"] is False and r["score"] == 0.0


def test_explain_matches_whole_words_and_stems_only():
    # "valid" must not count as "id"; "retried" counts for "retr*"
    r = grade_explain(1, "valid")
    assert r["covered"] == []
    r = grade_explain(1, "It was retried again and again today.")
    assert [p["id"] for p in r["covered"]] == ["duplicates"] or r["covered"] == []   # a stem match needs the example numbers too
    r = grade_explain(3, "The phone wall clock was skewed by a few minutes.")
    assert [p["id"] for p in r["covered"]] == ["wall-clock"]


def test_write_worker_in_process():
    from learn.grader import _write_worker
    assert _write_worker(IDS[2], REAL[2])["passed"]
    r = _write_worker(IDS[2], WRONG[2])
    assert r["passed"] is False and r["counterexample"]
    r = _write_worker(IDS[2], "def quantity(ops):\n    raise ValueError('no')\n")
    assert r["passed"] is False and "ValueError" in r["error"]


def test_cli_prints_public_stages(capsys):
    import json
    from learn.grader import _main, public_stages
    _main([])
    assert json.loads(capsys.readouterr().out) == public_stages()
