"""The simulation: virtual time, a faulty network, the real server with crash faults, and phones that mirror
`client/app.js` (one `sc_state` record in localStorage, an outbox of unsynced ops, a sync cursor and an epoch).

Everything random comes from `Simulation.rng`. The phones are modelled with JavaScript number semantics: integers
above 2**53 are rounded to the nearest double when parsed or ticked, exactly as `JSON.parse` and `Clock.tick` would.
"""
from __future__ import annotations

import heapq
import json
import os
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from chaos.asgi import AsgiClient, deterministic_runtime
from oracle.oracle import OracleFailure, check_delivery, permutation_check
from server.app import LogStore, create_app
from stillcounts.crdt import Op, canonical, merge, stats
from stillcounts.outbox import Outbox

ITEMS = ["blankets", "rice", "water", "paracetamol", "tarpaulin", "baby-food"]
NAMES = ["Alpha", "Bravo", "Charlie", "Delta"]
BATCH = 500                       # client/app.js: unsent.slice(0, 500)
SYNC_EVERY_MS = 2000              # client/app.js: setInterval(sync, 2000)
WALL_EPOCH_MS = 1_792_800_000_000  # 24 Oct 2026, the phones' Date.now() at virtual time 0
JS_SAFE = 2 ** 53
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


# ----------------------------------------------------------------------------- JavaScript value semantics

def js_number(v: Any) -> Any:
    if isinstance(v, bool) or not isinstance(v, int) or -JS_SAFE <= v <= JS_SAFE:
        return v
    try:
        return int(float(v))
    except OverflowError:
        return float("inf")


def _js_int(text: str) -> Any:
    try:
        return js_number(int(text))
    except ValueError:            # more digits than Python parses; a double would be Infinity
        return float("inf")


def js_loads(text: str | bytes) -> Any:
    return json.loads(text, parse_int=_js_int)


def js_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class Lazy:
    """A localStorage value whose JSON text is produced only when someone reads it (a speed-up, same bytes)."""
    __slots__ = ("value", "_text")

    def __init__(self, value: Any) -> None:
        self.value = {k: list(v) if isinstance(v, list) else v for k, v in value.items()} \
            if isinstance(value, dict) else value
        self._text: str | None = None

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = js_dumps(self.value)
        return self._text


def text_of(v: "str | Lazy") -> str:
    return v.text if isinstance(v, Lazy) else v


def b36(n: int) -> str:
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = B36[r] + out
        if not n:
            return out


def op_key(o: Any) -> str:
    d = o.__dict__ if isinstance(o, Op) else o
    return json.dumps([d.get("id"), d.get("replica"), repr(d.get("lamport")), d.get("kind"), d.get("item"),
                       repr(d.get("value"))], ensure_ascii=True)


def to_op(d: dict) -> Op:
    return Op(**d)


class TabKilled(Exception):
    """The OS killed the browser tab: the handler stops here and in-memory state is gone."""


class QuotaExceeded(Exception):
    pass


# ----------------------------------------------------------------------------- server

