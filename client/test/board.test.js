// Runs client/board.js against a tiny fake DOM and canned API replies.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const BOARD = fs.readFileSync(path.join(__dirname, "..", "board.js"), "utf8");

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ""; this._text = ""; this.listeners = {}; this.attrs = {}; }
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(v) { this.children = []; this._text = String(v); }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, fn) { this.listeners[t] = fn; }
}

async function boot(replies) {
  const els = {};
  const doc = {
    getElementById: (id) => (els[id] = els[id] || new El("div")),
    createElement: (t) => new El(t),
    createTextNode: (t) => { const e = new El("#text"); e._text = t; return e; },
  };
  const ctx = {
    document: doc, location: { origin: "http://x" }, console,
    setInterval: () => 0,
    fetch: (url) => {
      const key = Object.keys(replies).find((k) => url.indexOf(k) === 0);
      return key ? Promise.resolve({ json: () => Promise.resolve(replies[key]) }) : Promise.reject(new Error("offline"));
    },
    encodeURIComponent,
  };
  vm.createContext(ctx);
  vm.runInContext(BOARD, ctx);
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
  return els;
}

const state = (extra) => Object.assign({
  state: { rice: { name: "Rice", qty: 5, note: "", alive: true } },
  stats: { ops: 3, conflicts: 0, oversold: [] }, replicas: ["ph-a", "ph-b"], activity: [], phones: [],
}, extra);

test("with no per-run file the Attack tab shows the committed summary, one row per scenario, failures first", async () => {
  const summary = { seeds: [0, 500], runs: 12500, failed: 10, lost_ops: 99,
    by_scenario: { good: { runs: 500, failed: 0, lost: 0 }, bad: { runs: 500, failed: 10, lost: 99 } } };
  const els = await boot({ "/api/state": state(), "/api/chaos": { runs: [], summary } });
  assert.match(els.runs.children[0].textContent, /^OPEN bad .* 10 of 500 runs failed/);
  assert.match(els.runs.children[1].textContent, /^PASS good/);
  assert.match(els["attack-head"].textContent, /1 of 2 scenarios still fail/);
  assert.match(els["attack-note"].textContent, /simulated phones, not the phones in this room/i);
  assert.match(els["harness-line"].textContent, /10 of 12500 runs failed/);
});

test("the headline comes from what phones report, not from the harness", async () => {
  const caught = { replica: "ph-a", held: 3, queued: 0, age_s: 1, caught_up: true };
  let els = await boot({ "/api/state": state({ phones: [caught, Object.assign({}, caught, { replica: "ph-b" })] }), "/api/chaos": { runs: [] } });
  assert.strictEqual(els.headline.textContent, "Every phone holds every operation");
  assert.strictEqual(els["s-caught"].textContent, "2 / 2");
  els = await boot({ "/api/state": state({ phones: [caught, { replica: "ph-b", held: 1, queued: 2, age_s: 1, caught_up: false }] }), "/api/chaos": { runs: [] } });
  assert.strictEqual(els.headline.textContent, "1 of 2 phone(s) still catching up");
  assert.strictEqual(els["s-caught"].textContent, "1 / 2");
});

test("an oversold item is the headline, and a harness number is never shown as a live loss figure", async () => {
  const s = state({ stats: { ops: 3, conflicts: 0, oversold: ["rice"] } });
  const els = await boot({ "/api/state": s, "/api/chaos": { runs: [{ seed: 1, scenario: "x", ok: false, ops: 5, lost: 2, conflicts: 0 }] } });
  assert.strictEqual(els.headline.textContent, "1 item(s) need a human");
  assert.match(els["harness-line"].textContent, /simulated, not these phones/);
});
