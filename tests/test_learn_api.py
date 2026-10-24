"""The 'Earn the Code' API: locked code stays hidden until predict, bug and write all pass for that learner."""
import json
import types

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.learn_api import MAX_SOURCE_BYTES, WRITE_LIMIT

STAGES = [
    {"id": "lww", "title": "Last writer wins", "summary": "Who wins a name clash?",
     "code": "def _lww(ops, default):\n    return max(ops, key=_order).value if ops else default\n",
     "predict": {"prompt": "Two phones rename rice. Which name wins?", "expected": "Rice B"},
     "bug": {"prompt": "What is wrong with this merge?", "mutant_source": "return min(ops, key=_order).value", "answer": "lww-picks-earliest"},
     "write": {"prompt": "Write _lww.", "signature": "def _lww(ops, default):", "solution": "secret"},
     "explain": {"prompt": "Why not the wall clock?", "rubric": ["mentions lamport", "mentions clock skew"]}},
    {"id": "counter", "title": "Counting", "code": "SECRET COUNTER CODE",
     "predict": {"prompt": "p"}, "bug": {"prompt": "b"}, "write": {"prompt": "w"}, "explain": {"prompt": "e"}},
]
SECRETS = ("max(ops, key=_order)", "SECRET COUNTER CODE", "Rice B", "lww-picks-earliest", "secret", "mentions lamport")


