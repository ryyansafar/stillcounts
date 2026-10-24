"""The Devin bridge, with a fake Devin: the agent stops after each stage, the learner earns it, only then it continues."""
import json

import pytest
from fastapi.testclient import TestClient

from ops.devin_client import DevinError
from server.app import create_app
from server.devin_bridge import GitHubInfo

CODE1 = "def total(xs):\n    return sum(xs)\n"
CODE2 = CODE1 + "\ndef average(xs):\n    return total(xs) / len(xs) if xs else 0\n"


def stage(n, code):
    return {"n": n, "title": f"Stage {n}", "summary": "adds things up", "files": ["app.py"], "concepts": ["summing a list", "empty input"],
            "bug_hint": "off by one", "code": code,
            "predict": [{"expr": "total([3, 4])", "question": "What is the total?"}, {"expr": "total([])", "question": "And empty?"}],
            "bug": {"snippet": "def total(xs):\n    t = 1\n    for x in xs:\n        t += x\n    return t", "buggy_line": 2, "explanation": "t must start at 0"},
            "write": {"function": "total", "task": "Write total(xs)", "stub": "def total(xs):\n    raise NotImplementedError\n", "tests": ["total([1, 2, 3])", "total([])"]}}


class FakeDevin:
    def __init__(self):
        self.sent, self.created, self.stopped = [], [], []
        self.session = {"status": "running", "status_detail": "working", "acus_consumed": 0.4, "max_acu_limit": 3, "url": "https://app.devin.ai/sessions/s1",
                        "structured_output": {"stages_done": 0, "plan": [{"n": 1, "title": "Sum", "summary": "x"}, {"n": 2, "title": "Average", "summary": "y"},
                                                                         {"n": 3, "title": "CLI", "summary": "z"}], "stages": []},
                        "pull_requests": []}
        self.messages = [{"event_id": "e1", "source": "devin", "message": "Planning 3 stages."}]
        self.fail = None

    def create_session(self, prompt, **kw):
        if self.fail:
            raise self.fail
        self.created.append((prompt, kw))
        return {"session_id": "s1", "url": "https://app.devin.ai/sessions/s1"}

    def create_playbook(self, *a, **kw):
        return {"playbook_id": "pb-1"}

    def get_session(self, sid):
        if self.fail:
            raise self.fail
        return self.session

    def list_messages(self, sid, limit=200):
        return self.messages

    def message(self, sid, text):
        self.sent.append(text)
        return {}

    def terminate(self, sid):
        self.stopped.append(sid)
        return {}

    def built(self, *codes):
        self.session["structured_output"]["stages"] = [stage(i, c) for i, c in enumerate(codes, start=1)]
        self.session["structured_output"]["stages_done"] = len(codes)
        self.session["status_detail"] = "waiting_for_user"


class FakeGitHub(GitHubInfo):
    def __init__(self):
        super().__init__(token="")

    def lookup(self, url):
        return {"title": "Stage 1: sum (PR)", "state": "open", "ci": "passing", "review": {"state": "reviewed", "comments": 2}}


@pytest.fixture
def env(tmp_path):
    d = FakeDevin()
    c = TestClient(create_app(tmp_path / "ops.jsonl", devin_client=d, github_info=FakeGitHub()))
    return c, d


def begin(c):
    r = c.post("/api/devin/sessions", json={"prompt": "build a totals app", "user_prompt": "build a totals app", "max_acu_limit": 3, "difficulty": 3})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def poll(c, sid):
    return c.get(f"/api/devin/sessions/{sid}").json()


def post(c, sid, n, kind, body):
    return c.post(f"/api/devin/sessions/{sid}/stages/{n}/{kind}", json=body, headers={"X-Learner-Id": "me"})


def earn(c, sid, n):
    assert post(c, sid, n, "predict", {"answer": {"c1": 7, "c2": 0}}).json()["passed"]
    assert post(c, sid, n, "bug", {"answer": "2"}).json()["passed"]
    r = post(c, sid, n, "write", {"source": "def total(xs):\n    s = 0\n    for x in xs:\n        s += x\n    return s\n"}).json()
    assert r["passed"], r
    return r


