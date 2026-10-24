"""The 'base' sync server. It only stores and relays operations; all merging is the pure CRDT, so it can never lose an update."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

import segno
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stillcounts.crdt import Op, dedupe, merge, stats

ROOT = Path(__file__).resolve().parent.parent
MAX_OPS_PER_SYNC = 500
MAX_ID = 64
MAX_TEXT = 200
MAX_LAMPORT = 2 ** 53 - 1          # beyond this a JavaScript number loses precision
MAX_SKEW = 100_000                 # how far ahead of the log's newest Lamport time an op may claim to be
MAX_QTY = 10 ** 9
OP_FIELDS = {"id", "replica", "lamport", "kind", "item", "value"}


def _clean_text(value: str, limit: int) -> str | None:
    """Return a reason if the text is unusable, else None. Rejects control characters and lone surrogates."""
    if not value or len(value) > limit:
        return f"text must be 1 to {limit} characters"
    if any(ord(ch) < 32 for ch in value):
        return "control characters are not allowed"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "unpaired surrogate in text"
    return None


def validate_op(raw: Any, max_seen: int) -> tuple[Op | None, str | None]:
    """Strict per-operation validation: no coercion, no unbounded values. Returns (op, None) or (None, reason)."""
    if not isinstance(raw, dict) or set(raw) != OP_FIELDS:
        return None, "operation must have exactly: " + ", ".join(sorted(OP_FIELDS))
    for key in ("id", "replica", "item"):
        if not isinstance(raw[key], str):
            return None, f"{key} must be a string"
        reason = _clean_text(raw[key], MAX_ID)
        if reason:
            return None, f"{key}: {reason}"
    lam = raw["lamport"]
    if not isinstance(lam, int) or isinstance(lam, bool) or not 1 <= lam <= MAX_LAMPORT:
        return None, "lamport must be an integer between 1 and 2^53-1"
    if lam > max_seen + MAX_SKEW:
        return None, f"lamport skew too large (more than {MAX_SKEW} ahead of the log)"
    value = raw["value"]
    if raw["kind"] == "adjust" and isinstance(value, int) and not isinstance(value, bool) and abs(value) > MAX_QTY:
        return None, "adjust magnitude too large"
    if raw["kind"] in ("create", "note") and isinstance(value, str):
        if value == "" and raw["kind"] == "create":
            return None, "create needs a non-empty name"
        if value:
            reason = _clean_text(value, MAX_TEXT)
            if reason:
                return None, f"value: {reason}"
    op = Op(id=raw["id"], replica=raw["replica"], lamport=lam, kind=raw["kind"], item=raw["item"], value=value)
    try:
        dedupe([op])
    except ValueError as exc:
        return None, str(exc)
    return op, None


class SyncIn(BaseModel):
    replica: str = Field(min_length=1, max_length=MAX_ID)
    cursor: int = Field(ge=0, default=0)
    epoch: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    ops: list[Any] = Field(default_factory=list, max_length=MAX_OPS_PER_SYNC)


class LogStore:
    """An append-only operation log on disk. Crash-safe appends, tolerant loading, and an epoch that changes whenever
    positions in the log may have shifted, so clients never trust a stale cursor."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()
        self.ops: list[Op] = []
        self.by_id: dict[str, Op] = {}
        self.max_lamport = 0
        self._needs_newline = False
        epoch_path = self.path.with_suffix(self.path.suffix + ".epoch")
        skipped = 0
        if self.path.exists():
            raw = self.path.read_bytes().decode("utf-8", errors="replace")
            self._needs_newline = bool(raw) and not raw.endswith("\n")
            for line in raw.split("\n"):           # NOT splitlines(): it would also split on U+2028 inside text
                if not line.strip():
                    continue
                try:
                    op = Op(**json.loads(line))
                    dedupe([op])
                    self._remember(op)
                except (ValueError, TypeError):
                    skipped += 1
        if skipped or not epoch_path.exists():
            epoch_path.parent.mkdir(parents=True, exist_ok=True)
            epoch_path.write_text(uuid.uuid4().hex)
        self.epoch = epoch_path.read_text().strip()

    def _remember(self, op: Op) -> None:
        self.by_id[op.id] = op
        self.ops.append(op)
        self.max_lamport = max(self.max_lamport, op.lamport)

    def classify(self, op: Op) -> str:
        cur = self.by_id.get(op.id)
        if cur is None:
            return "new"
        return "dup" if cur == op else "conflict"

    def append(self, new: list[Op]) -> int:
        """Append only the operations that are new; returns how many were added."""
        with self.lock:
            fresh = [o for o in new if self.classify(o) == "new"]
            self.append_locked(fresh)
            return len(fresh)

    def append_locked(self, new: list[Op]) -> None:
        if not new:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            if self._needs_newline:
                fh.write("\n")                      # seal a torn last line so the next op starts clean
                self._needs_newline = False
            for o in new:
                fh.write(json.dumps(o.__dict__, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        for o in new:
            self._remember(o)


def _to_dict(op: Op) -> dict:
    return op.__dict__.copy()


_REFEREE: dict | None = None


def _referee_score() -> dict:
    """Run the mutation test once and keep the answer: how many deliberately broken merges the referee catches."""
    global _REFEREE
    if _REFEREE is None:
        from oracle.mutation import run
        _REFEREE = run()
    return _REFEREE


def create_app(store_path: str | Path = ROOT / "data" / "ops.jsonl",
               notifier: Callable[[str, str], None] | None = None) -> FastAPI:
    app = FastAPI(title="Still Counts base")
    store = LogStore(Path(store_path))
    names_path = Path(store_path).with_suffix(Path(store_path).suffix + ".names")
    try:
        names: dict[str, str] = json.loads(names_path.read_text())
    except (OSError, ValueError):
        names = {}

    def remember_name(replica: str, name: str | None) -> None:
        if not name or _clean_text(name.strip(), 40) is not None:
            return
        names[replica] = name.strip()
        names_path.write_text(json.dumps(names, ensure_ascii=False))

    def describe(op: Op, state: dict) -> str:
        label = state.get(op.item, {}).get("name", op.item)
        if op.kind == "adjust":
            return f"{'+' if op.value > 0 else ''}{op.value} {label}"
        return {"create": f"named {op.item} {op.value!r}", "note": f"noted {label}", "delete": ("removed " if op.value else "restored ") + label}[op.kind]
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
        remember_name(body.replica, body.name)
        accepted: list[Op] = []
        rejected: list[dict] = []
        with store.lock:                          # one consistent snapshot: validate, append, then slice the log
            seen_max = store.max_lamport
            for raw in body.ops:
                op, reason = validate_op(raw, seen_max)
                if reason:
                    rejected.append({"id": raw.get("id") if isinstance(raw, dict) else None, "reason": reason})
                    continue
                status = store.classify(op)
                if status == "conflict":
                    rejected.append({"id": op.id, "reason": "id reused with different content"})
                elif status == "new" and all(a.id != op.id for a in accepted):
                    accepted.append(op)
                    seen_max = max(seen_max, op.lamport)
            store.append_locked(accepted)
            fresh_log = body.epoch in (None, store.epoch) and body.cursor <= len(store.ops)
            start = body.cursor if fresh_log else 0
            out = [_to_dict(o) for o in store.ops[start:]]
            cursor, epoch = len(store.ops), store.epoch
        check_oversell()
        return {"accepted": len(accepted), "rejected": rejected, "cursor": cursor, "epoch": epoch, "ops": out}

    @app.get("/api/state")
    def state() -> dict:
        with store.lock:
            ops = list(store.ops)
        merged = merge(ops)
        activity = [{"who": names.get(o.replica, o.replica), "text": describe(o, merged)} for o in ops[-10:]]
        return {"state": merged, "stats": stats(ops), "log_size": len(ops), "names": dict(names),
                "replicas": sorted({o.replica for o in ops}), "activity": activity}

    @app.get("/api/export.csv")
    def export_csv() -> Response:
        """A paper-fallback export: item, name, quantity, note, status. Cells that could run as spreadsheet formulas are neutralised."""
        with store.lock:
            ops = list(store.ops)
        merged = merge(ops)
        over = set(stats(ops)["oversold"])

        def safe(value: str) -> str:
            return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["item", "name", "qty", "note", "status"])
        for item, s in merged.items():
            if s["alive"]:
                writer.writerow([safe(item), safe(s["name"]), s["qty"], safe(s["note"]), "needs a human" if item in over else "ok"])
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=stillcounts-ledger.csv"})

    SEED_STOCK = {"blankets": 100, "rice": 200, "water": 500, "paracetamol": 60, "tarpaulin": 40, "baby-food": 80}
    SEED_NAMES = {"blankets": "Blankets", "rice": "Rice (kg)", "water": "Drinking water (L)",
                  "paracetamol": "Paracetamol (strips)", "tarpaulin": "Tarpaulins", "baby-food": "Baby food (tins)"}

    @app.post("/api/seed")
    def seed() -> dict:
        """Starting stock for the demo. Fixed ids make it idempotent: pressing it twice adds nothing."""
        ops = [Op(id=f"seed:{i}", replica="seed", lamport=1, kind="adjust", item=item, value=qty)
               for i, (item, qty) in enumerate(SEED_STOCK.items(), start=1)]
        ops += [Op(id=f"seed:{i}", replica="seed", lamport=1, kind="create", item=item, value=name)
                for i, (item, name) in enumerate(SEED_NAMES.items(), start=len(SEED_STOCK) + 1)]
        with store.lock:
            fresh = [o for o in ops if store.classify(o) == "new"]
            store.append_locked(fresh)
        return {"accepted": len(fresh)}

    @app.get("/api/chaos")
    def chaos() -> dict:
        path = Path(store_path).parent / "chaos_results.json"
        if not path.exists():
            return {"runs": []}
        try:
            return json.loads(path.read_text())
        except ValueError:
            return {"runs": []}

    @app.get("/api/referee")
    def referee() -> dict:
        from functools import lru_cache
        return _referee_score()

    @app.get("/api/devin")
    def devin_counts() -> dict:
        path = Path(store_path).parent / "devin_counts.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {"sessions": 0, "prs": 0, "structured_outputs": 0, "by_status": {}}

    @app.get("/api/qr.svg")
    def qr(text: str = Query(..., max_length=300)) -> Response:
        if not text.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="only http(s) links")
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="svg", scale=8, border=2, dark="#f2f2ee", light=None)
        return Response(buf.getvalue(), media_type="image/svg+xml")

    @app.get("/api/log")
    def log() -> dict:
        with store.lock:
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
