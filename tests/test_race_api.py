from fastapi.testclient import TestClient

from server.app import create_app
from server.race_api import RaceGame, answer_key, make_round_ops
from stillcounts.crdt import Op, dedupe


def client(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def ops_of(rows):
    return [Op(**r) for r in rows]


def test_round_ops_are_8_to_12_valid_ops_from_three_volunteers():
    for seed in range(50):
        rows = make_round_ops(seed, f"r{seed}")
        ops = ops_of(rows)
        assert 8 <= len(ops) <= 12
        assert len(dedupe(ops)) == len(ops)
        assert len({o.replica for o in ops}) == 3
        kinds = {o.kind for o in ops}
        assert {"adjust", "create", "delete"} <= kinds


def test_round_generation_is_deterministic_per_seed():
    assert make_round_ops(7, "x") == make_round_ops(7, "x")
    assert make_round_ops(7, "x") != make_round_ops(8, "x")


def test_start_requires_no_round_then_returns_one(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/race/round", params={"player": "p1"}).status_code == 404
    r = c.post("/api/race/start", json={"seed": 3}).json()
    assert r["round"] == 1 and 8 <= r["ops"] <= 12 and r["items"]


def test_every_phone_gets_the_same_set_in_a_different_order_with_duplicates(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 11})
    base = {tuple(sorted(o.items())) for o in make_round_ops(11, "1")}
    orders = set()
    for i in range(12):
        body = c.get("/api/race/round", params={"player": f"p{i}"}).json()
        ids = [o["id"] for o in body["ops"]]
        assert len(ids) > len(set(ids)), "each phone should see a few duplicates"
        assert {tuple(sorted(o.items())) for o in body["ops"]} == base
        orders.add(tuple(ids))
    assert len(orders) == 12


def test_the_same_phone_gets_its_same_order_back(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 5})
    a = c.get("/api/race/round", params={"player": "p1"}).json()["ops"]
    b = c.get("/api/race/round", params={"player": "p1"}).json()["ops"]
    assert a == b


def test_answer_key_follows_the_real_merge():
    rows = [
        {"id": "a:1", "replica": "a", "lamport": 1, "kind": "create", "item": "rice", "value": "Rice"},
        {"id": "a:2", "replica": "a", "lamport": 2, "kind": "adjust", "item": "rice", "value": 5},
        {"id": "b:1", "replica": "b", "lamport": 3, "kind": "delete", "item": "rice", "value": True},
        {"id": "c:1", "replica": "c", "lamport": 4, "kind": "delete", "item": "rice", "value": False},
        {"id": "c:2", "replica": "c", "lamport": 1, "kind": "delete", "item": "water", "value": True},
    ]
    assert answer_key(ops_of(rows + rows[:2])) == {"rice": 5, "water": "removed"}


def _truth(c, seed):
    return answer_key(ops_of(make_round_ops(seed, "1")))


def test_fastest_correct_answer_scores_most_and_wrong_scores_nothing(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 21})
    truth = _truth(c, 21)
    wrong = {k: (v + 1 if isinstance(v, int) else 0) for k, v in truth.items()}
    for p in ("slow", "fast", "bad"):
        c.get("/api/race/round", params={"player": p})
    submit = lambda p, ans, ms: c.post("/api/race/answer", json={"player": p, "name": p.title(), "round": 1,
                                                                  "answers": ans, "merged": truth, "elapsed_ms": ms}).json()
    assert submit("slow", truth, 9000)["correct"] is True
    assert submit("fast", truth, 3000)["correct"] is True
    bad = submit("bad", wrong, 1000)
    assert bad["correct"] is False and bad["truth"] == truth
    board = c.get("/api/race/board").json()
    pts = {p["name"]: p["points"] for p in board["players"]}
    assert pts["Fast"] > pts["Slow"] > pts["Bad"] == 0
    assert [p["name"] for p in board["players"]][0] == "Fast"


def test_a_second_answer_for_the_same_round_is_ignored(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 2})
    truth = _truth(c, 2)
    c.get("/api/race/round", params={"player": "p"})
    body = {"player": "p", "name": "P", "round": 1, "answers": truth, "merged": truth, "elapsed_ms": 5000}
    first = c.post("/api/race/answer", json=body).json()
    again = c.post("/api/race/answer", json={**body, "elapsed_ms": 1}).json()
    assert again["duplicate"] is True and again["points"] == first["points"]


def test_answers_for_an_old_or_unknown_round_are_rejected(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 2})
    r = c.post("/api/race/answer", json={"player": "p", "name": "P", "round": 9, "answers": {}, "merged": {}, "elapsed_ms": 1})
    assert r.status_code == 409


def test_board_reports_the_payoff_every_order_different_one_answer(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 4})
    truth = _truth(c, 4)
    for i in range(4):
        c.get("/api/race/round", params={"player": f"p{i}"})
        c.post("/api/race/answer", json={"player": f"p{i}", "name": f"P{i}", "round": 1, "answers": truth,
                                         "merged": truth, "elapsed_ms": 1000 + i})
    b = c.get("/api/race/board").json()
    assert b["phones"] == 4 and b["orders"] == 4 and b["merge_answers"] == 1 and b["agree"] is True


def test_elapsed_time_is_clamped_to_what_the_server_saw():
    game = RaceGame(clock=iter([100.0, 100.0, 102.0]).__next__)
    game.start(seed=1)
    game.round_for("p")
    truth = answer_key(ops_of(make_round_ops(1, "1")))
    res = game.answer("p", "P", 1, truth, truth, elapsed_ms=10 ** 9)
    assert res["elapsed_ms"] == 2000


def test_names_and_bodies_are_bounded(tmp_path):
    c = client(tmp_path)
    c.post("/api/race/start", json={"seed": 2})
    assert c.get("/api/race/round", params={"player": "x" * 65}).status_code == 422
    r = c.post("/api/race/answer", json={"player": "p", "name": "n" * 41, "round": 1, "answers": {}, "merged": {}, "elapsed_ms": 1})
    assert r.status_code == 422


def test_race_page_and_assets_are_served_and_cached(tmp_path):
    c = client(tmp_path)
    assert "race.js" in c.get("/race").text
    for f in ("race.js", "race.css", "race.html"):
        assert c.get(f"/static/{f}").status_code == 200
    sw = c.get("/sw.js").text
    assert "/race" in sw and "/static/race.js" in sw and "/static/race.css" in sw and "stillcounts-v3" not in sw
