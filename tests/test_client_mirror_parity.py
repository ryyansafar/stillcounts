"""The chaos harness drives a Python mirror of client/app.js. These tests replay scripted sync traces through BOTH the
real app.js (node, fake DOM) and the mirror, and demand identical requests and identical stored records. If someone
changes the sync protocol in one copy and not the other, this fails.

What this does and does not prove: it covers the sync paths (first sync, offline queue, cursor and anchor, reset,
re-stamp, id reuse, rejections, HTTP errors). It does not cover rendering or the tab-to-tab storage events."""
import json
import shutil
import subprocess
from itertools import count
from pathlib import Path

import pytest

from chaos.sim import Simulation, b36, js_loads, text_of

ROOT = Path(__file__).resolve().parent.parent
LABELS = {"rice": "Rice (kg)", "water": "Drinking water (L)"}
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def op(id, lamport, item="rice", value=1, replica="ph-z"):
    return {"id": id, "replica": replica, "lamport": lamport, "kind": "adjust", "item": item, "value": value}


def ok(cursor, ops=(), epoch="e1", rejected=(), reset=False, newest=0):
    return {"reply": {"accepted": 0, "rejected": list(rejected), "cursor": cursor, "epoch": epoch, "ops": list(ops),
                      "reset": reset, "max_lamport": newest}}


TRACES = {
    "offline_queue_then_merge": [
        {"reply": None}, {"tap": ["rice", 1]}, {"reply": None}, {"tap": ["water", -10]}, {"reply": None},
        {"tap": ["rice", 10]}, ok(3, [op("ph-t:1i", 1, value=1, replica="ph-t"), op("ph-z:1", 4, "water", 7)] , newest=4),
        {"tick": 1}, ok(2),
    ],
    "base_log_was_replaced": [
        {"reply": None}, {"tap": ["rice", 1]}, ok(1, [op("ph-t:1i", 1, replica="ph-t")]),
        {"tick": 1}, ok(0, [], epoch="e2", reset=True),
        {"tick": 1}, ok(1, [op("ph-t:1i", 1, replica="ph-t")], epoch="e2"),
    ],
    "lamport_refused_gets_restamped": [
        {"reply": None}, {"tap": ["rice", 1]},
        ok(5, [op("ph-z:1", 40)], rejected=[{"id": "ph-t:1i", "reason": "lamport skew too large"}], newest=40),
        {"tick": 1}, ok(6, [op("ph-t:1i", 41, replica="ph-t")], newest=41),
    ],
    "id_reused_with_other_content_gets_a_new_id": [
        {"reply": None}, {"tap": ["rice", 10]},
        ok(1, [op("ph-t:1i", 1, value=5, replica="ph-t")],
           rejected=[{"id": "ph-t:1i", "reason": "id reused with different content"}], newest=1),
        {"tick": 1}, ok(2, [op("ph-t:1i", 1, value=5, replica="ph-t"), op("ph-t:2i", 1, value=10, replica="ph-t")], newest=1),
    ],
    "other_rejection_is_counted_not_retried": [
        {"reply": None}, {"tap": ["water", 1]},
        ok(0, rejected=[{"id": "ph-t:1i", "reason": "adjust magnitude too large"}]), {"tick": 1}, ok(0),
    ],
    "http_error_then_recovery": [
        {"reply": None}, {"tap": ["rice", -1]}, {"reply": {}, "status": 500}, {"tick": 1},
        ok(1, [op("ph-t:1i", 1, value=-1, replica="ph-t")]),
    ],
}


def run_node(trace: list, tmp_path: Path) -> dict:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps({"replica": "ph-t", "labels": LABELS, "steps": trace}))
    out = subprocess.run(["node", str(ROOT / "client" / "tools" / "trace_runner.js"), str(path)],
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def run_mirror(trace: list) -> dict:
    sim = Simulation(0, "parity")
    pending, requests = [], []

    def capture(src, method, path, body, on_done):
        requests.append(json.loads(body))
        pending.append(on_done)
    sim.net.request = capture
    dev = sim.device("ph-t", replica="ph-t")
    phone, counter = dev.tab, count(1)
    phone.uid = lambda: b36(next(counter)) + "i"
    for step in trace:
        if "tap" in step:
            sim.on_device(dev, lambda s=step: phone.add_op("adjust", s["tap"][0], s["tap"][1]), phone)
        elif "tick" in step:
            sim.on_device(dev, phone.sync, phone)
        else:
            done = pending.pop(0)
            if step["reply"] is None:
                done(None, b"")
            else:
                done(step.get("status", 200), json.dumps(step["reply"]).encode())
    state = js_loads(text_of(dev.storage["sc_state"]))
    state["synced"] = sorted(state["synced"])
    return {"requests": requests, "state": state}


@pytest.mark.parametrize("name", sorted(TRACES))
def test_the_mirror_and_the_shipped_client_agree(name, tmp_path):
    real, mirror = run_node(TRACES[name], tmp_path), run_mirror(TRACES[name])
    assert mirror["requests"] == real["requests"]
    assert mirror["state"] == real["state"]
