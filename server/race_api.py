"""Referee Race: every phone gets the same operations in its own shuffled order, with duplicates, and races to
predict the merged stock. The answer key is the real merge, so the round proves order does not matter."""
from __future__ import annotations

import json
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from stillcounts.crdt import Op, merge

VOLUNTEERS = ["vol-asha", "vol-biju", "vol-chitra"]
ITEMS = {"blankets": "Blankets", "rice": "Rice (kg)", "water": "Drinking water (L)", "paracetamol": "Paracetamol (strips)",
         "tarpaulin": "Tarpaulins", "baby-food": "Baby food (tins)", "lanterns": "Lanterns", "soap": "Soap bars"}
RANK_POINTS = [1000, 800, 650, 500, 400, 300]
MIN_POINTS = 200
PAYOFF = "Every phone saw a different order and the merge gave the same answer."
MAX_ITEMS = 20


def make_round_ops(seed: int, round_id: str) -> list[dict]:
    """8 to 12 unique operations over 3 items from 3 fake volunteers: a create and an opening count per item, a delete
    (sometimes reversed by a later restore) and a few more adjustments."""
    rng = random.Random(f"race:{seed}")
    total = rng.randint(8, 12)
    items = rng.sample(sorted(ITEMS), 3)
    counters = {v: 0 for v in VOLUNTEERS}
    ops: list[dict] = []

    def add(vol: str, lamport: int, kind: str, item: str, value: Any) -> None:
        counters[vol] += 1
        ops.append({"id": f"race{round_id}:{vol}:{counters[vol]}", "replica": vol, "lamport": lamport,
                    "kind": kind, "item": item, "value": value})

    for i, item in enumerate(items):
        add(VOLUNTEERS[i], 1, "create", item, ITEMS[item])
    for item in items:
        add(rng.choice(VOLUNTEERS), rng.randint(1, 3), "adjust", item, rng.randint(10, 30))
    target = rng.choice(items)
    deleter = rng.choice(VOLUNTEERS)
    del_at = rng.randint(2, 5)
    add(deleter, del_at, "delete", target, True)
    if rng.random() < 0.6 and len(ops) < total:
        add(rng.choice([v for v in VOLUNTEERS if v != deleter]), del_at + rng.randint(1, 2), "delete", target, False)
    while len(ops) < total:
        value = rng.choice([-5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 8, 10, 12])
        add(rng.choice(VOLUNTEERS), rng.randint(1, 6), "adjust", rng.choice(items), value)
    return ops


def answer_key(ops: list[Op]) -> dict[str, int | str]:
    return {item: (s["qty"] if s["alive"] else "removed") for item, s in merge(ops).items()}


class Answer(BaseModel):
    player: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=40)
    round: int = Field(ge=1)
    answers: dict[str, int | str]
    merged: dict[str, int | str] = Field(default_factory=dict)
    elapsed_ms: int = Field(ge=0, le=10 ** 9)

    @field_validator("answers", "merged")
    @classmethod
    def _bounded(cls, v: dict) -> dict:
        if len(v) > MAX_ITEMS or any(len(k) > 64 or (isinstance(x, str) and len(x) > 16) for k, x in v.items()):
            raise ValueError("too many or too long answers")
        return v


class StartIn(BaseModel):
    seed: int | None = Field(default=None, ge=0, le=2 ** 31)


