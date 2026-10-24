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

test("the Attack tab shows plain-language titles grouped by area, failures first, and counts the control separately", async () => {
  const summary = { seeds: [0, 500], runs: 12500, failed: 10, lost_ops: 99,
    by_scenario: { good: { runs: 500, failed: 0, lost: 0 }, bad: { runs: 500, failed: 10, lost: 99 },
                   other_good: { runs: 500, failed: 0, lost: 0 }, ctl: { runs: 500, failed: 3, lost: 0 } } };
  const titles = { areas: { network: "Network and server", control: "Control" },
    scenarios: { good: { area: "network", title: "Good one" }, bad: { area: "network", title: "Bad one" },
                 other_good: { area: "network", title: "Another" }, ctl: { area: "control", title: "Control row" } } };
  const els = await boot({ "/api/state": state(), "/api/chaos": { runs: [], summary, titles } });
  const lines = els.runs.children.map((c) => c.textContent);
  assert.deepStrictEqual(lines[0], "Network and server");
  assert.match(lines[1], /^OPEN Bad one · 10 of 500 runs failed, 99 ops lost/);
  assert.match(lines[2], /^PASS (Good one|Another)$/);
  assert.ok(lines.indexOf("Control") > 3, "the control group comes after");
  assert.ok(!els.runs.textContent.includes("net_") && !els.runs.textContent.includes("bad ·"), "no raw ids on screen");
  assert.strictEqual(els["attack-head"].textContent, "1 scenario of 3 still fails");
  assert.match(els["attack-note"].textContent, /simulated phones, not the phones in this room/i);
  assert.match(els["attack-note"].textContent, /control row is not counted/i);
});

test("the Attack headline pluralises and says so when everything passes", async () => {
  const mk = (failed) => ({ seeds: [0, 5], runs: 10, failed: 0, lost_ops: 0, by_scenario: { a: { runs: 5, failed, lost: 0 }, b: { runs: 5, failed, lost: 0 } } });
  const t = { areas: {}, scenarios: { a: { area: "x", title: "A" }, b: { area: "x", title: "B" } } };
  let els = await boot({ "/api/state": state(), "/api/chaos": { runs: [], summary: mk(2), titles: t } });
  assert.strictEqual(els["attack-head"].textContent, "2 scenarios of 2 still fail");
  els = await boot({ "/api/state": state(), "/api/chaos": { runs: [], summary: mk(0), titles: t } });
  assert.strictEqual(els["attack-head"].textContent, "All 2 scenarios pass");
});

test("the headline comes from what phones report, not from the harness", async () => {
  const caught = { replica: "ph-a", held: 3, queued: 0, age_s: 1, caught_up: true };
  let els = await boot({ "/api/state": state({ phones: [caught, Object.assign({}, caught, { replica: "ph-b" })] }), "/api/chaos": { runs: [] } });
  assert.strictEqual(els.headline.textContent, "Every phone holds every operation");
  assert.strictEqual(els["s-caught"].textContent, "2 / 2");
  els = await boot({ "/api/state": state({ phones: [caught, { replica: "ph-b", held: 1, queued: 2, age_s: 1, caught_up: false }] }), "/api/chaos": { runs: [] } });
  assert.strictEqual(els.headline.textContent, "1 of 2 phones still catching up");
  assert.strictEqual(els["s-caught"].textContent, "1 / 2");
});

test("an oversold item is the headline, grammatically, and it sorts to the top of the ledger", async () => {
  const st = state({ state: { rice: { name: "Rice", qty: 5, note: "", alive: true }, water: { name: "Water", qty: -3, note: "", alive: true } },
    stats: { ops: 3, conflicts: 0, oversold: ["water"] } });
  let els = await boot({ "/api/state": st, "/api/chaos": { runs: [] } });
  assert.strictEqual(els.headline.textContent, "1 item needs a human");
  assert.strictEqual(els.headline.className, "big alert", "the board itself flashes: no internet service needed");
  assert.match(els.rows.children[0].textContent, /^Water/);
  st.stats.oversold = ["water", "rice"];
  els = await boot({ "/api/state": st, "/api/chaos": { runs: [] } });
  assert.strictEqual(els.headline.textContent, "2 items need a human");
});

test("the Proof tab shows both the curated 14 and the real mutmut numbers", async () => {
  const els = await boot({ "/api/state": state(), "/api/chaos": { runs: [] }, "/api/referee": { killed: 14, total: 14, mutmut: { killed: 163, total: 166, survived: 3 } },
    "/api/devin": { sessions: 9, prs: 5, issues: 5 } });
  assert.strictEqual(els["p-referee"].textContent, "14 / 14");
  assert.strictEqual(els["p-mutmut"].textContent, "163 / 166");
  assert.match(els["p-mutmut-sub"].textContent, /3 survivors are equivalent/);
  assert.match(els["p-devin-sub"].textContent, /5 pull requests merged/);
});
