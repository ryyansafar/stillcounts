"""The 24 hostile scenarios from docs/scout_scenarios.json, plus a control, as seeded simulation setups.

Each function receives a fresh `Simulation`, builds phones, schedules taps and faults, and returns. The runner then
heals, syncs to quiescence and calls the referee. Ops a scenario injects as invalid on purpose are declared with
`sim.expect_rejected`; every other op a volunteer saw on screen must end up everywhere.
"""
from __future__ import annotations

import unicodedata
from typing import Callable

from chaos.sim import ITEMS, OutboxPhone, Simulation, js_number

SCENARIOS: dict[str, tuple[Callable[[Simulation], None], str, str]] = {}


def scenario(scout: str, title: str):
    def deco(fn):
        SCENARIOS[fn.__name__.removeprefix("s_")] = (fn, scout, title)
        return fn
    return deco


def phones(sim: Simulation, n: int, **kw):
    return [sim.device(f"ph-{chr(97 + i)}", **kw) for i in range(n)]


def base(sim: Simulation, n_phones: int = 3, ops: tuple[int, int] = (15, 40), span: int = 20_000, noise: float = 1.0):
    devs = phones(sim, n_phones)
    sim.noisy_network(noise)
    sim.workload(devs, sim.rng.randint(*ops), 0, span)
    sim.random_partitions(devs, span, sim.rng.randint(0, 3))
    return devs, span


def end(sim: Simulation, span: int) -> None:
    sim.run(span + 3000)


# ============================================================================ SCOUT-NETWORK

@scenario("SCOUT-NETWORK", "Server crash mid-append tears a line; the next accepted op merges into it and disappears on restart")
def s_net_torn_append_crash(sim):
    devs, span = base(sim)
    for _ in range(sim.rng.randint(1, 4)):
        sim.crash_server(sim.rng.randint(0, span), torn=True)
    end(sim, span)


@scenario("SCOUT-NETWORK", "A retry with the same id but different content (edited queued op, or reinstalled phone restarts its counter)")
def s_net_retry_same_id_new_content(sim):
    devs, span = base(sim, noise=0.5)
    a = devs[0]
    first = sim.tap(a, "adjust", "rice", 5)
    sim.net.drop_next_response.add(a.name)
    sim.run(3000)
    if sim.rng.random() < 0.5:
        sim.label("variant: buggy client edits the queued op in place")
        tab = a.tab
        edited = dict(first, value=9, lamport=tab.tick())
        tab.ops = [edited if o["id"] == first["id"] else o for o in tab.ops]
        tab.synced.pop(first["id"], None)
        tab.persist()
        server_ops, _ = sim.server_log()
        if any(o["id"] == first["id"] for o in server_ops):
            sim.created_op(a, edited)                     # the base already holds the original tap: both taps are real
        else:
            sim.supersede(first["id"], edited)            # the original never left the phone: the edit replaces it
    else:
        sim.label("variant: legacy client reinstalls and reuses its old op id")
        a.wipe(keep_replica=a.tab.replica if a.tab else None)
        tab = a.open_tab()
        sim.tap(a, "adjust", "rice", 9, id=first["id"])
    end(sim, span)


