import json

from fastapi.testclient import TestClient

from ops.devin_counts import summarise
from ops.oracle_guard import guard
from server.app import create_app


def make(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def test_qr_endpoint_returns_an_svg_for_an_http_url(tmp_path):
    r = make(tmp_path).get("/api/qr.svg", params={"text": "https://example.trycloudflare.com/"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and "<svg" in r.text


def test_qr_endpoint_refuses_non_http_and_long_text(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/qr.svg", params={"text": "javascript:alert(1)"}).status_code == 422
    assert c.get("/api/qr.svg", params={"text": "https://x.example/" + "a" * 400}).status_code == 422


def test_devin_counts_endpoint_defaults_and_reads_the_file(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/devin").json()["sessions"] == 0
    (tmp_path / "devin_counts.json").write_text(json.dumps({"sessions": 7, "prs": 1}))
    assert c.get("/api/devin").json()["sessions"] == 7


def test_summarise_counts_sessions_prs_and_outputs():
    sessions = [{"status": "running", "pull_requests": [{"pr_url": "u1"}], "structured_output": {"a": 1}, "acus_consumed": 0.0},
                {"status": "suspended", "pull_requests": [], "structured_output": None},
                {"status": "exit", "pull_requests": [{"pr_url": "u2"}, {"pr_url": "u3"}], "structured_output": {"b": 2}}]
    s = summarise(sessions)
    assert s["sessions"] == 3 and s["prs"] == 3 and s["structured_outputs"] == 2
    assert s["by_status"] == {"running": 1, "suspended": 1, "exit": 1}


def test_guard_passes_for_the_real_merge_and_does_not_notify():
    sent = []
    result = guard(mutant=None, notify=lambda t, m: sent.append((t, m)))
    assert result["ok"] and sent == []


def test_guard_catches_a_broken_merge_and_pushes_a_notification():
    sent = []
    result = guard(mutant="lww-picks-earliest", notify=lambda t, m: sent.append((t, m)), start_session=None)
    assert not result["ok"] and len(sent) == 1
    assert "lww-picks-earliest" in sent[0][1]


def test_guard_starts_a_devin_session_when_a_starter_is_given():
    started = []
    guard(mutant="order-swapped", notify=lambda t, m: None, start_session=lambda prompt: started.append(prompt) or "sess-1")
    assert len(started) == 1 and "order-swapped" in started[0]


def test_referee_endpoint_reports_the_mutation_score(tmp_path):
    body = make(tmp_path).get("/api/referee").json()
    assert body["total"] >= 12 and body["killed"] == body["total"] and body["survivors"] == []
