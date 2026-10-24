import json

from fastapi.testclient import TestClient

from server.app import create_app


def op(i, replica="ph-a", lamport=1, kind="adjust", item="rice", value=5):
    return {"id": f"{replica}:{i}", "replica": replica, "lamport": lamport, "kind": kind, "item": item, "value": value}


def make(tmp_path, notes=None):
    sent = [] if notes is None else notes
    app = create_app(tmp_path / "ops.jsonl", notifier=lambda title, msg: sent.append((title, msg)))
    return TestClient(app), sent


def test_sync_accepts_ops_and_returns_the_server_log(tmp_path):
    c, _ = make(tmp_path)
    r = c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1), op(2, item="water", value=3)]})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 2 and body["cursor"] == 2 and len(body["ops"]) == 2


def test_sync_is_idempotent_and_never_double_counts(tmp_path):
    c, _ = make(tmp_path)
    for _ in range(3):
        c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1)]})
    state = c.get("/api/state").json()
    assert state["state"]["rice"]["qty"] == 5 and state["stats"]["ops"] == 1


def test_a_second_replica_receives_what_the_first_sent(tmp_path):
    c, _ = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1)]})
    r = c.post("/api/sync", json={"replica": "ph-b", "cursor": 0, "ops": []}).json()
    assert [o["id"] for o in r["ops"]] == ["ph-a:1"]
    again = c.post("/api/sync", json={"replica": "ph-b", "cursor": r["cursor"], "ops": []}).json()
    assert again["ops"] == []


def test_state_and_stats_report_conflicts_and_oversells(tmp_path):
    c, _ = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [
        op(1, lamport=1, item="blankets", value=10), op(2, lamport=2, kind="note", item="blankets", value="A")]})
    c.post("/api/sync", json={"replica": "ph-b", "cursor": 0, "ops": [
        op(1, "ph-b", 3, "adjust", "blankets", -7), op(2, "ph-b", 3, "note", "blankets", "B"),
        op(3, "ph-b", 4, "adjust", "blankets", -6)]})
    s = c.get("/api/state").json()
    assert s["state"]["blankets"]["qty"] == -3
    assert s["stats"]["oversold"] == ["blankets"] and s["stats"]["conflicts"] == 1


def test_invalid_ops_are_rejected_individually_and_valid_ones_kept(tmp_path):
    c, _ = make(tmp_path)
    bad = op(1, kind="explode")
    r = c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(2), bad]})
    assert r.status_code == 200 and r.json()["accepted"] == 1 and len(r.json()["rejected"]) == 1
    assert c.get("/api/state").json()["stats"]["ops"] == 1


def test_limits_are_enforced(tmp_path):
    c, _ = make(tmp_path)
    too_many = [op(i) for i in range(501)]
    assert c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": too_many}).status_code == 422
    assert c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1, item="x" * 300)]}).json()["rejected"]
    assert c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1, kind="note", item="rice", value="y" * 500)]}).json()["rejected"]


def test_log_survives_a_restart(tmp_path):
    c, _ = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1), op(2, item="water", value=3)]})
    c2, _ = make(tmp_path)
    assert c2.get("/api/state").json()["stats"]["ops"] == 2


def test_oversell_triggers_one_notification_until_it_clears(tmp_path):
    c, sent = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1, item="blankets", value=-2)]})
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(2, item="blankets", value=-1)]})
    assert len(sent) == 1 and "blankets" in sent[0][1]
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(3, item="blankets", value=10)]})
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(4, item="blankets", value=-20)]})
    assert len(sent) == 2


def test_notifier_failure_never_breaks_a_sync(tmp_path):
    def broken(title, msg):
        raise ConnectionError("offline")
    app = create_app(tmp_path / "o.jsonl", notifier=broken)
    c = TestClient(app)
    r = c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1, item="x", value=-1)]})
    assert r.status_code == 200


