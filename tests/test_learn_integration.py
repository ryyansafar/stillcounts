"""The learn API wired to the real grader and the real stages (no fakes): locked answers never leak, correct
answers unlock the real code, and a wrong function does not."""
import json

import pytest
from fastapi.testclient import TestClient

from learn.grader import load_stages
from server.app import create_app
from tests.test_learn_grader import REAL, WRONG

H = {"X-Learner-Id": "ada"}
STAGE_IDS = {1: "duplicate-ids", 2: "signed-adjust-sum", 3: "lamport-lww", 4: "oversell-flag"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def _full(order):
    return next(s for s in load_stages() if s["order"] == order)


def test_locked_stage_list_leaks_no_answers_or_real_code(client):
    body = client.get("/api/learn/stages", headers=H).json()
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


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_earning_all_three_checkpoints_unlocks_the_real_code(client, order):
    stage, sid = _full(order), STAGE_IDS[order]
    guesses = {c["id"]: c["expected"] for c in stage["predict"]["cases"]}
    r = client.post(f"/api/learn/{sid}/predict", json={"answer": guesses}, headers=H).json()
    assert r["passed"] is True and r["unlocked"] is False and r["code"] is None
    r = client.post(f"/api/learn/{sid}/bug", json={"answer": stage["bug"]["answer"]}, headers=H).json()
    assert r["passed"] is True and r["unlocked"] is False
    r = client.post(f"/api/learn/{sid}/write", json={"source": REAL[order]}, headers=H).json()
    assert r["passed"] is True, r["result"]
    assert r["unlocked"] is True and r["code"] == stage["code"]
    stages = client.get("/api/learn/stages", headers=H).json()["stages"]
    assert next(s for s in stages if s["id"] == sid)["unlocked"] is True


def test_a_wrong_function_gets_a_counterexample_and_stays_locked(client):
    r = client.post("/api/learn/lamport-lww/write", json={"source": WRONG[3]}, headers=H).json()
    assert r["passed"] is False and r["unlocked"] is False and r["code"] is None
    assert r["result"]["counterexample"]


def test_a_wrong_predict_guess_does_not_pass(client):
    stage = _full(2)
    wrong = {c["id"]: 12345 for c in stage["predict"]["cases"]}
    r = client.post("/api/learn/signed-adjust-sum/predict", json={"answer": wrong}, headers=H).json()
    assert r["passed"] is False and r["progress"]["predict"] is False


def test_explain_reveals_the_rubric_only_after_submitting(client):
    sid = "duplicate-ids"
    r = client.post(f"/api/learn/{sid}/explain", json={"text": "retries resend the same unique id so it is deduped"}, headers=H).json()
    assert r["result"]["covered"] and r["progress"]["explain"] is True
    explain = next(s for s in client.get("/api/learn/stages", headers=H).json()["stages"] if s["id"] == sid)["explain"]
    assert "rubric" in explain