class FakeGrader:
    """Plain-code fake: an answer of 'right' passes, anything else fails. Records every call."""

    def __init__(self):
        self.calls = []

    def _grade(self, kind, stage, value):
        self.calls.append((kind, stage, value))
        return {"passed": value == "right", "feedback": f"{kind} graded"}

    def grade_predict(self, stage, answer):
        return self._grade("predict", stage, answer)

    def grade_bug(self, stage, answer):
        return self._grade("bug", stage, answer)

    def grade_write(self, stage, source):
        return self._grade("write", stage, source)

    def grade_explain(self, stage, text):
        self.calls.append(("explain", stage, text))
        return {"passed": True, "score": 1, "rubric": ["mentions lamport", "mentions clock skew"]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    stages = tmp_path / "stages.json"
    stages.write_text(json.dumps(STAGES))
    grader, clock = FakeGrader(), Clock()

    def make():
        app = create_app(tmp_path / "ops.jsonl", learn_grader=grader, learn_stages=stages, learn_clock=clock)
        return TestClient(app)

    client = make()

    def other():                                    # a second browser: same server, its own cookie jar
        return TestClient(client.app)

    return types.SimpleNamespace(client=client, make=make, other=other, grader=grader, clock=clock, tmp=tmp_path)


COOKIE = "sc_learner"


def post(c, stage, kind, value, headers=None):
    key = {"predict": "answer", "bug": "answer", "write": "source", "explain": "text"}[kind]
    return c.post(f"/api/learn/{stage}/{kind}", json={key: value}, headers=headers or {})


def earn(env, stage="lww", client=None):
    for kind in ("predict", "bug", "write"):
        assert post(client or env.client, stage, kind, "right").json()["passed"] is True


def stages_of(c, **kw):
    return c.get("/api/learn/stages", **kw).json()["stages"]


def test_stages_list_hides_locked_code_and_answers(env):
    r = env.client.get("/api/learn/stages")
    assert r.status_code == 200
    stages = r.json()["stages"]
    assert [s["id"] for s in stages] == ["lww", "counter"]
    assert all(s["unlocked"] is False and s["code"] is None for s in stages)
    assert stages[0]["predict"]["prompt"].startswith("Two phones")
    assert stages[0]["bug"]["mutant_source"].startswith("return min")       # the mutant is shown, its name is not
    assert stages[0]["write"]["signature"] == "def _lww(ops, default):"
    body = r.text
    for secret in SECRETS:
        assert secret not in body


def test_stages_list_works_without_a_learner_id(env):
    stages = env.client.get("/api/learn/stages").json()["stages"]
    assert all(s["code"] is None and s["progress"] == {"predict": False, "bug": False, "write": False, "explain": False}
               for s in stages)


def test_a_checkpoint_calls_the_grader_with_the_stage_id(env):
    r = post(env.client, "lww", "predict", "Rice A")
    assert r.status_code == 200
    assert r.json()["passed"] is False and r.json()["result"]["feedback"] == "predict graded"
    assert env.grader.calls == [("predict", "lww", "Rice A")]


def test_code_unlocks_only_after_predict_bug_and_write_pass(env):
    c = env.client
    for kind in ("predict", "bug"):
        r = post(c, "lww", kind, "right").json()
        assert r["unlocked"] is False and r["code"] is None
    assert "max(ops, key=_order)" not in c.get("/api/learn/stages").text
    r = post(c, "lww", "write", "right").json()
    assert r["unlocked"] is True and r["code"].startswith("def _lww")
    stage = c.get("/api/learn/stages").json()["stages"][0]
    assert stage["unlocked"] is True and stage["code"].startswith("def _lww")
    assert stage["write"]["solution"] == "secret"                   # once earned, nothing is hidden


def test_explain_is_not_required_to_unlock_and_reveals_the_rubric_after(env):
    c = env.client
    stage = c.get("/api/learn/stages").json()["stages"][0]
    assert "rubric" not in stage["explain"]
    r = post(c, "lww", "explain", "Lamport time, because phone clocks drift").json()
    assert r["result"]["rubric"] == ["mentions lamport", "mentions clock skew"] and r["unlocked"] is False
    stage = c.get("/api/learn/stages").json()["stages"][0]
    assert stage["progress"]["explain"] is True and stage["explain"]["rubric"] == ["mentions lamport", "mentions clock skew"]
    assert stage["code"] is None


def test_a_failed_attempt_never_undoes_a_pass(env):
    c = env.client
    post(c, "lww", "predict", "right")
    post(c, "lww", "predict", "wrong")
    assert c.get("/api/learn/stages").json()["stages"][0]["progress"]["predict"] is True


def test_progress_is_per_learner(env):
    earn(env)
    other = stages_of(env.other())[0]
    assert other["unlocked"] is False and other["code"] is None
    assert env.client.get("/api/learn/stages").json()["stages"][0]["unlocked"] is True


def test_progress_is_stored_in_a_json_file_and_survives_a_restart(env):
    earn(env)
    saved = json.loads((env.tmp / "learn_progress.json").read_text())
    mine = env.client.cookies.get(COOKIE)
    assert list(saved) == [mine]
    assert saved[mine]["lww"] == {"predict": True, "bug": True, "write": True, "explain": False}
    restarted = env.make()
    restarted.cookies.set(COOKIE, mine)
    again = stages_of(restarted)[0]
    assert again["unlocked"] is True and again["code"].startswith("def _lww")


def test_a_corrupt_progress_file_starts_fresh(env):
    (env.tmp / "learn_progress.json").write_text("{not json")
    c = env.make()
    assert c.get("/api/learn/stages").json()["stages"][0]["unlocked"] is False
    assert post(c, "lww", "predict", "right").status_code == 200


def test_unlocking_one_stage_does_not_unlock_another(env):
    earn(env)
    stages = env.client.get("/api/learn/stages").json()["stages"]
    assert stages[1]["unlocked"] is False and "SECRET COUNTER CODE" not in json.dumps(stages)


def test_unknown_stage_and_unknown_checkpoint(env):
    assert post(env.client, "nope", "predict", "x").status_code == 404
    assert env.client.post("/api/learn/lww/hack", json={"answer": "x"}).status_code == 404
    assert env.grader.calls == []


def test_the_server_issues_the_learner_id_in_an_httponly_cookie(env):
    r = env.client.get("/api/learn/stages")
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(COOKIE + "=")
    assert "httponly" in set_cookie.lower() and "samesite=lax" in set_cookie.lower() and "path=/" in set_cookie.lower()
    first = env.client.cookies.get(COOKIE)
    assert len(first) >= 22
    assert COOKIE not in env.client.get("/api/learn/stages").headers.get("set-cookie", "")   # kept, not re-issued
    assert env.client.cookies.get(COOKIE) == first
    assert env.other().get("/api/learn/stages").cookies.get(COOKIE) != first                  # random per browser


def test_a_checkpoint_post_without_a_cookie_still_works_and_issues_one(env):
    r = post(env.client, "lww", "predict", "right")
    assert r.status_code == 200 and COOKIE in r.headers["set-cookie"]
    assert post(env.client, "lww", "predict", "right").json()["progress"]["predict"] is True


def test_the_x_learner_id_header_is_ignored(env):
    earn(env)
    victim = {"X-Learner-Id": "victim"}
    assert stages_of(env.client, headers=victim)[0]["unlocked"] is True        # still me, whatever the header says
    thief = env.other()
    assert stages_of(thief, headers={"X-Learner-Id": env.client.cookies.get(COOKIE)})[0]["unlocked"] is False
    post(thief, "lww", "predict", "right", victim)
    assert "victim" not in json.loads((env.tmp / "learn_progress.json").read_text())


@pytest.mark.parametrize("forged", ["", "a" * 65, "bad id", "../etc", "a.b"])
def test_a_malformed_cookie_is_replaced_not_trusted(env, forged):
    r = env.client.get("/api/learn/stages", headers={"Cookie": f"{COOKIE}={forged}"})
    assert r.status_code == 200 and COOKIE in r.headers["set-cookie"]
    assert forged == "" or forged not in r.headers["set-cookie"]


def test_learner_source_is_size_capped_before_it_runs(env):
    big = "x = 1\n" * (MAX_SOURCE_BYTES // 6 + 1)
    assert len(big.encode()) > MAX_SOURCE_BYTES
    assert post(env.client, "lww", "write", big).status_code == 413
    multibyte = "é" * (MAX_SOURCE_BYTES // 2 + 1)                     # few characters, too many bytes
    assert post(env.client, "lww", "write", multibyte).status_code == 413
    assert env.grader.calls == []


def test_other_answers_are_size_capped(env):
    assert post(env.client, "lww", "predict", "x" * 5000).status_code == 422
    assert post(env.client, "lww", "explain", "x" * 50000).status_code == 422
    assert env.grader.calls == []


def test_writes_are_rate_limited_per_learner(env):
    for _ in range(WRITE_LIMIT):
        assert post(env.client, "lww", "write", "wrong").status_code == 200
    r = post(env.client, "lww", "write", "right")
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}
    assert len(env.grader.calls) == WRITE_LIMIT
    assert post(env.other(), "lww", "write", "right").status_code == 200
    assert post(env.client, "lww", "predict", "right").status_code == 200    # only executing writes are limited
    env.clock.now += 61
    assert post(env.client, "lww", "write", "right").status_code == 200


def test_writes_are_rate_limited_across_learners(env):
    from server.learn_api import GLOBAL_WRITE_LIMIT
    for i in range(GLOBAL_WRITE_LIMIT):
        assert post(env.other(), "lww", "write", "wrong").status_code == 200
    assert post(env.other(), "lww", "write", "wrong").status_code == 429


def test_a_grader_error_is_a_clean_failure_not_a_crash(env):
    def boom(stage, source):
        raise RuntimeError("referee crashed")
    env.grader.grade_write = boom
    r = post(env.client, "lww", "write", "right")
    assert r.status_code == 200 and r.json()["passed"] is False and "error" in r.json()["result"]
    assert "referee crashed" not in r.text


def test_a_grader_result_without_a_boolean_pass_counts_as_failed(env):
    env.grader.grade_predict = lambda stage, answer: {"passed": "yes"}
    assert post(env.client, "lww", "predict", "right").json()["passed"] is False


def test_default_grader_is_learn_grader(tmp_path, monkeypatch):
    import learn.grader as real
    stages = tmp_path / "stages.json"
    stages.write_text(json.dumps({"stages": STAGES}))
    monkeypatch.setattr(real, "grade_predict", lambda stage, answer: {"passed": True, "via": "learn.grader"})
    c = TestClient(create_app(tmp_path / "ops.jsonl", learn_stages=stages))
    assert post(c, "lww", "predict", "anything").json()["result"]["via"] == "learn.grader"


def test_default_stages_file_loads(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    stages = c.get("/api/learn/stages").json()["stages"]
    assert stages and all(s["code"] is None and s["unlocked"] is False for s in stages)


def test_a_malformed_progress_entry_is_ignored(env):
    (env.tmp / "learn_progress.json").write_text(json.dumps({"ana": ["junk"], "ben": {"lww": "junk"}}))
    c = env.make()
    for who in ("ana", "ben"):
        c.cookies.set(COOKIE, who)
        assert stages_of(c)[0]["unlocked"] is False
        assert post(c, "lww", "predict", "right").json()["progress"]["predict"] is True


def test_predict_cases_show_their_ops_but_not_the_expected_result(env):
    stages = [{"id": "dup", "code": "x", "predict": {"cases": [
        {"name": "same id twice", "ops": [{"id": "a:1"}], "expected": {"rice": {"qty": 777}}}]}}]
    path = env.tmp / "cases.json"
    path.write_text(json.dumps({"stages": stages}))
    c = TestClient(create_app(env.tmp / "ops.jsonl", learn_grader=env.grader, learn_stages=path))
    r = c.get("/api/learn/stages")
    assert r.json()["stages"][0]["predict"]["cases"] == [{"name": "same id twice", "ops": [{"id": "a:1"}]}]
    assert "777" not in r.text


def test_real_stages_hide_answers_until_unlocked(tmp_path):
    from learn.grader import load_stages
    real = {s["id"]: s for s in load_stages()}
    c = TestClient(create_app(tmp_path / "ops.jsonl", learn_grader=FakeGrader()))
    for s in c.get("/api/learn/stages").json()["stages"]:
        src = real[s["id"]]
        assert s["code"] is None and s["predict"]["cases"] and s["bug"]["options"] and s["write"]["stub"]
        assert all("expected" not in case and "field" in case for case in s["predict"]["cases"])
        for hidden in ("mutant", "answer", "explanation", "counterexample"):
            assert hidden not in s["bug"]
        assert "hook" not in s["write"] and "rubric" not in s["explain"]
        assert src["code"] not in json.dumps(s)


# ---- the write endpoint is not open to the LAN by default ----

def _write_from(host, monkeypatch, tmp_path, allow=False):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.learn_api import learn_router
    if allow:
        monkeypatch.setenv("STILLCOUNTS_LEARN_WRITE_LAN", "1")
    else:
        monkeypatch.delenv("STILLCOUNTS_LEARN_WRITE_LAN", raising=False)
    app = FastAPI()
    app.include_router(learn_router(tmp_path / "p.json"))
    c = TestClient(app, client=(host, 5000))
    return c.post("/api/learn/signed-adjust-sum/write", json={"source": "def quantity(ops):\n    return 0\n"})


def test_write_from_a_lan_address_is_refused_by_default(monkeypatch, tmp_path):
    assert _write_from("192.168.1.20", monkeypatch, tmp_path).status_code == 403


def test_write_from_loopback_is_allowed(monkeypatch, tmp_path):
    assert _write_from("127.0.0.1", monkeypatch, tmp_path).status_code == 200


def test_the_host_can_opt_a_lan_in(monkeypatch, tmp_path):
    assert _write_from("192.168.1.20", monkeypatch, tmp_path, allow=True).status_code == 200


def test_an_attempt_header_starts_a_fresh_run_for_the_same_browser(env):
    """A new chat is a new attempt: same server-issued cookie, its own progress. It cannot reach anyone else's progress."""
    earn(env, client=env.client)
    assert stages_of(env.client)[0]["unlocked"] is True
    fresh = stages_of(env.client, headers={"X-Attempt": "chat2"})
    assert fresh[0]["unlocked"] is False and fresh[0]["code"] is None
    assert post(env.client, "lww", "predict", "right", {"X-Attempt": "chat2"}).json()["progress"] == {"predict": True, "bug": False, "write": False, "explain": False}
    assert stages_of(env.client)[0]["unlocked"] is True                                # the first attempt is untouched
    assert stages_of(env.other(), headers={"X-Attempt": "chat2"})[0]["progress"]["predict"] is False   # another browser has nothing of it


def test_a_malformed_attempt_header_is_ignored(env):
    earn(env)
    assert stages_of(env.client, headers={"X-Attempt": "../../x y"})[0]["unlocked"] is True