class Server:
    """The real `server.app.create_app` on a real log file. Crashes, torn appends and disk faults are applied to that
    file; a restart is a fresh `create_app` on the same path."""

    def __init__(self, sim: "Simulation", path: Path) -> None:
        self.sim, self.path = sim, path
        self.client: AsgiClient | None = None
        self.notifications: list[tuple[str, str]] = []
        self.crashes = self.restarts = 0
        self.tear_next_append = False
        self.start()

    @property
    def up(self) -> bool:
        return self.client is not None

    def start(self) -> None:
        if self.client is not None:
            return
        app = create_app(self.path, notifier=lambda title, msg: self.notifications.append((title, msg)))
        self.client = AsgiClient(app)
        self.restarts += 1
        self.sim.note(f"server start #{self.restarts}")

    def stop(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def crash(self, restart_after: int | None = None) -> None:
        if not self.up:
            return
        self.crashes += 1
        self.sim.note("server crash")
        self.stop()
        if restart_after is not None:
            self.sim.after(restart_after, self.start)

    def _size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, bytes] | None:
        """Process one request. Returns None if the process died while handling it (no response is sent)."""
        assert self.client is not None
        before = self._size()
        status, out = self.client.request(method, path, body)
        if self.tear_next_append and self._size() > before:
            self.tear_next_append = False
            data = self.path.read_bytes()
            cut = before + self.sim.rng.randint(1, len(data) - before - 1)   # the crash beat the write to disk
            with self.path.open("r+b") as fh:
                fh.truncate(cut)
            self.sim.note(f"server killed mid-append: kept {cut - before} of {len(data) - before} new bytes")
            self.crash(restart_after=self.sim.rng.randint(300, 4000))
            return None
        return status, out

    # disk faults (applied while the server is down)
    def corrupt_line(self, index: int) -> None:
        lines = self.path.read_bytes().split(b"\n") if self.path.exists() else []
        if 0 <= index < len(lines) and lines[index]:
            lines[index] = lines[index][: max(1, len(lines[index]) // 2)]
            self.path.write_bytes(b"\n".join(lines))
            self.sim.note(f"disk fault: line {index} of the log truncated")

    def files(self) -> list[Path]:
        return [self.path, self.path.with_suffix(self.path.suffix + ".epoch")]

    def snapshot(self) -> dict[str, bytes | None]:
        return {p.name: (p.read_bytes() if p.exists() else None) for p in self.files()}

    def restore(self, snap: dict[str, bytes | None]) -> None:
        for p in self.files():
            data = snap.get(p.name)
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(data)
        self.sim.note("server data restored from an older backup")

    def wipe(self) -> None:
        for p in self.files():
            p.unlink(missing_ok=True)
        self.sim.note("server data directory wiped")


# ----------------------------------------------------------------------------- network

class Call:
    def __init__(self, sim: "Simulation", src: str, on_done: Callable[[int | None, bytes], None]) -> None:
        self.sim, self.src, self.on_done = sim, src, on_done
        self.done = False
        self.status: int | None = None

    def finish(self, status: int | None, body: bytes = b"") -> None:
        if self.done:
            return
        self.done, self.status = True, status
        self.sim.net.inflight -= 1
        self.on_done(status, body)


class Network:
    """Phone <-> base. Each leg can be dropped, delayed (which reorders) or cut by a partition; requests can be
    duplicated (a retransmitting proxy), and the duplicate reaches the server later with its response discarded."""

    def __init__(self, sim: "Simulation") -> None:
        self.sim = sim
        self.drop = 0.0
        self.dup = 0.0
        self.latency = (5, 120)
        self.timeout = 8000
        self.cut: set[str] = set()
        self.inflight = 0
        self.drop_next_response: set[str] = set()
        self.requests = 0

    def heal(self) -> None:
        self.drop = self.dup = 0.0
        self.cut.clear()
        self.drop_next_response.clear()

    def _lat(self) -> int:
        return self.sim.rng.randint(*self.latency)

    def blocked(self, src: str) -> bool:
        return src in self.cut

    def request(self, src: str, method: str, path: str, body: bytes,
                on_done: Callable[[int | None, bytes], None]) -> Call:
        rng = self.sim.rng
        call = Call(self.sim, src, on_done)
        self.inflight += 1
        self.requests += 1
        if self.blocked(src) or rng.random() < self.drop:
            self.sim.after(self.timeout, call.finish, None)
            return call
        if rng.random() < self.dup:
            self.inflight += 1
            self.sim.after(self._lat() * rng.randint(2, 40), self._arrive, src, None, method, path, body)
        self.sim.after(self._lat(), self._arrive, src, call, method, path, body)
        return call

    def _arrive(self, src: str, call: Call | None, method: str, path: str, body: bytes) -> None:
        server = self.sim.server
        if call is None:
            self.inflight -= 1                         # a duplicate: processed, response thrown away
            if server.up:
                server.handle(method, path, body)
            return
        if not server.up:
            self.sim.after(self._lat(), call.finish, None)          # connection refused
            return
        result = server.handle(method, path, body)
        if result is None:                                          # died mid-request
            self.sim.after(self.timeout, call.finish, None)
            return
        if self.blocked(src) or src in self.drop_next_response or self.sim.rng.random() < self.drop:
            self.drop_next_response.discard(src)
            self.sim.after(self.timeout, call.finish, None)
            return
        self.sim.after(self._lat(), call.finish, *result)


# ----------------------------------------------------------------------------- phones

class Device:
    """One phone (browser profile): localStorage, a wall clock that can be skewed, and its open tabs."""

    def __init__(self, sim: "Simulation", name: str, replica: str | None, quota: int | None) -> None:
        self.sim, self.name, self.quota = sim, name, quota
        self.storage: dict[str, str] = {}
        if replica:
            self.storage["sc_replica"] = js_dumps(replica)
        self.skew_ms = 0
        self.tabs: list[Phone] = []
        self.kill_on_save = False

    def now_ms(self) -> int:
        return WALL_EPOCH_MS + self.sim.now + self.skew_ms

    def set_item(self, key: str, text: "str | Lazy", writer: "Phone | None" = None) -> None:
        if self.kill_on_save:
            self.kill_on_save = False
            raise TabKilled(f"{self.name} killed while writing {key}")
        if self.quota is not None:
            used = sum(len(k) + len(text_of(v)) for k, v in self.storage.items() if k != key)
            if used + len(key) + len(text_of(text)) > self.quota:
                raise QuotaExceeded(key)
        self.storage[key] = text
        for tab in list(self.tabs):                      # the `storage` event fires in every OTHER tab of this origin
            if tab is not writer and tab.alive and key == "sc_state":
                tab.on_storage()

    @property
    def tab(self) -> "Phone | None":
        live = [t for t in self.tabs if t.alive]
        return live[0] if live else None

    def open_tab(self, kind: type | None = None) -> "Phone":
        tab = (kind or Phone)(self)
        self.tabs.append(tab)
        tab.boot()
        return tab

    def close_tabs(self) -> None:
        for t in self.tabs:
            t.alive = False
        self.tabs = []

    def reload(self) -> "Phone":
        kind = type(self.tabs[0]) if self.tabs else Phone
        self.close_tabs()
        return self.open_tab(kind)

    def wipe(self, keep_replica: str | None = None) -> None:
        """Clear site data or reinstall. Ops that never left the device are destroyed with it."""
        self.close_tabs()
        state = js_loads(text_of(self.storage["sc_state"])) if "sc_state" in self.storage else {}
        synced = set(state.get("synced") or [])
        self.sim.destroy([o["id"] for o in state.get("ops") or [] if o["id"] not in synced], self)
        self.storage = {"sc_replica": js_dumps(keep_replica)} if keep_replica else {}


class Phone:
    """One tab running client/app.js. Method names follow the JavaScript functions they mirror."""

    def __init__(self, device: Device) -> None:
        self.device, self.sim = device, device.sim
        self.alive = True
        self.online = False
        self.busy = False

    @property
    def name(self) -> str:
        return self.device.name

    # localStorage helpers, as in app.js
    def load(self, key: str, fallback: Any) -> Any:
        try:
            v = js_loads(text_of(self.device.storage[key]))
            return fallback if v is None else v
        except (KeyError, ValueError):
            return fallback

    def save(self, key: str, value: Any) -> bool:
        try:
            self.device.set_item(key, js_dumps(value) if self.device.quota is not None else Lazy(value), self)
            return True
        except QuotaExceeded:
            self.sim.note(f"{self.name}: localStorage quota exceeded writing {key}")
            return False

    def boot(self) -> None:
        self.replica = self.load("sc_replica", None)
        if not self.replica:
            self.replica = "ph-" + "".join(self.sim.rng.choice(B36) for _ in range(5))
            self.save("sc_replica", self.replica)
        saved = self.load("sc_state", {})
        self.ops: list[dict] = list(saved.get("ops") or [])
        self.synced: dict[str, bool] = {i: True for i in saved.get("synced") or []}
        self.cursor = saved.get("cursor") or 0
        self.epoch = saved.get("epoch") or None
        self.anchor = saved.get("anchor") or None
        self.storage_full = False
        self.rejected = saved.get("rejected") or 0
        self.clock = saved.get("lamport") or 0
        self.known = {o["id"]: True for o in self.ops}
        self.sim.at(self.sim.now + self.sim.rng.randint(0, SYNC_EVERY_MS), self._interval)
        self.sync()

    def _interval(self) -> None:
        if not self.alive:
            return
        if not self.sim.quiescing:
            self.sync()
        self.sim.after(SYNC_EVERY_MS, self._interval)

    def tick(self) -> int:
        self.clock = js_number(self.clock + 1)
        return self.clock

    def observe(self, remote: Any) -> None:
        if isinstance(remote, (int, float)) and not isinstance(remote, bool):
            self.clock = max(self.clock, remote)

    def absorb(self, stored: Any) -> bool:
        """app.js absorb(): fold in whatever another tab of this phone wrote to the shared sc_state record."""
        if not isinstance(stored, dict) or "ops" not in stored:
            return False
        added = False
        marks = set(stored.get("synced") or [])
        for o in stored.get("ops") or []:
            if not self.known.get(o["id"]):
                self.ops.append(o)
                self.known[o["id"]] = True
                added = True
                if o["id"] in marks:
                    self.synced[o["id"]] = True
        self.observe(stored.get("lamport") or 0)
        self.rejected = max(self.rejected, stored.get("rejected") or 0)
        return added

    def on_storage(self) -> None:
        self.absorb(self.load("sc_state", None))

    def persist(self) -> None:
        self.absorb(self.load("sc_state", None))
        rec = {"ops": self.ops, "synced": list(self.synced), "cursor": self.cursor, "epoch": self.epoch,
               "anchor": self.anchor, "rejected": self.rejected, "lamport": self.clock}
        if self.save("sc_state", rec):
            self.storage_full = False
            return
        # Storage is full: keep only the unsent ops on disk and forget the cursor (app.js persist()).
        unsent = [o for o in self.ops if not self.synced.get(o["id"])]
        self.storage_full = not self.save("sc_state", {"ops": unsent, "synced": [], "cursor": 0, "epoch": None,
                                                       "anchor": None, "rejected": self.rejected, "lamport": self.clock})

    def uid(self) -> str:
        return b36(self.device.now_ms()) + "".join(self.sim.rng.choice(B36) for _ in range(6))

    def add_op(self, kind: str, item: str, value: Any, **override: Any) -> dict:
        self.absorb(self.load("sc_state", None))
        op = {"id": self.replica + ":" + self.uid(), "replica": self.replica, "lamport": self.tick(),
              "kind": kind, "item": item, "value": value}
        op.update(override)
        self.ops.append(op)
        self.known[op["id"]] = True
        try:
            self.persist()
        except TabKilled:
            raise                      # killed before the write and before render: the user never saw this op
        self.sim.created_op(self.device, op)
        self.sync()
        return op

    def unsent(self) -> list[dict]:
        return [o for o in self.ops if not self.synced.get(o["id"])]

    def sync(self) -> None:
        if not self.alive or self.busy:
            return
        self.busy = True
        batch = self.unsent()[:BATCH]
        body = js_dumps({"replica": self.replica, "cursor": self.cursor, "epoch": self.epoch, "anchor": self.anchor,
                         "ops": batch})
        self.sim.net.request(self.name, "POST", "/api/sync", body.encode(),
                             lambda status, data: self.sim.on_device(self.device, lambda: self._reply(batch, status, data), self))

    def restamp(self, r: dict, newest: Any) -> bool:
        """app.js restamp(): an op whose Lamport time the base refused gets a new one after everything the base holds."""
        if not r.get("id") or not isinstance(newest, int) or not str(r.get("reason") or "").startswith("lamport"):
            return False
        for o in self.ops:
            if o["id"] == r["id"]:
                o["lamport"] = newest + 1
                self.clock = newest + 1
                self.sim.supersede(o["id"], o)       # the volunteer's tap is unchanged; only its Lamport time moved
                return True
        return False

    def _reply(self, batch: list[dict], status: int | None, data: bytes) -> None:
        if not self.alive:
            return                     # the page that sent this request is gone
        try:
            if status is None or not 200 <= status < 300:
                raise ConnectionError(f"HTTP {status}")
            body = js_loads(data)
            if body.get("reset"):
                self.synced = {}
            again: set[str] = set()
            newest = body.get("max_lamport")
            for r in body.get("rejected") or []:
                if self.restamp(r, newest):
                    again.add(r["id"])
                    newest += 1
                elif r.get("id"):
                    self.rejected += 1
                    self.sim.server_rejected(self.device, r)
            for o in batch:
                if o["id"] not in again:
                    self.synced[o["id"]] = True
            for o in body["ops"]:
                if not self.known.get(o["id"]):
                    self.ops.append(o)
                    self.known[o["id"]] = True
                self.synced[o["id"]] = True
                self.observe(o["lamport"])
            for r in body.get("rejected") or []:
                if r.get("id") and r["id"] not in again:
                    self.synced[r["id"]] = True
            if body["ops"]:
                self.anchor = body["ops"][-1]["id"]
            elif body["cursor"] == 0:
                self.anchor = None
            self.cursor = body["cursor"]
            self.epoch = body.get("epoch")
            self.online = True
            self.persist()
        except TabKilled:
            raise
        except Exception as exc:       # app.js: .catch(function () { online = false; })
            if status == 200:
                self.sim.note(f"{self.name}: bad sync response: {exc!r}")
            self.online = False
        self.busy = False

    def fingerprint(self) -> tuple:
        return (len(self.ops), self.cursor, self.epoch, len(self.unsent()), self.rejected)


class OutboxPhone(Phone):
    """A phone whose queue is the real `stillcounts.outbox.Outbox` file. `Outbox.flush` calls `send` once per op and
    each send blocks the phone while virtual time (and the volunteer's taps) carry on."""

    def boot(self) -> None:
        self.outbox = Outbox(self.sim.workdir / f"outbox-{self.name}.jsonl")
        super().boot()

    def add_op(self, kind: str, item: str, value: Any, **override: Any) -> dict:
        op = {"id": self.replica + ":" + self.uid(), "replica": self.replica, "lamport": self.tick(),
              "kind": kind, "item": item, "value": value}
        op.update(override)
        self.ops.append(op)
        self.known[op["id"]] = True
        self.outbox.enqueue(op)
        self.persist()
        self.sim.created_op(self.device, op)
        self.sync()
        return op

    def unsent(self) -> list[dict]:
        return [None] * self.outbox.pending()            # only the count matters for the fingerprint

    def sync(self) -> None:
        if not self.alive or self.busy:
            return
        self.busy = True
        try:
            self.outbox.flush(self._send)
            if not self.outbox.pending():
                self._send(None)                         # a pull with no ops
        except ConnectionError:
            pass
        finally:
            self.busy = False

    def _send(self, item: dict | None) -> None:
        batch = [item] if item else []
        body = js_dumps({"replica": self.replica, "cursor": self.cursor, "epoch": self.epoch, "ops": batch})
        result: dict = {}
        call = self.sim.net.request(self.name, "POST", "/api/sync", body.encode(),
                                    lambda status, data: result.update(status=status, data=data))
        self.sim.run_until_done(call)
        if result.get("status") != 200:
            raise ConnectionError(f"HTTP {result.get('status')}")
        reply = js_loads(result["data"])
        for o in reply["ops"]:
            if not self.known.get(o["id"]):
                self.ops.append(o)
                self.known[o["id"]] = True
            self.observe(o["lamport"])
        for r in reply.get("rejected") or []:
            if r.get("id"):
                self.rejected += 1
                self.sim.server_rejected(self.device, r)
        self.cursor, self.epoch = reply["cursor"], reply.get("epoch")
        self.persist()


# ----------------------------------------------------------------------------- the simulation

class Simulation:
    def __init__(self, seed: int, scenario: str = "", workdir: str | Path | None = None, trace: bool = False) -> None:
        self.seed, self.scenario = seed, scenario
        self.rng = random.Random(f"{scenario}:{seed}")
        self.now = 0
        self._queue: list[tuple[int, int, Callable, tuple]] = []
        self._seq = 0
        self.quiescing = False
        self.trace: list[str] | None = [] if trace else None
        base = "/dev/shm" if os.path.isdir("/dev/shm") else None
        self._own_dir = workdir is None
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="chaos-", dir=base))
        self._runtime = deterministic_runtime(self.rng)
        self._runtime.__enter__()
        self.net = Network(self)
        self.server = Server(self, self.workdir / "ops.jsonl")
        self.devices: dict[str, Device] = {}
        self.created: list[tuple[str, dict]] = []      # (device, op) for every op a volunteer saw on screen
        self.expected_rejected: set[str] = set()        # ids a scenario injected as invalid on purpose
        self.destroyed: set[str] = set()                # ids wiped with site data before they left the phone
        self.unexpected_rejections: list[tuple[str, str, str]] = []
        self.labels: list[str] = []

    # --- bookkeeping
    def note(self, msg: str) -> None:
        if self.trace is not None:
            self.trace.append(f"t={self.now:>7} {msg}")

    def label(self, text: str) -> None:
        self.labels.append(text)

    def created_op(self, device: Device, op: dict) -> None:
        self.created.append((device.name, dict(op)))
        self.note(f"{device.name} created {op['id']} {op['kind']} {op['item']}={op['value']!r} @{op['lamport']!r}")

    def supersede(self, op_id: str, new: dict) -> None:
        """A (buggy) client edited an op in place: the volunteer now expects the new content."""
        self.created = [(d, dict(new) if o["id"] == op_id else o) for d, o in self.created]

    def expect_rejected(self, op: dict | str) -> None:
        self.expected_rejected.add(op if isinstance(op, str) else op["id"])

    def destroy(self, ids: list[str], device: Device) -> None:
        self.destroyed.update(ids)
        if ids:
            self.note(f"{device.name}: wipe destroyed {len(ids)} unsynced ops")

    def server_rejected(self, device: Device, r: dict) -> None:
        if r["id"] not in self.expected_rejected:
            self.unexpected_rejections.append((device.name, r["id"], str(r.get("reason"))))
            self.note(f"server rejected {r['id']} from {device.name}: {r.get('reason')}")

    # --- event loop
    def at(self, t: int, fn: Callable, *args: Any) -> None:
        self._seq += 1
        heapq.heappush(self._queue, (max(t, self.now), self._seq, fn, args))

    def after(self, dt: int, fn: Callable, *args: Any) -> None:
        self.at(self.now + dt, fn, *args)

    def _step(self) -> None:
        t, _, fn, args = heapq.heappop(self._queue)
        self.now = t
        fn(*args)

    def run(self, until: int) -> None:
        while self._queue and self._queue[0][0] <= until:
            self._step()
        self.now = max(self.now, until)

    def run_until_done(self, call: Call) -> None:
        while not call.done and self._queue:
            self._step()

    def run_until_idle(self, limit: int = 100_000) -> None:
        steps = 0
        while self.net.inflight and self._queue and steps < limit:
            self._step()
            steps += 1

    # --- actors and faults
    def device(self, name: str, replica: str | None = None, quota: int | None = None,
               kind: type | None = None, open_tab: bool = True) -> Device:
        dev = Device(self, name, replica, quota)
        self.devices[name] = dev
        if open_tab:
            self.on_device(dev, lambda: dev.open_tab(kind))
        return dev

    def on_device(self, dev: Device, fn: Callable, tab: "Phone | None" = None) -> Any:
        """Run fn as an event of this device; a TabKilled kills that tab (or every tab of the device)."""
        try:
            return fn()
        except TabKilled as exc:
            self.note(str(exc))
            for t in ([tab] if tab else list(dev.tabs)):
                t.alive = False
            dev.tabs = [t for t in dev.tabs if t.alive]
            return None

    def tap(self, dev: Device, kind: str, item: str, value: Any, **override: Any) -> dict | None:
        tab = dev.tab
        if tab is None:
            return None                                  # the app is closed; nobody can tap
        return self.on_device(dev, lambda: tab.add_op(kind, item, value, **override), tab)

    def random_op(self, items: list[str] = ITEMS) -> tuple[str, str, Any]:
        rng = self.rng
        kind = rng.choice(["adjust", "adjust", "adjust", "create", "note", "delete"])
        item = rng.choice(items)
        if kind == "adjust":
            value: Any = rng.choice([-1, 1]) * rng.randint(1, 40)
        elif kind == "delete":
            value = rng.random() < 0.3
        else:
            value = rng.choice(NAMES)
        return kind, item, value

    def workload(self, devices: list[Device], n: int, start: int, end: int, items: list[str] = ITEMS) -> None:
        for _ in range(n):
            dev = self.rng.choice(devices)
            kind, item, value = self.random_op(items)
            self.at(self.rng.randint(start, end), self.tap, dev, kind, item, value)

    def noisy_network(self, level: float = 1.0) -> None:
        rng = self.rng
        self.net.drop = rng.uniform(0, 0.3) * level
        self.net.dup = rng.uniform(0, 0.25) * level
        self.net.latency = (rng.randint(1, 20), rng.randint(40, int(40 + 600 * level)))
        self.label(f"drop={self.net.drop:.2f} dup={self.net.dup:.2f} lat<={self.net.latency[1]}ms")

    def partition(self, dev: Device, start: int, end: int) -> None:
        self.at(start, self.net.cut.add, dev.name)
        self.at(end, self.net.cut.discard, dev.name)

    def random_partitions(self, devices: list[Device], span: int, count: int) -> None:
        for _ in range(count):
            a = self.rng.randint(0, span)
            self.partition(self.rng.choice(devices), a, a + self.rng.randint(500, span // 2))

    def crash_server(self, t: int, torn: bool, restart_after: tuple[int, int] = (300, 4000)) -> None:
        def go() -> None:
            if torn:
                self.server.tear_next_append = True      # dies inside the next append that writes bytes
            else:
                self.server.crash(restart_after=self.rng.randint(*restart_after))
        self.at(t, go)

    def reload(self, dev: Device, t: int) -> None:
        self.at(t, lambda: self.on_device(dev, dev.reload))

    # --- the end of a run
    def _fingerprint(self) -> tuple:
        return (self.server.path.stat().st_size if self.server.path.exists() else 0,
                tuple((n, d.tab.fingerprint() if d.tab else None) for n, d in self.devices.items()))

    def quiesce(self, max_rounds: int = 60) -> bool:
        """Sync every phone, one after another, until a full round changes nothing."""
        self.quiescing = True
        self.server.start()
        for dev in self.devices.values():
            if dev.tab is None:
                self.on_device(dev, dev.reload)
        self.run_until_idle()
        last, stable = None, 0
        for _ in range(max_rounds):
            for dev in self.devices.values():
                tab = dev.tab
                if tab is not None:
                    self.on_device(dev, tab.sync)
                    self.run_until_idle()
            fp = self._fingerprint()
            stable = stable + 1 if fp == last else 0
            last = fp
            if stable >= 1:
                return True
        return False

    def finish(self) -> dict:
        """Heal everything, sync to quiescence, restart the server and every app, sync again, then ask the referee."""
        self.net.heal()
        self.server.tear_next_append = False
        for dev in self.devices.values():
            dev.kill_on_save = False
        settled = self.quiesce()
        self.server.stop()
        self.server.start()
        for dev in self.devices.values():
            self.on_device(dev, dev.reload)
        settled = self.quiesce() and settled
        try:
            return self.referee(settled)
        finally:
            self.close()

    def server_log(self) -> tuple[list[dict], str | None]:
        assert self.server.client is not None
        status, body = self.server.client.request("GET", "/api/log")
        if status != 200:
            return [o.__dict__ for o in LogStore(self.server.path).ops], f"GET /api/log returned {status}"
        st, _ = self.server.client.request("GET", "/api/state")
        if st != 200:
            return json.loads(body)["ops"], f"GET /api/state returned {st}"
        return json.loads(body)["ops"], None

    def referee(self, settled: bool) -> dict:
        server_ops, server_err = self.server_log()
        # An injected invalid op only stays out of the expected set if the server really refused it, and a wiped op
        # only if it never left the phone: whatever reached any replica must reach every replica.
        refused = self.expected_rejected - {o.get("id") for o in server_ops}
        logs: dict[str, list[dict]] = {"server": server_ops}
        for name, dev in self.devices.items():
            logs[name] = [o for o in (dev.tab.ops if dev.tab else []) if o["id"] not in refused]
        seen = {o.get("id") for log in logs.values() for o in log}
        skip = refused | (self.destroyed - seen)
        created = [o for _, o in self.created if o["id"] not in skip]
        errors: list[str] = []
        if server_err:
            errors.append(server_err)
        if not settled:
            errors.append("did not reach quiescence")
        keys = {op_key(o) for o in created}
        lost = len({k for log in logs.values() for k in keys - {op_key(o) for o in log}})
        conflicts = 0
        try:
            created_ops = [to_op(o) for o in created]
            deliveries = {name: [to_op(o) for o in log] for name, log in logs.items()}
            check_delivery(created_ops, deliveries)
            states = {name: canonical(merge(ops)) for name, ops in deliveries.items()}
            if len(set(states.values())) > 1:
                errors.append("states are not byte-identical")
            permutation_check(created_ops, perms=6, seed=self.seed)
            conflicts = stats(created_ops)["conflicts"]
        except OracleFailure as exc:
            errors.append(str(exc))
        except (ValueError, TypeError) as exc:
            errors.append(f"merge refused a delivered log: {exc}")
        if lost and not any("lost" in e for e in errors):
            errors.append(f"{lost} created ops are missing or altered on some replica")
        if self.unexpected_rejections and errors:
            dev, oid, reason = self.unexpected_rejections[0]
            errors.append(f"server rejected {len(self.unexpected_rejections)} real ops, first {oid} from {dev}: "
                          f"{reason}")
        ok = not errors
        extra = []
        if self.destroyed - seen:
            extra.append(f"{len(self.destroyed - seen)} unsynced ops destroyed by a wipe")
        if self.expected_rejected:
            extra.append(f"{len(self.expected_rejected)} invalid ops injected")
        label = "; ".join(self.labels + extra + (["PASS"] if ok else []))
        return {"seed": self.seed, "scenario": self.scenario, "ok": ok, "ops": len(created), "conflicts": conflicts,
                "lost": lost, "label": label, "error": " | ".join(errors) or None,
                "requests": self.net.requests, "crashes": self.server.crashes, "virtual_ms": self.now}

    def close(self) -> None:
        self.server.stop()
        self._runtime.__exit__(None, None, None)
        if self._own_dir:
            shutil.rmtree(self.workdir, ignore_errors=True)