def test_static_app_and_health(tmp_path):
    c, _ = make(tmp_path)
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/").status_code == 200
    assert c.get("/board").status_code == 200
    home = c.get("/").text
    assert 'href="/static/learn.html"' in home and 'href="/race"' in home
    assert 'href="/static/learn.html"' in c.get("/board").text


def test_service_worker_is_served_from_the_root_scope(tmp_path):
    c, _ = make(tmp_path)
    r = c.get("/sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/"
    assert 'var CACHE = "stillcounts-v' in r.text
    assert c.get("/static/crdt.js").status_code == 200
    for name in ("race.js", "race.css"):
        assert c.get("/static/" + name).status_code == 200 and "/static/" + name in r.text
    assert '"/race"' in r.text and c.get("/race").status_code == 200
    for name in ("learn.html", "learn.js", "learn.css"):
        assert c.get("/static/" + name).status_code == 200 and "/static/" + name in r.text


def test_seed_is_idempotent(tmp_path):
    c, _ = make(tmp_path)
    assert c.post("/api/seed").json()["accepted"] == 12
    assert c.post("/api/seed").json()["accepted"] == 0
    assert c.get("/api/state").json()["state"]["rice"]["qty"] == 200


def test_chaos_results_default_to_empty_and_read_the_file(tmp_path):
    c, _ = make(tmp_path)
    assert c.get("/api/chaos").json()["runs"] == []
    assert c.get("/api/chaos").json()["summary"]["runs"] > 0      # nothing in tmp_path: the committed summary answers
    (tmp_path / "chaos_results.json").write_text(json.dumps({"runs": [{"seed": 1, "ok": True}]}))
    assert c.get("/api/chaos").json()["runs"][0]["seed"] == 1


def test_chaos_falls_back_to_the_committed_summary(tmp_path):
    c, _ = make(tmp_path)
    (tmp_path / "chaos_summary.json").write_text(json.dumps({"runs": 5, "failed": 1}))
    body = c.get("/api/chaos").json()
    assert body["runs"] == [] and body["summary"]["failed"] == 1       # a newer summary next to the log wins


def test_phone_reports_show_who_is_caught_up(tmp_path):
    from fastapi.testclient import TestClient
    from server.app import create_app
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    op = {"id": "p:1", "replica": "p", "lamport": 1, "kind": "adjust", "item": "rice", "value": 1}
    c.post("/api/sync", json={"replica": "p", "cursor": 0, "held": 1, "queued": 1, "ops": [op]})
    c.post("/api/sync", json={"replica": "q", "cursor": 0, "held": 0, "queued": 0, "ops": []})
    rows = {r["replica"]: r for r in c.get("/api/state").json()["phones"]}
    assert rows["p"]["queued"] == 1 and rows["p"]["caught_up"] is False     # it said it still had one queued
    assert rows["q"]["held"] == 0 and rows["q"]["caught_up"] is False       # the log has 1 op and q holds none
    c.post("/api/sync", json={"replica": "q", "cursor": 0, "held": 1, "queued": 0, "ops": []})
    assert {r["replica"]: r["caught_up"] for r in c.get("/api/state").json()["phones"]}["q"] is True


def test_a_phone_that_never_reports_is_not_listed(tmp_path):
    from fastapi.testclient import TestClient
    from server.app import create_app
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    c.post("/api/sync", json={"replica": "old", "cursor": 0, "ops": []})
    assert c.get("/api/state").json()["phones"] == []


def test_a_foreign_data_dir_still_serves_the_committed_evidence_and_summary(tmp_path):
    """STILLCOUNTS_DATA_DIR moves the live log only. The board's Devin and Attack panels read what ships in the repo."""
    c, _ = make(tmp_path / "elsewhere")
    assert c.get("/api/devin").json()["sessions"] == 9
    chaos = c.get("/api/chaos").json()
    assert chaos["summary"]["runs"] > 0 and len(chaos["summary"]["by_scenario"]) == 25
