"""Regression tests for bugs found by Devin scout sessions (see docs/scout_scenarios.json)."""
import json

from fastapi.testclient import TestClient

from server.app import LogStore, MAX_SKEW, create_app


def op(i, replica="ph-a", lamport=1, kind="adjust", item="rice", value=5):
    return {"id": f"{replica}:{i}", "replica": replica, "lamport": lamport, "kind": kind, "item": item, "value": value}


def make(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def sync(c, ops, cursor=0, epoch=None, replica="ph-a"):
    body = {"replica": replica, "cursor": cursor, "ops": ops}
    if epoch is not None:
        body["epoch"] = epoch
    return c.post("/api/sync", json=body)


def test_one_poison_op_is_rejected_by_id_and_the_rest_are_accepted(tmp_path):
    c = make(tmp_path)
    body = sync(c, [op(1), op(2, kind="explode"), op(3, item="water", value=3)]).json()
    assert body["accepted"] == 2
    assert [r["id"] for r in body["rejected"]] == ["ph-a:2"]
    assert c.get("/api/state").json()["stats"]["ops"] == 2


def test_same_id_with_different_content_is_rejected_not_silently_dropped(tmp_path):
    c = make(tmp_path)
    sync(c, [op(1, value=-30)])
    body = sync(c, [op(1, value=5)]).json()
    assert body["accepted"] == 0 and "different content" in body["rejected"][0]["reason"]
    assert c.get("/api/state").json()["state"]["rice"]["qty"] == -30


def test_resending_the_identical_op_is_not_an_error(tmp_path):
    c = make(tmp_path)
    sync(c, [op(1)])
    body = sync(c, [op(1)]).json()
    assert body["accepted"] == 0 and body["rejected"] == []


def test_absurd_lamport_values_are_rejected(tmp_path):
    c = make(tmp_path)
    assert sync(c, [op(1, lamport=2 ** 60)]).json()["rejected"]
    sync(c, [op(2, lamport=5)])
    skewed = sync(c, [op(3, lamport=5 + MAX_SKEW + 10)]).json()
    assert skewed["accepted"] == 0 and "skew" in skewed["rejected"][0]["reason"]


def test_coercion_is_refused(tmp_path):
    c = make(tmp_path)
    bad = [op(1, value="5"), {**op(2), "lamport": True}, {**op(3), "lamport": 7.5}, op(4, value=2 ** 40)]
    r = sync(c, bad)
    body = r.json() if r.status_code == 200 else {"accepted": 0, "rejected": [1]}
    assert body["accepted"] == 0


def test_lone_surrogate_text_is_rejected_and_does_not_poison_the_log(tmp_path):
    c = make(tmp_path)
    r = c.post("/api/sync", content=json.dumps({"replica": "ph-a", "cursor": 0,
              "ops": [op(1, kind="note", item="x", value="bad \ud800 text")]}), headers={"content-type": "application/json"})
    assert r.status_code in (200, 422)
    sync(c, [op(2)])
    assert c.get("/api/state").json()["stats"]["ops"] >= 1


def test_unicode_line_separator_in_text_survives_a_restart(tmp_path):
    c = make(tmp_path)
    sync(c, [op(1, kind="note", item="x", value="line break"), op(2, item="water", value=3)])
    c2 = make(tmp_path)
    s = c2.get("/api/state").json()
    assert s["stats"]["ops"] == 2 and s["state"]["x"]["note"] == "line break"


def test_torn_last_line_does_not_swallow_the_next_op(tmp_path):
    p = tmp_path / "ops.jsonl"
    c = TestClient(create_app(p))
    sync(c, [op(1, item="x", value=1)])
    with p.open("a") as fh:
        fh.write('{"id": "ph-a:2", "replica": "ph-a", "lam')          # a crash mid-write, no newline
    c2 = TestClient(create_app(p))
    sync(c2, [op(3, item="y", value=2)])
    c3 = TestClient(create_app(p))
    ids = {o["id"] for o in c3.get("/api/log").json()["ops"]}
    assert ids == {"ph-a:1", "ph-a:3"}


def test_epoch_changes_when_a_corrupt_line_is_skipped_so_cursors_resync(tmp_path):
    p = tmp_path / "ops.jsonl"
    c = TestClient(create_app(p))
    first = sync(c, [op(1), op(2, item="water", value=3)]).json()
    with p.open("a") as fh:
        fh.write("not json\n")
    c2 = TestClient(create_app(p))
    assert sync(c2, []).json()["epoch"] != first["epoch"]


def test_a_stale_cursor_or_foreign_epoch_triggers_a_full_resync(tmp_path):
    c = make(tmp_path)
    a = sync(c, [op(1), op(2, item="water", value=3)]).json()
    far = sync(c, [], cursor=999, epoch=a["epoch"]).json()
    assert len(far["ops"]) == 2
    foreign = sync(c, [], cursor=2, epoch="some-other-log").json()
    assert len(foreign["ops"]) == 2


def test_cursor_and_ops_come_from_one_consistent_snapshot(tmp_path):
    c = make(tmp_path)
    body = sync(c, [op(1), op(2, item="water", value=3)], cursor=0).json()
    assert body["cursor"] == len(body["ops"]) == 2


def test_logstore_appends_a_newline_guard_after_a_torn_file(tmp_path):
    p = tmp_path / "ops.jsonl"
    p.write_text('{"id": "a:1", "replica": "a", "lamport": 1, "kind": "adjust", "item": "x", "value": 1}\n{"id": "a:2"')
    store = LogStore(p)
    from stillcounts.crdt import Op
    store.append([Op("a:3", "a", 2, "adjust", "x", 2)])
    assert [o.id for o in LogStore(p).ops] == ["a:1", "a:3"]
