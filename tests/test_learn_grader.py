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
            ok = grade_predict(s["id"], case["id"], case["expected"])
            assert ok["passed"] and ok["expected"] == case["expected"]
            assert "state" in ok
            bad = case["expected"] + 1 if isinstance(case["expected"], int) and not isinstance(case["expected"], bool) \
                else (not case["expected"] if isinstance(case["expected"], bool) else case["expected"] + "x")
            assert grade_predict(s["id"], case["id"], bad)["passed"] is False


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

def test_bug_correct_and_wrong_choice():
    for s in load_stages():
        bug = s["bug"]
        ok = grade_bug(s["id"], bug["answer"])
        assert ok["passed"] and ok["mutant"] == bug["mutant"] and ok["explanation"]
        assert ok["counterexample"] == bug["counterexample"]
        wrong = next(o["id"] for o in bug["options"] if o["id"] != bug["answer"])
        assert grade_bug(s["id"], wrong)["passed"] is False


def test_bug_accepts_the_mutant_name():
    assert grade_bug(1, "merge-skips-dedupe")["passed"]


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

def test_explain_full_answer_covers_every_point():
    text = ("Every operation has a unique id. A flaky network makes the phone retry, so the same op "
            "can arrive twice. The merge dedupes by id and keeps it once, so the result is the same "
            "in any order.")
    r = grade_explain(1, text)
    assert r["missed"] == [] and r["passed"] and r["score"] == 1.0
    assert len(r["covered"]) == len(_stage(1)["explain"]["rubric"])


def test_explain_partial_answer_lists_missed_points():
    r = grade_explain(1, "Each op has an id.")
    assert r["passed"] is False
    assert len(r["covered"]) == 1 and r["missed"]
    assert all({"id", "point"} <= p.keys() for p in r["covered"] + r["missed"])


def test_explain_empty_answer():
    r = grade_explain(3, "")
    assert r["covered"] == [] and r["passed"] is False and r["score"] == 0.0


def test_explain_matches_whole_words_and_stems_only():
    # "valid" must not count as "id"; "retried" counts for "retr*"
    r = grade_explain(1, "valid")
    assert r["covered"] == []
    r = grade_explain(1, "It was retried.")
    assert [p["id"] for p in r["covered"]] == ["duplicates"]


@pytest.mark.parametrize("stage_id,text", [
    (2, "Each op is a signed change. Addition is commutative so any order works, and when two phones "
        "are offline both changes count instead of one overwriting the other."),
    (3, "We use a Lamport logical clock because the phone wall clock can be skewed. The highest "
        "lamport wins and a tie is broken by the replica id."),
    (4, "Two phones offline both handed out the last blankets, and both handouts really happened, so "
        "the stock goes negative. We flag it for a human instead of hiding it; zero or deleted items "
        "are not flagged."),
])
def test_explain_model_answers_pass(stage_id, text):
    r = grade_explain(stage_id, text)
    assert r["missed"] == [], r["missed"]


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