class RaceGame:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.round = 0
        self.seed = 0
        self.ops: list[dict] = []
        self.truth: dict[str, int | str] = {}
        self.issued: dict[str, tuple[list[dict], float]] = {}
        self.results: dict[int, dict[str, dict]] = {}
        self.names: dict[str, str] = {}

    def start(self, seed: int | None = None) -> dict:
        with self.lock:
            self.round += 1
            self.seed = random.randrange(2 ** 31) if seed is None else seed
            self.ops = make_round_ops(self.seed, str(self.round))
            self.truth = answer_key([Op(**o) for o in self.ops])
            self.issued = {}
            self.results[self.round] = {}
            self.token = uuid.uuid4().hex
            self.started_at = self.clock()
            return {"round": self.round, "ops": len(self.ops), "items": sorted(self.truth)}

    def _shuffle_for(self, player: str) -> list[dict]:
        seen = {tuple(o["id"] for o in seq) for seq, _ in self.issued.values()}
        adjusts = [o for o in self.ops if o["kind"] == "adjust"]
        for attempt in range(100):
            rng = random.Random(f"{self.seed}:{self.round}:{player}:{attempt}")
            seq = self.ops + rng.sample(adjusts, rng.randint(2, 3))
            rng.shuffle(seq)
            if tuple(o["id"] for o in seq) not in seen:
                break
        return [dict(o) for o in seq]

    def round_for(self, player: str) -> dict | None:
        with self.lock:
            if not self.round:
                return None
            if player not in self.issued:
                self.issued[player] = (self._shuffle_for(player), self.clock())
            return {"round": self.round, "token": self.token, "ops": self.issued[player][0], "items": sorted(self.truth),
                    "names": {o["item"]: o["value"] for o in self.ops if o["kind"] == "create"}, "payoff": PAYOFF}

    def _points(self, rnd: int) -> dict[str, int]:
        correct = sorted((r["elapsed_ms"], p) for p, r in self.results.get(rnd, {}).items() if r["correct"])
        pts = {p: 0 for p in self.results.get(rnd, {})}
        for rank, (_, p) in enumerate(correct):
            pts[p] = RANK_POINTS[rank] if rank < len(RANK_POINTS) else MIN_POINTS
        return pts

    def answer(self, player: str, name: str, rnd: int, answers: dict, merged: dict, elapsed_ms: int) -> dict:
        with self.lock:
            if rnd != self.round or player not in self.issued:
                raise LookupError("that round is over or was never sent to this phone")
            if name.strip():
                self.names[player] = name.strip()
            done = self.results[rnd]
            duplicate = player in done
            if not duplicate:
                seen_ms = int((self.clock() - self.issued[player][1]) * 1000)
                done[player] = {"correct": answers == self.truth, "elapsed_ms": max(0, min(elapsed_ms, seen_ms)),
                                "merged": json.dumps(merged, sort_keys=True)}
            r = done[player]
            correct_order = sorted(x["elapsed_ms"] for x in done.values() if x["correct"])
            return {"correct": r["correct"], "points": self._points(rnd)[player], "truth": self.truth,
                    "elapsed_ms": r["elapsed_ms"], "duplicate": duplicate,
                    "rank": correct_order.index(r["elapsed_ms"]) + 1 if r["correct"] else None}

    def board(self) -> dict:
        with self.lock:
            totals: dict[str, dict] = {}
            for rnd in self.results:
                for p, pts in self._points(rnd).items():
                    t = totals.setdefault(p, {"name": self.names.get(p, p[:8]), "points": 0, "correct": 0})
                    t["points"] += pts
                    t["correct"] += int(self.results[rnd][p]["correct"])
            now = self.results.get(self.round, {})
            merges = {r["merged"] for r in now.values()}
            truth = json.dumps(self.truth, sort_keys=True)
            return {"round": self.round, "phones": len(self.issued),
                    "orders": len({tuple(o["id"] for o in seq) for seq, _ in self.issued.values()}),
                    "answered": len(now), "merge_answers": len(merges), "agree": bool(now) and merges == {truth},
                    "payoff": PAYOFF,
                    "players": sorted(totals.values(), key=lambda t: (-t["points"], t["name"]))}


def race_router(client_dir: Path, game: RaceGame | None = None) -> APIRouter:
    game = game or RaceGame()
    router = APIRouter()

    @router.post("/api/race/start")
    def start(body: StartIn | None = None) -> dict:
        return game.start(body.seed if body else None)

    @router.get("/api/race/round")
    def get_round(player: str = Query(..., min_length=1, max_length=64)) -> dict:
        out = game.round_for(player)
        if out is None:
            raise HTTPException(status_code=404, detail="no round yet: the host starts one from the board")
        return out

    @router.post("/api/race/answer")
    def answer(body: Answer) -> dict:
        try:
            return game.answer(body.player, body.name, body.round, body.answers, body.merged, body.elapsed_ms)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/api/race/board")
    def board() -> dict:
        return game.board()

    @router.get("/race")
    def page():
        return FileResponse(Path(client_dir) / "race.html")

    return router
