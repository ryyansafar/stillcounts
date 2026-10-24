"""With a join token set, writes under /api/ and the full log need it; reads of the ledger, the health check and the
pages stay open. Without a token (unit tests, an isolated network) nothing changes."""
import pytest
from fastapi.testclient import TestClient

from server.app import create_app

TOKEN = "s3cret-token"
OP = {"id": "ph-a:1", "replica": "ph-a", "lamport": 1, "kind": "adjust", "item": "rice", "value": 5}
SYNC = {"replica": "ph-a", "cursor": 0, "ops": [OP]}


def client(tmp_path, token=TOKEN):
    return TestClient(create_app(tmp_path / "ops.jsonl", join_token=token))


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/sync", SYNC), ("post", "/api/seed", None), ("post", "/api/race/start", {}),
    ("post", "/api/race/answer", {}), ("post", "/api/learn/duplicate-ids/predict", {"answer": {}}),
    ("get", "/api/log", None)])
def test_guarded_routes_refuse_a_missing_or_wrong_token(tmp_path, method, path, body):
    c = client(tmp_path)
    for headers in ({}, {"X-Join-Token": "guess"}):
        r = getattr(c, method)(path, headers=headers, **({"json": body} if body is not None else {}))
        assert r.status_code == 401 and "join token" in r.json()["detail"]
    assert c.get("/api/state").json()["stats"]["ops"] == 0


def test_the_right_token_is_accepted(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/sync", json=SYNC, headers={"X-Join-Token": TOKEN}).json()["accepted"] == 1
    assert len(c.get("/api/log", headers={"X-Join-Token": TOKEN}).json()["ops"]) == 1
    assert c.post("/api/seed", headers={"X-Join-Token": TOKEN}).json()["accepted"] == 12


def test_reads_pages_and_health_stay_open(tmp_path):
    c = client(tmp_path)
    for path in ("/health", "/api/state", "/api/chaos", "/api/devin", "/api/auth", "/", "/board", "/sw.js"):
        assert c.get(path).status_code == 200, path
    assert c.get("/api/auth").json() == {"token_required": True}
    assert TOKEN not in c.get("/api/auth").text


def test_no_token_configured_means_open(tmp_path):
    c = client(tmp_path, token=None)
    assert c.post("/api/sync", json=SYNC).json()["accepted"] == 1
    assert c.get("/api/auth").json() == {"token_required": False}


def test_an_environment_token_is_picked_up(tmp_path, monkeypatch):
    monkeypatch.setenv("STILLCOUNTS_TOKEN", TOKEN)
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    assert c.post("/api/sync", json=SYNC).status_code == 401
