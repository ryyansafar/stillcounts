"""The learn API wired to the real grader and the real stages (no fakes): locked answers never leak, correct
answers unlock the real code, and a wrong function does not."""
import json

import pytest
from fastapi.testclient import TestClient

from learn.grader import load_stages
from server.app import create_app
from tests.test_learn_grader import REAL, WRONG

STAGE_IDS = {1: "duplicate-ids", 2: "signed-adjust-sum", 3: "lamport-lww", 4: "oversell-flag"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def _full(order):
    return next(s for s in load_stages() if s["order"] == order)


def test_locked_stage_list_leaks_no_answers_or_real_code(client):
    body = client.get("/api/learn/stages").json()
    text = json.dumps(body)
    assert [s["id"] for s in body["stages"]] == list(STAGE_IDS.values())
    assert all(s["unlocked"] is False and s["code"] is None for s in body["stages"])
    for stage in load_stages():
        assert json.dumps(stage["code"])[1:-1] not in text
        assert stage["bug"]["mutant"] not in text
        assert all(p["point"] not in text for p in stage["explain"]["rubric"])
    for s in body["stages"]:
        assert not {"answer", "mutant", "explanation", "counterexample"} & set(s["bug"])
        assert not {"hook", "rubric"} & (set(s["write"]) | set(s["explain"]))
        assert all("expected" not in c for c in s["predict"]["cases"])
    first = body["stages"][0]["predict"]["cases"][0]
    assert first["field"] and first["ops"]


def my_cases(client, sid):
    return next(s for s in client.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["predict"]["cases"]


def my_answers(client, sid):
    """What a learner who ran the merge by hand would type for their own cases."""
    from learn.cases import answer
    return {c["id"]: answer(c) for c in my_cases(client, sid)}


def bug_answer(client, sid, stage):
    return {"answer": {"option": stage["bug"]["answer"], "line": stage["bug"]["line_index"]}}


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_earning_all_three_checkpoints_unlocks_the_real_code(client, order):
    stage, sid = _full(order), STAGE_IDS[order]
    r = client.post(f"/api/learn/{sid}/predict", json={"answer": my_answers(client, sid)}).json()
    assert r["passed"] is True and r["unlocked"] is False and r["code"] is None
    assert "expected" not in json.dumps(r) and "state" not in r["result"]
    r = client.post(f"/api/learn/{sid}/bug", json=bug_answer(client, sid, stage)).json()
    assert r["passed"] is True and r["unlocked"] is False
    assert r["result"]["explanation"] == stage["bug"]["explanation"] and "explanation" not in r   # the client must read result.explanation
    r = client.post(f"/api/learn/{sid}/write", json={"source": REAL[order]}).json()
    assert r["passed"] is True, r["result"]
    assert r["unlocked"] is True and r["code"] == stage["code"]
    stages = client.get("/api/learn/stages").json()["stages"]
    mine = next(s for s in stages if s["id"] == sid)
    assert mine["unlocked"] is True
    assert all("expected" in c for c in mine["predict"]["cases"])        # the merged answers appear once the stage is earned


def test_the_stock_answers_do_not_pass_someone_elses_cases(client):
    stage = _full(1)
    stock = {c["id"]: c["expected"] for c in stage["predict"]["cases"]}
    r = client.post("/api/learn/duplicate-ids/predict", json={"answer": stock}).json()
    assert r["passed"] is False


def test_two_learners_get_different_numbers_for_the_same_case(client, tmp_path):
    other = TestClient(client.app)
    a, b = my_cases(client, "duplicate-ids"), my_cases(other, "duplicate-ids")
    assert [c["id"] for c in a] == [c["id"] for c in b]
    assert [c["ops"] for c in a] != [c["ops"] for c in b]
    assert my_cases(client, "duplicate-ids") == a                              # stable for one learner across requests


def test_a_wrong_predict_names_the_case_and_replaces_it(client):
    sid = "signed-adjust-sum"
    before = my_cases(client, sid)
    answers = my_answers(client, sid)
    answers["p2"] = answers["p2"] + 1
    r = client.post(f"/api/learn/{sid}/predict", json={"answer": answers}).json()
    assert r["passed"] is False and r["result"]["wrong"] == ["p2"]
    text = json.dumps(r)
    assert "expected" not in text and "state" not in r["result"]
    after = my_cases(client, sid)
    assert [c for c in after if c["id"] == "p2"][0]["ops"] != [c for c in before if c["id"] == "p2"][0]["ops"]
    assert [c for c in after if c["id"] == "p1"] == [c for c in before if c["id"] == "p1"]      # right cases stay put
    assert [c["id"] for c in r["result"]["new_cases"]] == ["p2"]
    assert r["result"]["new_cases"][0]["ops"] == [c for c in after if c["id"] == "p2"][0]["ops"]
    assert "expected" not in r["result"]["new_cases"][0]


def test_a_wrong_guess_keeps_rerolling_so_a_replay_of_old_answers_fails(client):
    sid = "duplicate-ids"
    first = my_answers(client, sid)
    bad = {**first, "p1": first["p1"] + 1}
    client.post(f"/api/learn/{sid}/predict", json={"answer": bad})
    r = client.post(f"/api/learn/{sid}/predict", json={"answer": first}).json()          # the old p1 answer no longer fits
    assert r["passed"] is False and r["result"]["wrong"] == ["p1"]


def test_an_offline_pass_counts_only_if_the_server_agrees(client):
    """The page grades offline against the cases it cached and replays the answers later: the server re-checks them
    against the learner's current cases, so answers for stale numbers do not pass."""
    sid = "signed-adjust-sum"
    cached = my_cases(client, sid)
    from learn.cases import answer
    offline = {c["id"]: answer(c) for c in cached}
    client.post(f"/api/learn/{sid}/predict", json={"answer": {**offline, "p3": offline["p3"] + 1}})   # a wrong guess re-rolls p3
    r = client.post(f"/api/learn/{sid}/predict", json={"answer": offline}).json()
    assert r["passed"] is False and r["progress"]["predict"] is False


def test_find_the_bug_needs_the_changed_line_and_the_cause(client):
    sid, stage = "lamport-lww", _full(3)
    bug = stage["bug"]
    shown = next(s for s in client.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["bug"]
    assert 3 <= len(shown["excerpt"]) <= 5 and "line_index" not in shown and "snippet" not in shown and "line" not in shown
    only_cause = client.post(f"/api/learn/{sid}/bug", json={"answer": {"option": bug["answer"], "line": (bug["line_index"] + 1) % 5}})
    assert only_cause.json()["passed"] is False
    result = only_cause.json()["result"]
    assert not {"answer", "mutant", "explanation", "counterexample"} & set(result)       # a miss reveals nothing


def test_a_wrong_find_the_bug_pick_locks_the_options_for_30_seconds_and_shuffles_them(tmp_path):
    from tests.test_learn_api import Clock
    clock = Clock()
    c = TestClient(create_app(tmp_path / "ops.jsonl", learn_clock=clock))
    sid, bug = "lamport-lww", _full(3)["bug"]
    first = next(s for s in c.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["bug"]["options"]
    r = c.post(f"/api/learn/{sid}/bug", json={"answer": {"option": "a", "line": 99}})
    assert r.json()["passed"] is False and r.json()["result"]["locked_for"] == 30
    again = c.post(f"/api/learn/{sid}/bug", json={"answer": {"option": bug["answer"], "line": bug["line_index"]}})
    assert again.status_code == 429 and int(again.headers["retry-after"]) >= 29
    listed = next(s for s in c.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["bug"]
    assert 29 <= listed["locked_for"] <= 31
    assert sorted(o["id"] for o in listed["options"]) == sorted(o["id"] for o in first)
    orders = {tuple(o["id"] for o in listed["options"])}
    for _ in range(6):                                                         # each miss reshuffles
        clock.now += 31
        c.post(f"/api/learn/{sid}/bug", json={"answer": {"option": "a", "line": 99}})
        orders.add(tuple(o["id"] for o in next(s for s in c.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["bug"]["options"]))
    assert len(orders) > 1
    clock.now += 31
    ok = c.post(f"/api/learn/{sid}/bug", json={"answer": {"option": bug["answer"], "line": bug["line_index"]}})
    assert ok.status_code == 200 and ok.json()["passed"] is True


def test_a_wrong_function_gets_a_counterexample_and_stays_locked(client):
    r = client.post("/api/learn/lamport-lww/write", json={"source": WRONG[3]}).json()
    assert r["passed"] is False and r["unlocked"] is False and r["code"] is None
    assert r["result"]["counterexample"]
    assert {"ops", "expected", "got"} <= set(r["result"]["counterexample"]) and r["result"]["error"]   # the client shows both


def test_a_wrong_predict_guess_does_not_pass(client):
    stage = _full(2)
    wrong = {c["id"]: 12345 for c in stage["predict"]["cases"]}
    r = client.post("/api/learn/signed-adjust-sum/predict", json={"answer": wrong}).json()
    assert r["passed"] is False and r["progress"]["predict"] is False


def _model_text(client, sid):
    from learn.cases import answer, example_numbers
    case = next(c for c in my_cases(client, sid) if c["id"] == "p1")
    n = sorted(example_numbers(case))
    texts = {
        "duplicate-ids": ("Every operation has a unique id. A flaky network makes the phone retry, so the same op can "
                          "arrive twice, and counting both copies of {a} and {b} blankets gives the wrong total. The "
                          "merge dedupes by id and keeps each once. So the result is the same in any order."),
        "signed-adjust-sum": ("Each op is a signed change such as {a} minus {b}, not an absolute count. Addition is "
                              "commutative so any order works. When two phones are offline both changes count instead "
                              "of one overwriting the other."),
    }
    return texts[sid].format(a=n[0], b=n[1])


def test_explain_that_fails_is_not_marked_and_does_not_show_the_keywords(client):
    sid = "duplicate-ids"
    r = client.post(f"/api/learn/{sid}/explain", json={"text": "retries resend the same unique id so it is deduped"}).json()
    assert r["passed"] is False and r["progress"]["explain"] is False
    assert r["result"]["reasons"] and "keywords" not in json.dumps(r)
    explain = next(s for s in client.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["explain"]
    assert "rubric" not in explain


def test_a_good_explain_is_marked_and_reveals_the_rubric_without_keywords(client):
    sid = "duplicate-ids"
    r = client.post(f"/api/learn/{sid}/explain", json={"text": _model_text(client, sid)}).json()
    assert r["passed"] is True and r["progress"]["explain"] is True and r["result"]["covered"]
    explain = next(s for s in client.get("/api/learn/stages").json()["stages"] if s["id"] == sid)["explain"]
    assert explain["rubric"] and all("keywords" not in p and p["point"] for p in explain["rubric"])


@pytest.mark.parametrize("sid", ["duplicate-ids", "signed-adjust-sum"])
def test_explain_alone_never_unlocks_a_stage(client, sid):
    r = client.post(f"/api/learn/{sid}/explain", json={"text": _model_text(client, sid)}).json()
    assert r["passed"] is True
    assert r["unlocked"] is False and r["code"] is None and r["progress"]["predict"] is False
    stage = next(s for s in client.get("/api/learn/stages").json()["stages"] if s["id"] == sid)
    assert stage["unlocked"] is False and stage["code"] is None


def test_the_explain_example_uses_the_learners_current_numbers(client):
    sid = "duplicate-ids"
    from learn.cases import example_numbers
    mine = sorted(example_numbers(next(c for c in my_cases(client, sid) if c["id"] == "p1")))
    stock = sorted(example_numbers(next(c for c in _full(1)["predict"]["cases"] if c["id"] == "p1")))
    assert mine != stock
    stock_text = _model_text(client, sid).replace(str(mine[0]), str(stock[0])).replace(str(mine[1]), str(stock[1]))
    assert client.post(f"/api/learn/{sid}/explain", json={"text": stock_text}).json()["passed"] is False
