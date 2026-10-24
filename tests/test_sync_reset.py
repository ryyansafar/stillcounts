"""The base tells a phone when its cursor no longer fits the log, so the phone can offer back what the base lost."""
from fastapi.testclient import TestClient

from server.app import create_app


def op(n, lamport=1):
    return {"id": f"p:{n}", "replica": "p", "lamport": lamport, "kind": "adjust", "item": "rice", "value": 1}


def sync(c, **body):
    body.setdefault("replica", "p")
    return c.post("/api/sync", json=body).json()


def test_a_fresh_phone_is_not_told_to_reset(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    r = sync(c, cursor=0, ops=[op(1), op(2)])
    assert r["reset"] is False and r["cursor"] == 2 and r["max_lamport"] == 1


def test_matching_cursor_and_anchor_is_not_a_reset(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    first = sync(c, cursor=0, ops=[op(1), op(2)])
    r = sync(c, cursor=first["cursor"], epoch=first["epoch"], anchor="p:2")
    assert r["reset"] is False and r["ops"] == []


def test_a_log_that_shifted_under_the_cursor_is_a_reset_even_if_it_is_long_enough(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    first = sync(c, cursor=0, ops=[op(1), op(2), op(3)])
    lines = (tmp_path / "ops.jsonl").read_text().splitlines()
    (tmp_path / "ops.jsonl").write_text("\n".join([lines[0], lines[2], "x" * 5]) + "\n")   # same epoch file, different log
    (tmp_path / "ops.jsonl.epoch").write_text(first["epoch"])
    c2 = TestClient(create_app(tmp_path / "ops.jsonl"))
    r = sync(c2, cursor=2, epoch=first["epoch"], anchor="p:2", ops=[])
    assert r["reset"] is True and [o["id"] for o in r["ops"]] == ["p:1", "p:3"]


def test_a_different_epoch_is_a_reset(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    sync(c, cursor=0, ops=[op(1)])
    r = sync(c, cursor=1, epoch="not-the-epoch", anchor="p:1")
    assert r["reset"] is True and len(r["ops"]) == 1


def test_a_cursor_past_the_end_is_a_reset(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    sync(c, cursor=0, ops=[op(1)])
    assert sync(c, cursor=9, anchor="p:1")["reset"] is True


def test_old_clients_without_an_anchor_still_work(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    first = sync(c, cursor=0, ops=[op(1)])
    r = sync(c, cursor=1, epoch=first["epoch"])
    assert r["reset"] is False


def test_a_skewed_lamport_is_rejected_with_a_reason_and_the_reply_carries_the_newest_time(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    sync(c, cursor=0, ops=[op(1, lamport=7)])
    r = sync(c, cursor=0, ops=[op(2, lamport=10 ** 12)])
    assert r["rejected"][0]["reason"].startswith("lamport") and r["max_lamport"] == 7
