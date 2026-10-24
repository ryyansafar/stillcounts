"""The 'base' sync server. It only stores and relays operations; all merging is the pure CRDT, so it can never lose an update."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from stillcounts.crdt import Op, dedupe, merge, stats

ROOT = Path(__file__).resolve().parent.parent
MAX_OPS_PER_SYNC = 500
MAX_ID = 64
MAX_TEXT = 200


class OpIn(BaseModel):
    id: str = Field(min_length=1, max_length=MAX_ID)
    replica: str = Field(min_length=1, max_length=MAX_ID)
    lamport: int = Field(ge=1)
    kind: str
    item: str = Field(min_length=1, max_length=MAX_ID)
    value: Any = None

    @field_validator("value")
    @classmethod
    def short_text(cls, v):
        if isinstance(v, str) and len(v) > MAX_TEXT:
            raise ValueError("text too long")
        return v


class SyncIn(BaseModel):
    replica: str = Field(min_length=1, max_length=MAX_ID)
    cursor: int = Field(ge=0, default=0)
    ops: list[OpIn] = Field(default_factory=list, max_length=MAX_OPS_PER_SYNC)


class LogStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.ops: list[Op] = []
        self._seen: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    self._add(Op(**json.loads(line)))
                except (ValueError, TypeError):
                    continue

    def _add(self, op: Op) -> bool:
        if op.id in self._seen:
            return False
        self._seen.add(op.id)
        self.ops.append(op)
        return True

    def append(self, new: list[Op]) -> int:
        with self._lock:
            added = [o for o in new if self._add(o)]
            if added:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as fh:
                    for o in added:
                        fh.write(json.dumps(o.__dict__, ensure_ascii=False) + "\n")
            return len(added)


def _to_dict(op: Op) -> dict:
    return op.__dict__.copy()


def create_app(store_path: str | Path = ROOT / "data" / "ops.jsonl",
               notifier: Callable[[str, str], None] | None = None) -> FastAPI:
    app = FastAPI(title="Still Counts base")
    store = LogStore(Path(store_path))
    notified: set[str] = set()

    def check_oversell() -> None:
        over = set(stats(store.ops)["oversold"])
        fresh = over - notified
        notified.intersection_update(over)       # forget items that recovered, so a new oversell notifies again
        if fresh and notifier:
            notified.update(fresh)
            try:
                notifier("Oversell: needs a human", "Stock went negative after a merge: " + ", ".join(sorted(fresh)))
            except Exception:
                pass                              # a failed push must never break a sync

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/sync")
    def sync(body: SyncIn) -> dict:
        try:
            incoming = [Op(**o.model_dump()) for o in body.ops]
            dedupe(incoming)                      # validates every op; one bad op rejects the whole batch
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        accepted = store.append(incoming)
        check_oversell()
        return {"accepted": accepted, "cursor": len(store.ops), "ops": [_to_dict(o) for o in store.ops[body.cursor:]]}

    @app.get("/api/state")
    def state() -> dict:
        return {"state": merge(store.ops), "stats": stats(store.ops), "log_size": len(store.ops),
                "replicas": sorted({o.replica for o in store.ops})}

    SEED_STOCK = {"blankets": 100, "rice": 200, "water": 500, "paracetamol": 60, "tarpaulin": 40, "baby-food": 80}

    @app.post("/api/seed")
    def seed() -> dict:
        """Starting stock for the demo. Fixed ids make it idempotent: pressing it twice adds nothing."""
        ops = [Op(id=f"seed:{i}", replica="seed", lamport=1, kind="adjust", item=item, value=qty)
               for i, (item, qty) in enumerate(SEED_STOCK.items(), start=1)]
        return {"accepted": store.append(ops)}

    @app.get("/api/chaos")
    def chaos() -> dict:
        path = Path(store_path).parent / "chaos_results.json"
        if not path.exists():
            return {"runs": []}
        try:
            return json.loads(path.read_text())
        except ValueError:
            return {"runs": []}

    @app.get("/api/log")
    def log() -> dict:
        return {"ops": [_to_dict(o) for o in store.ops]}

    client_dir = ROOT / "client"

    @app.get("/")
    def index():
        return FileResponse(client_dir / "index.html")

    @app.get("/board")
    def board():
        return FileResponse(client_dir / "board.html")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(client_dir / "sw.js", media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=client_dir), name="static")
    return app


def build_notifier() -> Callable[[str, str], None] | None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return None
    from stillcounts.ntfy import notify
    return lambda title, msg: notify(topic, title, msg, priority=4, tags=["warning"])


app = create_app(notifier=build_notifier())
