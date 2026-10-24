import csv
import io

from fastapi.testclient import TestClient

from server.app import create_app


def op(i, replica="ph-a", lamport=1, kind="adjust", item="rice", value=5):
    return {"id": f"{replica}:{i}", "replica": replica, "lamport": lamport, "kind": kind, "item": item, "value": value}


def make(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def test_csv_export_lists_each_item_with_status(tmp_path):
    c = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [
        op(1, kind="create", item="blankets", value="Blankets"), op(2, item="blankets", value=10),
        op(3, "ph-a", 2, "adjust", "blankets", -13), op(4, kind="note", item="rice", value="check, \"quoted\"")]})
    r = c.get("/api/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    by = {x["item"]: x for x in rows}
    assert by["blankets"]["name"] == "Blankets" and by["blankets"]["qty"] == "-3" and by["blankets"]["status"] == "needs a human"
    assert by["rice"]["note"] == 'check, "quoted"' and by["rice"]["status"] == "ok"


def test_csv_export_neutralises_spreadsheet_formulas(tmp_path):
    c = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": [op(1, kind="note", item="rice", value="=HYPERLINK(\"http://evil\")")]})
    row = list(csv.DictReader(io.StringIO(c.get("/api/export.csv").text)))[0]
    assert row["note"].startswith("'")


def test_volunteer_names_are_remembered_and_shown(tmp_path):
    c = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "name": "Anita", "cursor": 0, "ops": [op(1, item="blankets", value=10)]})
    s = c.get("/api/state").json()
    assert s["names"]["ph-a"] == "Anita"
    assert s["activity"][-1]["who"] == "Anita" and "+10" in s["activity"][-1]["text"] and "blankets" in s["activity"][-1]["text"].lower()


def test_names_persist_across_restarts_and_are_validated(tmp_path):
    c = make(tmp_path)
    c.post("/api/sync", json={"replica": "ph-a", "name": "Anita", "cursor": 0, "ops": []})
    c.post("/api/sync", json={"replica": "ph-b", "name": "x" * 200, "cursor": 0, "ops": []})
    c.post("/api/sync", json={"replica": "ph-c", "name": "bad\u0000name", "cursor": 0, "ops": []})
    c2 = make(tmp_path)
    names = c2.get("/api/state").json()["names"]
    assert names == {"ph-a": "Anita"}


def test_activity_feed_shows_the_latest_operations_in_order(tmp_path):
    c = make(tmp_path)
    ops = [op(i, item="rice", value=i) for i in range(1, 15)]
    c.post("/api/sync", json={"replica": "ph-a", "cursor": 0, "ops": ops})
    act = c.get("/api/state").json()["activity"]
    assert len(act) == 10 and "+14" in act[-1]["text"]
    assert [a["who"] for a in act] == ["ph-a"] * 10