@scenario("SCOUT-NETWORK", "A restart drops a corrupt log line, so every index cursor moves and late-syncing replicas skip ops for good")
def s_net_corrupt_line_shifts_cursor(sim):
    devs, span = base(sim, noise=0.3)
    late = devs[-1]
    t = sim.rng.randint(span // 3, span // 2)
    sim.partition(late, t - 1000, span)

    def fault():
        sim.server.crash()
        n = len(sim.server.path.read_bytes().split(b"\n")) if sim.server.path.exists() else 0
        if n > 1:
            sim.server.corrupt_line(sim.rng.randint(0, n - 2))
        sim.after(sim.rng.randint(300, 3000), sim.server.start)
    sim.at(t, fault)
    sim.label("disk fault truncates one earlier log line")
    end(sim, span)


@scenario("SCOUT-NETWORK", "A partition heals after the server was restored from an older backup: the stale cursor runs past the end of the log")
def s_net_restore_old_backup(sim):
    devs, span = base(sim, ops=(30, 60), noise=0.3)
    t0 = sim.rng.randint(1000, span // 3)
    t1 = sim.rng.randint(span // 2, span)
    snap = {}
    sim.at(t0, lambda: snap.update(sim.server.snapshot()))
    sim.partition(devs[0], t1 - 2000, span + 2000)

    def restore():
        sim.server.crash()
        sim.server.restore(snap)
        sim.server.start()
    sim.at(t1, restore)
    end(sim, span)


@scenario("SCOUT-NETWORK", "Outbox.flush clobbers an op enqueued while a slow sync is in flight")
def s_net_outbox_clobber_inflight(sim):
    devs = [sim.device(f"ph-{c}", kind=OutboxPhone) for c in "ab"]
    span = 15_000
    sim.net.latency = (200, sim.rng.randint(400, 3000))
    sim.net.drop = sim.rng.uniform(0, 0.2)
    sim.workload(devs, sim.rng.randint(10, 30), 0, span)
    sim.label(f"slow network {sim.net.latency[1]}ms, real stillcounts.outbox.Outbox")
    end(sim, span)


POISON = [("adjust", 0), ("adjust", True), ("adjust", 2.5), ("note", "x" * 201), ("restock", 5)]


@scenario("SCOUT-NETWORK", "A huge offline backlog contains one poison op, so every batch after it stalls behind a 422")
def s_net_poison_in_huge_backlog(sim):
    devs = phones(sim, 2)
    a = devs[0]
    n = sim.rng.randint(520, 1300)
    bad_at = sim.rng.randrange(n)
    kind, value = sim.rng.choice(POISON)
    sim.net.cut.add(a.name)
    for i in range(n):
        if i == bad_at:
            sim.expect_rejected(sim.tap(a, kind, "rice", value))
        else:
            sim.tap(a, *sim.random_op())
        sim.now += 10
    sim.workload(devs[1:], 20, sim.now, sim.now + 5000)
    sim.at(sim.now + sim.rng.randint(1000, 5000), sim.net.cut.discard, a.name)
    sim.label(f"backlog {n} ops, poison {kind}={value!r} at #{bad_at}")
    end(sim, sim.now + 8000)


@scenario("SCOUT-NETWORK", "The same replica syncs twice at once over overlapping batches, interleaved with pulls")
def s_net_double_sync_in_flight(sim):
    devs, span = base(sim, ops=(30, 60))
    sim.net.dup = max(sim.net.dup, 0.4)
    for _ in range(sim.rng.randint(2, 6)):
        sim.reload(devs[0], sim.rng.randint(0, span))          # reload while a request is in flight: busy is lost
    sim.label("reloads with requests in flight, dup>=0.4")
    end(sim, span)


@scenario("SCOUT-NETWORK", "A replica comes back after a late heal with a skewed, enormous Lamport value and a spoofed replica id")
def s_net_late_heal_skew_spoof(sim):
    devs, span = base(sim)
    z = sim.device("ph-z")
    sim.partition(z, 0, span)

    def hostile():
        tab = z.tab
        if tab is None:
            return
        tab.clock = js_number(2 ** 80 + sim.rng.randrange(1, 2 ** 20, 2))
        for _ in range(sim.rng.randint(2, 6)):
            k, item, v = sim.random_op()
            sim.expect_rejected(sim.tap(z, k, item, v, replica=sim.rng.choice(["ph-a", "ph-z"])))
    sim.at(sim.rng.randint(0, span), hostile)
    for _ in range(sim.rng.randint(1, 3)):                       # spoofed replica id with a sane clock: real data
        sim.at(sim.rng.randint(0, span // 2), lambda: sim.tap(devs[1], "note", "rice", "spoof", replica="ph-a"))
    sim.label("hostile ph-z at lamport ~2^80 (expected rejected) plus spoofed op.replica")
    end(sim, span)


# ============================================================================ SCOUT-IDS-CLOCKS

SEPARATORS = ["\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\r", "\n", "\t"]


@scenario("SCOUT-IDS-CLOCKS", "U+2028 line separator in a note silently drops the op on server restart, and the shifted cursor hides later ops")
def s_ids_line_separator_text(sim):
    devs, span = base(sim, noise=0.5)
    for _ in range(sim.rng.randint(2, 6)):
        sep = sim.rng.choice(SEPARATORS)
        dev = sim.rng.choice(devs)

        def tap(dev=dev, sep=sep):
            op = sim.tap(dev, "note", sim.rng.choice(ITEMS), f"line{sep}two")
            if op and ord(sep) < 32:
                sim.expect_rejected(op)                        # the server refuses control characters by design
        sim.at(sim.rng.randint(0, span), tap)
    for _ in range(sim.rng.randint(1, 3)):
        sim.crash_server(sim.rng.randint(0, span), torn=False)
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Lone surrogate in text poisons the server and the op is lost")
def s_ids_lone_surrogate_text(sim):
    devs, span = base(sim, noise=0.5)

    def tap():
        op = sim.tap(devs[0], "note", "rice", "bad " + sim.rng.choice(["\ud800", "\udfff", "\ud83c"]) + " text")
        if op:
            sim.expect_rejected(op)
    sim.at(sim.rng.randint(0, span // 2), tap)
    sim.crash_server(sim.rng.randint(span // 2, span), torn=False)
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Same id, different content: server keeps the first arrival while dedupe keeps the max")
def s_ids_same_id_two_contents(sim):
    devs, span = base(sim, noise=0.3)
    q = devs[0]
    sim.net.dup = 1.0                                          # the first copy is replayed by the network later
    first = sim.tap(q, "adjust", "rice", -500)
    sim.net.drop_next_response.add(q.name)
    sim.run(200)
    sim.net.dup = 0.1
    tab = q.tab
    if tab is not None:
        edited = dict(first, value=5)
        tab.ops = [edited if o["id"] == first["id"] else o for o in tab.ops]
        tab.synced.pop(first["id"], None)
        tab.persist()
        sim.supersede(first["id"], edited)
    sim.label("buggy client rewrites q:1 from -500 to 5 while a replay of -500 is in flight")
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Replica id collision: two phones both named ph-a reuse ids ph-a:1..n")
def s_ids_replica_name_collision(sim):
    devs = [sim.device(n, replica="ph-a") for n in ("phone-1", "phone-2")] + [sim.device("phone-3")]
    sim.noisy_network(0.5)
    span = 20_000
    sim.workload(devs, sim.rng.randint(20, 40), 0, span)
    sim.label("two devices share replica id ph-a")
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Enormous or skewed Lamport values: a permanent LWW winner and stuck outbox batches")
def s_ids_enormous_lamport(sim):
    devs, span = base(sim, noise=0.5)
    jump = sim.rng.choice([10 ** 40, 2 ** 53 + 1, 4_102_444_800_000, 10 ** 7])
    t = sim.rng.randint(0, span // 2)

    def corrupt():
        if devs[0].tab:
            devs[0].tab.clock = js_number(jump)
    sim.at(t, corrupt)

    def raw_client():                                          # a non-browser client sends a 5000-digit lamport
        body = ('{"replica":"raw","cursor":0,"ops":[{"id":"raw:1","replica":"raw","lamport":' + "9" * 5000 +
                ',"kind":"adjust","item":"rice","value":1}]}').encode()
        sim.net.request("raw", "POST", "/api/sync", body, lambda s, d: sim.note(f"raw client got {s}"))
    sim.at(sim.rng.randint(0, span), raw_client)
    sim.label(f"ph-a's stored Lamport clock corrupted to {jump}")
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Pydantic coercion makes the server and phone copies of the same op differ (lamport true/'7'/8.0)")
def s_ids_coerced_lamport_types(sim):
    devs, span = base(sim, noise=0.5)
    for _ in range(sim.rng.randint(1, 4)):
        lam = sim.rng.choice(["7", True, 8.0, 7.5, None, -1, 0])

        def tap(lam=lam):
            op = sim.tap(devs[0], "adjust", "rice", 3, lamport=lam)
            if op:
                sim.expect_rejected(op)
        sim.at(sim.rng.randint(0, span), tap)
    sim.label("legacy client sends non-integer lamports")
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Lamport ties and tombstone/restore races decided by string order, not causality")
def s_ids_lamport_ties_tombstones(sim):
    devs, span = base(sim, noise=0.5)
    for _ in range(sim.rng.randint(1, 4)):
        def race():
            tabs = [d.tab for d in devs[:2] if d.tab]
            if len(tabs) < 2:
                return
            lam = max(t.clock for t in tabs)
            for t in tabs:
                t.clock = lam                                  # both write at the same Lamport time
            sim.tap(devs[0], "delete", "tarpaulin", False)
            sim.tap(devs[1], "delete", "tarpaulin", True)
        sim.at(sim.rng.randint(0, span), race)

    def lamport_reset():
        if devs[2].tab:
            devs[2].tab.clock = 0                              # stored lamport lost, ops kept
    sim.at(sim.rng.randint(0, span), lamport_reset)
    sim.label("same-lamport delete/restore races and one Lamport reset")
    end(sim, span)


@scenario("SCOUT-IDS-CLOCKS", "Unicode and size edges in item ids and values: NFC/NFD split, empty text, huge signed quantities")
def s_ids_unicode_size_edges(sim):
    devs, span = base(sim, noise=0.5)
    cafe = "caf\u00e9"
    edges = [("adjust", cafe, 1, False), ("adjust", unicodedata.normalize("NFD", cafe), 1, False),
             ("adjust", "Rice", 2, False), ("adjust", "rice ", 2, False), ("note", "rice", "", False),
             ("create", "rice", "", True), ("adjust", "rice", 10 ** 30, True), ("adjust", "rice", -10 ** 9, False),
             ("adjust", "i" * 64, 1, False), ("adjust", "i" * 65, 1, True),
             ("note", "water", "\U0001F3D5" * 200, False), ("note", "water", "e\u0301" * 100, False),
             ("note", "water", "y" * 201, True)]
    for kind, item, value, invalid in sim.rng.sample(edges, sim.rng.randint(3, len(edges))):
        def tap(kind=kind, item=item, value=value, invalid=invalid):
            op = sim.tap(sim.rng.choice(devs), kind, item, value)
            if op and invalid:
                sim.expect_rejected(op)
        sim.at(sim.rng.randint(0, span), tap)
    end(sim, span)


# ============================================================================ SCOUT-APP-LIFECYCLE

@scenario("SCOUT-APP-LIFECYCLE", "Two tabs share one replica id and counter, so op ids collide")
def s_life_two_tabs_one_phone(sim):
    devs, span = base(sim, n_phones=2, noise=0.5)
    a = devs[0]
    sim.run(0)
    sim.at(1, lambda: sim.on_device(a, a.open_tab))             # a second tab on the same localStorage
    offline_from = sim.rng.randint(1000, span // 2)
    sim.partition(a, offline_from, span)
    for _ in range(sim.rng.randint(4, 12)):
        def tap_in(i):
            tabs = [t for t in a.tabs if t.alive]
            if len(tabs) > i:
                tab = tabs[i]
                sim.on_device(a, lambda: tab.add_op(*sim.random_op()), tab)
        sim.at(sim.rng.randint(offline_from, span), tap_in, sim.rng.randint(0, 1))

    def close_one():
        tabs = [t for t in a.tabs if t.alive]
        if len(tabs) > 1:
            victim = sim.rng.choice(tabs)
            victim.alive = False
            a.tabs.remove(victim)
            sim.note(f"{a.name}: one tab closed while offline")
    sim.at(sim.rng.randint(offline_from, span), close_one)
    sim.label("two tabs, offline, one closed")
    end(sim, span)


@scenario("SCOUT-APP-LIFECYCLE", "Reinstall or cleared site data reuses the replica id and resets counter and Lamport")
def s_life_reinstall_resets_identity(sim):
    devs, span = base(sim, noise=0.5)
    a = devs[0]
    keep = sim.rng.random() < 0.5
    t = sim.rng.randint(span // 3, span)
    if sim.rng.random() < 0.5:
        sim.partition(a, t - 3000, t)                          # some ops are still unsynced when it is wiped

    def reinstall():
        replica = a.tab.replica if (keep and a.tab) else None
        a.wipe(keep_replica=replica)
        sim.on_device(a, a.open_tab)
    sim.at(t, reinstall)
    sim.label("reinstall keeps replica id" if keep else "reinstall gets a new replica id")
    end(sim, span)


@scenario("SCOUT-APP-LIFECYCLE", "Base process killed mid-append leaves a torn line in ops.jsonl")
def s_life_kill_mid_append(sim):
    devs = phones(sim, 2)
    a, b = devs
    sim.tap(a, "adjust", "rice", 1)
    sim.run(500)
    sim.server.tear_next_append = True
    sim.tap(b, "adjust", "water", 2)
    sim.run(sim.rng.randint(4000, 9000))
    sim.tap(b, "adjust", "blankets", 3)
    sim.workload(devs, sim.rng.randint(0, 10), sim.now, sim.now + 5000)
    sim.at(sim.now + 6000, sim.server.crash, 1000)
    end(sim, sim.now + 8000)


@scenario("SCOUT-APP-LIFECYCLE", "Positional cursor goes stale after the server log shrinks or shifts")
def s_life_cursor_after_log_shrinks(sim):
    devs, span = base(sim, ops=(20, 50), noise=0.3)
    t = sim.rng.randint(span // 3, span)
    variant = sim.rng.choice(["wipe", "drop-middle"])

    def shrink():
        sim.server.crash()
        if variant == "wipe":
            sim.server.wipe()
        else:
            for _ in range(sim.rng.randint(1, 3)):
                n = len(sim.server.path.read_bytes().split(b"\n")) if sim.server.path.exists() else 0
                if n > 1:
                    sim.server.corrupt_line(sim.rng.randint(0, n - 2))
        sim.server.start()
    sim.at(t, shrink)
    sim.label(f"server log {variant}")
    end(sim, span)


@scenario("SCOUT-APP-LIFECYCLE", "App killed between saving the cursor and saving the received ops (or between sending and the ack)")
def s_life_kill_between_saves(sim):
    devs, span = base(sim)
    for _ in range(sim.rng.randint(2, 8)):
        dev = sim.rng.choice(devs)
        t = sim.rng.randint(0, span)
        if sim.rng.random() < 0.5:
            sim.at(t, setattr, dev, "kill_on_save", True)     # dies inside the next localStorage write
        else:
            sim.at(t, dev.close_tabs)                          # dies with a request in flight
        sim.reload(dev, t + sim.rng.randint(100, 4000))
    end(sim, span)


@scenario("SCOUT-APP-LIFECYCLE", "Storage quota reached mid-batch (QuotaExceededError / IDB AbortError)")
def s_life_storage_quota(sim):
    quota = sim.rng.randint(3_000, 40_000)
    devs = [sim.device("ph-a", quota=quota)] + phones(sim, 2)[1:]
    sim.noisy_network(0.5)
    span = 20_000
    a = devs[0]
    off = sim.rng.randint(0, span // 2)
    sim.partition(a, off, span)
    n = sim.rng.randint(40, 200 if sim.rng.random() < 0.8 else 700)
    sim.workload([a], n, 0, span)
    sim.workload(devs[1:], 15, 0, span)
    for _ in range(sim.rng.randint(1, 3)):
        sim.reload(a, sim.rng.randint(off, span))
    sim.label(f"quota {quota} chars, {n} taps, reloads while offline")
    end(sim, span)


@scenario("SCOUT-APP-LIFECYCLE", "Stale service worker runs the old client after an update, offline, and sends a batch the server rejects")
def s_life_stale_sw_bad_batch(sim):
    devs = phones(sim, 3)
    c = devs[2]
    sim.net.cut.add(c.name)
    bad = [("adjust", "rice", 2.5, {}), ("adjust", "rice", "5", {}), ("transfer", "rice", 5, {}),
           ("adjust", "rice", 1, {"id": "ph-c:" + "x" * 70}), ("adjust", "rice", 1, {"extra": 1})]
    picks = sim.rng.sample(bad, sim.rng.randint(1, 3))
    slots = set(sim.rng.sample(range(41), len(picks)))
    pi = iter(picks)
    for i in range(41):
        if i in slots:
            kind, item, value, over = next(pi)
            sim.expect_rejected(sim.tap(c, kind, item, value, **over))
        else:
            sim.tap(c, *sim.random_op())
        sim.now += 50
    sim.workload(devs[:2], 15, sim.now, sim.now + 5000)
    sim.at(sim.now + 2000, sim.net.cut.discard, c.name)
    sim.label("v1 client: " + ", ".join(f"{k}={v!r}" for k, _, v, _ in picks))
    end(sim, sim.now + 8000)


@scenario("SCOUT-APP-LIFECYCLE", "Phone clock jump feeds a huge Lamport value that breaks LWW and JS number precision")
def s_life_clock_jump_lamport(sim):
    devs, span = base(sim, noise=0.5)
    a = devs[0]
    jump = sim.rng.choice([4_102_444_800_000, 2 ** 53 + 1, 2 ** 53 - 1, 200_000])

    def go():
        a.skew_ms = 4_102_444_800_000 - WALL                  # the phone's wall clock reads 2099
        if a.tab:
            a.tab.clock = js_number(jump)                      # an older client seeded Lamport from Date.now()
            sim.tap(a, "note", "rice", "Hold")
    sim.at(sim.rng.randint(0, span // 2), go)
    sim.label(f"ph-a wall clock 2099, Lamport jumps to {jump}")
    end(sim, span)


WALL = 1_792_800_000_000


# ============================================================================ control

@scenario("CONTROL", "Network chaos only: drops, duplicates, reordering, partitions and clean crashes")
def s_control_network_chaos(sim):
    devs, span = base(sim, n_phones=sim.rng.randint(2, 5), ops=(20, 60))
    for _ in range(sim.rng.randint(0, 3)):
        sim.crash_server(sim.rng.randint(0, span), torn=False)
    end(sim, span)


def run_one(seed: int, name: str, trace: bool = False) -> dict:
    fn, _, _ = SCENARIOS[name]
    sim = Simulation(seed, name, trace=trace)
    try:
        fn(sim)
    except BaseException:
        sim.close()
        raise
    result = sim.finish()
    if trace:
        result["trace"] = sim.trace
    return result
