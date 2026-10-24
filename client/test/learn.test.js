const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../crdt.js");
const L = require("../learn.js");

const CLIENT = path.join(__dirname, "..");
const read = (f) => fs.readFileSync(path.join(CLIENT, f), "utf8");

// ---- a tiny DOM, just enough for learn.js to render into ----
class Node {
  constructor(tag, text) {
    this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ""; this._text = text === undefined ? "" : String(text); this.value = ""; this.disabled = false; this.type = "";
  }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t) { (this.listeners[t] || []).forEach((f) => f({ type: t, target: this, preventDefault() {} })); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  focus() {}
}
const doc = { createElement: (t) => new Node(t), createTextNode: (t) => new Node("#text", t) };
function all(root, pred, out = []) { if (pred(root)) out.push(root); root.children.forEach((c) => all(c, pred, out)); return out; }
const byAttr = (root, k, v) => all(root, (n) => n.getAttribute(k) === v);
const one = (root, k, v) => { const r = byAttr(root, k, v); assert.ok(r.length, "missing [" + k + "=" + v + "]"); return r[0]; };

function memoryStorage() {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, dump: m };
}

// ---- a fake Learn API with the same shape the server exposes ----
const OPS = [
  { id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "blankets", value: 10 },
  { id: "b:1", replica: "b", lamport: 1, kind: "adjust", item: "blankets", value: -3 },
  { id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "blankets", value: 10 },
  { id: "b:2", replica: "b", lamport: 2, kind: "adjust", item: "blankets", value: 5 },
];
const REAL_CODE = "def merge(ops):\n    ...";

