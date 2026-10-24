// Replays a scripted sync trace through the REAL client/app.js (fake DOM, scripted network) and prints what it sent and
// what it stored. tests/test_client_mirror_parity.py runs the same trace through the Python mirror in chaos/sim.py and
// requires identical output, so the two copies of the sync protocol cannot drift apart unnoticed.
//   node client/tools/trace_runner.js trace.json
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CRDT = fs.readFileSync(path.join(__dirname, "..", "crdt.js"), "utf8");
const APP = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

class El {
  constructor(doc, tag) { this.doc = doc; this.children = []; this.attrs = {}; this.listeners = {}; this.className = ""; this._text = ""; this.value = ""; }
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(v) { this.children = []; this._text = String(v); }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  focus() {}
  setSelectionRange() {}
}

function run(trace) {
  const doc = { activeElement: null, createElement: (t) => new El(doc, t) };
  const byId = {};
  doc.getElementById = (id) => (byId[id] = byId[id] || new El(doc, "div"));
  const data = { sc_replica: JSON.stringify(trace.replica) };
  const pending = [], requests = [];
  let ticks = [], clockN = 0;
  const RealDate = Date;
  const FakeDate = function () { return new RealDate(); };
  FakeDate.now = () => ++clockN;                    // op ids become "<replica>:<base36 counter>i"
  const FakeMath = Object.create(Math);
  FakeMath.random = () => 0.5;
  const win = { addEventListener() {} };
  const ctx = {
    window: win, document: doc, navigator: {}, console, Date: FakeDate, Math: FakeMath,
    localStorage: { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } },
    setInterval: (fn) => { ticks.push(fn); return ticks.length; },
    fetch: (url, init) => new Promise((resolve, reject) => { requests.push(JSON.parse(init.body)); pending.push({ resolve, reject }); }),
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(CRDT, ctx);
  win.StillCounts = ctx.StillCounts;
  vm.runInContext(APP, ctx);
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
  const labels = { "-10": "−10", "-1": "−1", "1": "+1", "10": "+10" };
  function press(item, value) {
    const hit = (e) => e.getAttribute && e.getAttribute("aria-label") && e.getAttribute("aria-label").endsWith(" " + labels[String(value)]);
    const cards = [];
    (function walk(e) { if (e.className === "card") cards.push(e); e.children.forEach(walk); })(byId.list);
    for (const card of cards) {
      const find = (e) => (hit(e) ? e : e.children.map(find).find(Boolean));
      const key = card.children[0].children[0].textContent;       // the card's visible name
      if (key === trace.labels[item]) { find(card).listeners.click[0](); return; }
    }
    throw new Error("no card for " + item);
  }
  return (async () => {
    await settle();
    for (const step of trace.steps) {
      if (step.tap) press(step.tap[0], step.tap[1]);
      else if (step.tick) ticks.forEach((fn) => fn());
      else if ("reply" in step) {
        const p = pending.shift();
        if (step.reply === null) p.reject(new Error("offline"));
        else p.resolve({ ok: (step.status || 200) < 400, status: step.status || 200, json: () => Promise.resolve(step.reply) });
      }
      await settle();
    }
    const state = JSON.parse(data.sc_state);
    state.synced.sort();
    return { requests, state };
  })();
}

run(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then((out) => process.stdout.write(JSON.stringify(out)));