def test_without_a_token_the_bridge_says_so_and_names_the_code(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVIN_TOKEN", raising=False)
    monkeypatch.delenv("STILLCOUNTS_DEVIN_ENV", raising=False)
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    r = c.post("/api/devin/sessions", json={"prompt": "x"})
    assert r.status_code == 503 and r.json()["code"] == "no_token"


def test_start_creates_a_real_session_with_playbook_schema_and_cap(env):
    c, d = env
    sid = begin(c)
    prompt, kw = d.created[0]
    assert sid == "s1" and kw["playbook_id"] == "pb-1" and kw["max_acu_limit"] == 3
    assert "stages_done" in kw["structured_output_schema"]["properties"] and "STOP" in prompt and "continue" in prompt
    assert "150 lines" in prompt


def test_quota_and_api_errors_are_named_not_hidden(env):
    c, d = env
    d.fail = DevinError("HTTP 402 limit", 402)
    r = c.post("/api/devin/sessions", json={"prompt": "x"})
    assert r.status_code == 429 and r.json()["code"] == "quota"
    d.fail = DevinError("HTTP 500 boom", 500)
    assert c.post("/api/devin/sessions", json={"prompt": "x"}).json()["code"] == "api_error"


def test_poll_reports_status_acu_link_plan_messages_and_pr_cards(env):
    c, d = env
    sid = begin(c)
    d.built(CODE1)
    d.session["pull_requests"] = [{"pr_url": "https://github.com/o/r/pull/7", "pr_state": "open"}]
    snap = poll(c, sid)
    assert snap["status_detail"] == "waiting_for_user" and snap["acu_consumed"] == 0.4 and snap["acu_limit"] == 3
    assert snap["url"].startswith("https://app.devin.ai/") and [p["n"] for p in snap["plan"]] == [1, 2, 3]
    assert snap["messages"] == [{"id": "e1", "source": "devin", "text": "Planning 3 stages."}]
    pr = snap["pull_requests"][0]
    assert pr["ci"] == "passing" and pr["review"]["comments"] == 2 and pr["stage"] == 1


def test_code_is_withheld_until_the_stage_is_earned(env):
    c, d = env
    sid = begin(c)
    d.built(CODE1)
    view = poll(c, sid)["stages"][0]
    assert view["code"] is None and view["unlocked"] is False
    assert "return sum" not in json.dumps(poll(c, sid))
    assert "buggy_line" not in json.dumps(view) and '"expected"' not in json.dumps(view)
    r = earn(c, sid, 1)
    assert r["unlocked"] is True and r["code"] == CODE1
    assert poll(c, sid)["stages"][0]["code"] == CODE1


def test_a_wrong_answer_does_not_unlock_and_nothing_is_marked(env):
    c, d = env
    sid = begin(c)
    d.built(CODE1)
    r = post(c, sid, 1, "predict", {"answer": {"c1": 8, "c2": 0}}).json()
    assert r["passed"] is False and r["code"] is None and r["progress"]["predict"] is False
    assert post(c, sid, 1, "bug", {"answer": "3"}).json()["passed"] is False
    r = post(c, sid, 1, "write", {"source": "def total(xs):\n    return len(xs)\n"}).json()
    assert r["passed"] is False and r["code"] is None


def test_write_runs_in_a_sandbox_and_refuses_os(env):
    c, d = env
    sid = begin(c)
    d.built(CODE1)
    r = post(c, sid, 1, "write", {"source": "import os\ndef total(xs):\n    return os.getpid()\n"}).json()
    assert r["passed"] is False and "not allowed" in r["result"]["error"]
    r = post(c, sid, 1, "write", {"source": "def total(xs):\n    while True:\n        pass\n"}).json()
    assert r["passed"] is False


def test_devin_is_only_told_to_continue_after_the_learner_earned_the_stage(env):
    c, d = env
    sid = begin(c)
    d.built(CODE1)
    ok = c.post(f"/api/devin/sessions/{sid}/message", json={"text": "continue 2", "reason": "continue", "n": 2})
    assert ok.status_code == 200                       # one stage of lookahead (pipelining)
    refused = c.post(f"/api/devin/sessions/{sid}/message", json={"text": "continue 3", "reason": "continue", "n": 3})
    assert refused.status_code == 403 and refused.json()["code"] == "not_earned"
    assert d.sent == ["continue 2"]
    d.built(CODE1, CODE2)
    earn(c, sid, 1)
    assert c.post(f"/api/devin/sessions/{sid}/message", json={"text": "continue 3", "reason": "continue", "n": 3}).status_code == 200
    assert d.sent[-1] == "continue 3"


def test_a_stage_cannot_be_worked_before_the_one_before_it_is_earned(env):
    c, d = env
    sid = begin(c)
    d.built(CODE1, CODE2)
    r = post(c, sid, 2, "bug", {"answer": "2"})
    assert r.status_code == 403 and r.json()["code"] == "locked"


def test_adaptation_messages_really_reach_devin_and_stop_terminates(env):
    c, d = env
    sid = begin(c)
    r = c.post(f"/api/devin/sessions/{sid}/message", json={"text": "The learner is stressed, make the next stage smaller and add comments.", "reason": "adapt"})
    assert r.status_code == 200 and "stressed" in d.sent[0]
    assert c.post(f"/api/devin/sessions/{sid}/stop", json={}).json()["stopped"] is True and d.stopped == [sid]


def test_unknown_sessions_are_refused_and_the_token_never_appears(env):
    c, d = env
    assert c.get("/api/devin/sessions/nope").status_code == 410
    sid = begin(c)
    d.built(CODE1)
    assert "cog_" not in json.dumps(poll(c, sid))


def test_adaptation_events_are_logged_without_any_heart_rate(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    r = c.post("/api/learn/adaptations", json={"chat": "c1", "level": 1, "prev": 3, "kind": "stress", "stage": 2,
                                                  "reason": "HR 112 vs baseline 72: switching to easier steps"})
    assert r.status_code == 200
    out = c.get("/api/learn/results").json()
    ev = out["adaptations"][0]
    assert ev["level"] == 1 and ev["stage"] == 2 and not any(ch.isdigit() for ch in ev["reason"])
    assert out["summary"]["lowered"] == 1
    assert c.post("/api/learn/adaptations", json={"chat": "c1", "level": 9, "prev": 3}).status_code == 422