function fakeServer() {
  const passed = {};
  const calls = [];
  let offline = false;
  const stage = (id, title, mutant) => ({
    id, title,
    earned: ["predict", "bug", "write", "explain"].every((k) => passed[id + "/" + k]),
    checkpoints: {
      predict: { ops: OPS, item: "blankets", question: "How many blankets after the merge?", passed: !!passed[id + "/predict"] },
      bug: { mutant: { code: "sum(abs(o.value) for o in group)" }, choices: ["counts magnitudes", "ignores replica"], passed: !!passed[id + "/bug"] },
      write: { prompt: "Write quantity(group)", starter: "def quantity(group):\n    pass\n", passed: !!passed[id + "/write"] },
      explain: { prompt: "Why sum signed values?", passed: !!passed[id + "/explain"] },
    },
  });
  async function fetchStub(url, init) {
    calls.push({ url, method: (init && init.method) || "GET", body: init && init.body ? JSON.parse(init.body) : null });
    if (offline) throw new TypeError("Failed to fetch");
    const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
    if (url === "/api/learn/stages") {
      const stages = [stage("quantity", "Quantities add up"), stage("lww", "Last writer wins")];
      stages.forEach((s) => { if (s.earned) s.code = REAL_CODE; });
      return json({ stages });
    }
    const m = url.match(/^\/api\/learn\/([^/]+)\/(predict|bug|write|explain)$/);
    if (!m) return json({ detail: "not found" }, 404);
    const [, id, kind] = m; const body = JSON.parse(init.body);
    let ok;
    if (kind === "predict") ok = body.guess === C.merge(OPS).blankets.qty;
    if (kind === "bug") ok = body.answer === "counts magnitudes";
    if (kind === "write") ok = /sum\(/.test(body.code);
    if (kind === "explain") ok = body.text.trim().length > 0;
    if (ok) passed[id + "/" + kind] = true;
    const out = { passed: ok };
    if (kind === "predict") out.answer = C.merge(OPS).blankets.qty;
    if (kind === "write" && !ok) out.failure = "merged state differs from the reference on 'basic'";
    if (kind === "explain") out.rubric = ["mentions signed adjustments", "mentions order does not matter"];
    if (["predict", "bug", "write", "explain"].every((k) => passed[id + "/" + k])) out.code = REAL_CODE;
    return json(out);
  }
  return { fetch: fetchStub, calls, passed, setOffline: (v) => { offline = v; } };
}

async function mounted(server, storage) {
  const root = doc.createElement("div");
  const app = L.mount(root, { doc, fetch: server.fetch, storage: storage || memoryStorage() });
  await app.ready;
  return { root, app };
}

// ---- pure logic ----
test("predict answer is computed in the browser with the real merge", () => {
  assert.strictEqual(L.predictAnswer({ ops: OPS, item: "blankets" }), 12);
  assert.strictEqual(L.predictAnswer({ ops: OPS, item: "rice" }), 0);
  assert.strictEqual(L.gradePredict({ ops: OPS, item: "blankets" }, "12"), true);
  assert.strictEqual(L.gradePredict({ ops: OPS, item: "blankets" }, " 15 "), false);
  assert.strictEqual(L.gradePredict({ ops: OPS, item: "blankets" }, "twelve"), null);
});

test("stages are normalised from either an array or {stages}", () => {
  const a = L.normalizeStages([{ id: "x", checkpoints: [{ kind: "predict", ops: [], item: "i" }] }]);
  const b = L.normalizeStages({ stages: [{ id: "x", checkpoints: { predict: { ops: [], item: "i" } } }] });
  assert.deepStrictEqual(a, b);
  assert.deepStrictEqual(a[0].order, ["predict"]);
  assert.strictEqual(a[0].title, "x");
  assert.deepStrictEqual(L.normalizeStages(null), []);
});

test("progress and locks follow earned checkpoints in order", () => {
  const stages = L.normalizeStages({ stages: [
    { id: "s1", checkpoints: { predict: {}, bug: {}, write: {}, explain: {} } },
    { id: "s2", checkpoints: { predict: {}, bug: {} } },
  ] });
  let passed = {};
  assert.deepStrictEqual(L.progress(stages, passed), { done: 0, total: 6, pct: 0 });
  assert.strictEqual(L.isStageLocked(stages, 1, passed), true);
  assert.strictEqual(L.isCheckpointLocked(stages[0], "bug", passed), true);
  passed = { "s1/predict": true };
  assert.strictEqual(L.isCheckpointLocked(stages[0], "bug", passed), false);
  assert.strictEqual(L.isCheckpointLocked(stages[0], "write", passed), true);
  passed = { "s1/predict": true, "s1/bug": true, "s1/write": true, "s1/explain": true };
  assert.strictEqual(L.isEarned(stages[0], passed), true);
  assert.strictEqual(L.isStageLocked(stages, 1, passed), false);
  assert.deepStrictEqual(L.progress(stages, passed), { done: 4, total: 6, pct: 67 });
});

// ---- UI against the stubbed API ----
test("renders the stage list with locks and a progress bar", async () => {
  const server = fakeServer();
  const { root } = await mounted(server);
  assert.strictEqual(server.calls[0].url, "/api/learn/stages");
  const items = byAttr(root, "data-stage", "quantity").concat(byAttr(root, "data-stage", "lww"));
  assert.strictEqual(items.length, 2);
  assert.strictEqual(one(root, "data-stage", "lww").getAttribute("data-locked"), "true");
  assert.strictEqual(one(root, "data-stage", "quantity").getAttribute("data-locked"), "false");
  const bar = one(root, "role", "progressbar");
  assert.strictEqual(bar.getAttribute("aria-valuenow"), "0");
  assert.strictEqual(bar.getAttribute("aria-valuemax"), "100");
  assert.ok(!byAttr(root, "data-action", "reveal").length, "code must stay locked");
});

test("earning every checkpoint unlocks the next stage and reveals the real code", async () => {
  const server = fakeServer();
  const { root, app } = await mounted(server);

  one(root, "data-input", "predict").value = "9";
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "predict").textContent, /12/);
  assert.ok(!server.passed["quantity/predict"]);

  one(root, "data-input", "predict").value = "12";
  one(root, "data-action", "predict").fire("click"); await app.idle();
  const p = server.calls.filter((c) => c.url === "/api/learn/quantity/predict").pop();
  assert.deepStrictEqual(p.body, { guess: 12 });
  assert.strictEqual(one(root, "role", "progressbar").getAttribute("aria-valuenow"), "13");

  one(root, "data-choice", "ignores replica").fire("click"); await app.idle();
  assert.ok(!server.passed["quantity/bug"]);
  one(root, "data-choice", "counts magnitudes").fire("click"); await app.idle();
  assert.ok(server.passed["quantity/bug"]);

  const ta = one(root, "data-input", "write");
  assert.match(ta.value, /def quantity/);
  ta.value = "def quantity(group):\n    return 0\n";
  one(root, "data-action", "write").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "write").textContent, /differs from the reference/);
  ta.value = "def quantity(group):\n    return sum(o.value for o in group)\n";
  one(root, "data-action", "write").fire("click"); await app.idle();
  assert.deepStrictEqual(server.calls.filter((c) => c.url === "/api/learn/quantity/write").pop().body.code, ta.value);

  assert.ok(!byAttr(root, "data-rubric", "explain").length, "rubric stays hidden until the learner answers");
  one(root, "data-input", "explain").value = "Adjustments are signed so issuing subtracts.";
  one(root, "data-action", "explain").fire("click"); await app.idle();
  assert.match(one(root, "data-rubric", "explain").textContent, /signed adjustments/);

  assert.strictEqual(one(root, "data-stage", "lww").getAttribute("data-locked"), "false");
  one(root, "data-action", "reveal").fire("click"); await app.idle();
  assert.strictEqual(one(root, "data-code", "quantity").textContent, REAL_CODE);
});

test("works offline: stages come from the cache and predict is graded locally, then synced", async () => {
  const storage = memoryStorage();
  const server = fakeServer();
  await mounted(server, storage);              // first visit online fills the cache
  server.setOffline(true);
  const { root, app } = await mounted(server, storage);
  assert.ok(byAttr(root, "data-stage", "quantity").length, "stages render from the cache");
  assert.match(one(root, "data-status", "net").textContent, /offline/i);

  one(root, "data-input", "predict").value = "12";
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "predict").textContent, /12/);
  assert.strictEqual(one(root, "role", "progressbar").getAttribute("aria-valuenow"), "13");
  assert.ok(!server.passed["quantity/predict"]);

  one(root, "data-action", "write").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "write").textContent, /earn the previous checkpoint/i);
  one(root, "data-choice", "counts magnitudes").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "bug").textContent, /signal/i);
  assert.ok(!server.passed["quantity/bug"], "the bug checkpoint is only earned when the server grades it");

  server.setOffline(false);
  await app.flush();
  assert.ok(server.passed["quantity/predict"], "queued predict reaches the server when the signal returns");
});

test("first visit with no signal and no cache explains what to do", async () => {
  const server = fakeServer(); server.setOffline(true);
  const { root } = await mounted(server);
  assert.match(one(root, "data-status", "empty").textContent, /connect once/i);
});

// ---- wiring ----
test("learn files are in the offline cache and the cache name was bumped", () => {
  const sw = read("sw.js");
  for (const f of ["/static/learn.html", "/static/learn.js", "/static/learn.css"]) assert.ok(sw.includes('"' + f + '"'), f);
  const name = sw.match(/var CACHE = "stillcounts-v(\d+)"/);
  assert.ok(name && Number(name[1]) > 3);
});

test("learn page loads crdt.js before learn.js and is linked from the phone app and the board", () => {
  const html = read("learn.html");
  assert.ok(html.indexOf("/static/crdt.js") > 0 && html.indexOf("/static/crdt.js") < html.indexOf("/static/learn.js"));
  assert.ok(html.includes("/static/style.css") && html.includes("/static/learn.css"));
  assert.ok(read("index.html").includes('href="/static/learn.html"'));
  assert.ok(read("board.html").includes('href="/static/learn.html"'));
});
