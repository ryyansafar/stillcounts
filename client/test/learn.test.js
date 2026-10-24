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

// ---- a fake Learn API with the same shape server/learn_api.py exposes ----
const OPS = [
  { id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "blankets", value: 10 },
  { id: "b:1", replica: "b", lamport: 1, kind: "adjust", item: "blankets", value: -3 },
  { id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "blankets", value: 10 },
  { id: "b:2", replica: "b", lamport: 2, kind: "adjust", item: "blankets", value: 5 },
];
const OPS2 = [
  { id: "a:2", replica: "a", lamport: 1, kind: "adjust", item: "rice", value: -4 },
  { id: "b:3", replica: "b", lamport: 1, kind: "create", item: "rice", value: "Rice A" },
  { id: "a:3", replica: "a", lamport: 2, kind: "create", item: "rice", value: "Rice B" },
];
const CASES = [
  { id: "p1", prompt: "Retries happen.", question: "How many blankets after the merge?", ops: OPS, item: "blankets", field: "qty" },
  { id: "p2", prompt: "Two names.", ops: OPS2, item: "rice", field: "name" },
  { id: "p3", prompt: "Is it oversold?", ops: OPS2, item: "rice", field: "oversold" },
];
const REAL_CODE = "def merge(ops):\n    ...";
const LEARNER = /^[A-Za-z0-9_-]{1,64}$/;

function fakeServer() {
  const passed = {};
  const calls = [];
  let offline = false;
  const progressOf = (id) => ({ predict: !!passed[id + "/predict"], bug: !!passed[id + "/bug"],
                               write: !!passed[id + "/write"], explain: !!passed[id + "/explain"] });
  const unlocked = (id) => passed[id + "/predict"] && passed[id + "/bug"] && passed[id + "/write"];
  const stage = (id, title, order) => ({
    id, order, title, summary: "A lesson.", unlocked: !!unlocked(id), progress: progressOf(id),
    code: unlocked(id) ? REAL_CODE : null,
    predict: { prompt: "Predict the merged result.", cases: CASES },
    bug: { prompt: "What is wrong?", snippet: "sum(abs(o.value) for o in group)",
           options: [{ id: "a", text: "counts magnitudes" }, { id: "b", text: "ignores replica" }] },
    write: { function: "quantity", prompt: "Write quantity(group)", stub: "def quantity(group):\n    pass\n" },
    explain: { prompt: "Why sum signed values?", min_points: 1 },
  });
  async function fetchStub(url, init) {
    const headers = (init && init.headers) || {};
    calls.push({ url, method: (init && init.method) || "GET", headers, body: init && init.body ? JSON.parse(init.body) : null });
    if (offline) throw new TypeError("Failed to fetch");
    const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
    if (!LEARNER.test(headers["X-Learner-Id"] || "")) return json({ detail: "learner id required" }, 422);
    if (url === "/api/learn/stages") return json({ stages: [stage("quantity", "Quantities add up", 1), stage("lww", "Last writer wins", 2)] });
    const m = url.match(/^\/api\/learn\/([^/]+)\/(predict|bug|write|explain)$/);
    if (!m) return json({ detail: "not found" }, 404);
    const [, id, kind] = m; const body = JSON.parse(init.body);
    let ok, result = {};
    if (kind === "predict") ok = body.answer.p1 === 12 && body.answer.p2 === "Rice B" && body.answer.p3 === true;
    if (kind === "bug") ok = body.answer === "a";
    if (kind === "write") {
      ok = /sum\(/.test(body.source);
      if (!ok) result = { passed: false, error: null, counterexample: { ops: OPS, expected: 12, got: 0 } };
    }
    if (kind === "explain") {
      ok = body.text.trim().length > 0;
      result = { passed: ok, covered: [{ id: "signed", point: "mentions signed adjustments" }], missed: [{ id: "order", point: "mentions order does not matter" }] };
    }
    if (ok || kind === "explain") passed[id + "/" + kind] = true;
    return json({ stage: id, checkpoint: kind, passed: !!ok, result: Object.assign({ passed: !!ok }, result),
                  progress: progressOf(id), unlocked: !!unlocked(id), code: unlocked(id) ? REAL_CODE : null });
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
test("predict answers are computed in the browser with the real merge", () => {
  const [p1, p2, p3] = CASES;
  assert.strictEqual(L.predictAnswer(p1), 12);
  assert.strictEqual(L.predictAnswer(p2), "Rice B");
  assert.strictEqual(L.predictAnswer(p3), true);
  assert.strictEqual(L.predictAnswer({ ops: OPS, item: "rice" }), 0);
  assert.strictEqual(L.gradePredict(p1, "12"), true);
  assert.strictEqual(L.gradePredict(p1, " 15 "), false);
  assert.strictEqual(L.gradePredict(p1, "twelve"), null);
  assert.strictEqual(L.gradePredict(p2, "Rice B"), true);
  assert.strictEqual(L.gradePredict(p2, ""), null);
  assert.strictEqual(L.gradePredict(p3, "yes"), true);
  assert.strictEqual(L.gradePredict(p3, "no"), false);
  assert.strictEqual(L.gradePredict(p3, "banana"), null);
});

test("stages are normalised from either an array or {stages}", () => {
  const a = L.normalizeStages([{ id: "x", checkpoints: [{ kind: "predict", cases: [] }] }]);
  const b = L.normalizeStages({ stages: [{ id: "x", checkpoints: { predict: { cases: [] } } }] });
  assert.deepStrictEqual(a, b);
  assert.deepStrictEqual(a[0].order, ["predict"]);
  assert.strictEqual(a[0].title, "x");
  assert.deepStrictEqual(L.normalizeStages(null), []);
});

test("the server's stage shape maps to earned and per-checkpoint progress", () => {
  const [s] = L.normalizeStages({ stages: [{ id: "x", unlocked: false, progress: { predict: true, bug: false },
    predict: { cases: [] }, bug: { options: [] }, write: {}, explain: {} }] });
  assert.deepStrictEqual(s.order, ["predict", "bug", "write", "explain"]);
  assert.strictEqual(s.earned, false);
  assert.strictEqual(s.checkpoints.predict.passed, true);
  assert.ok(!s.checkpoints.bug.passed);
  assert.strictEqual(L.normalizeStages([{ id: "y", unlocked: true, predict: {} }])[0].earned, true);
});

test("progress and locks follow earned checkpoints in order; explain is practice", () => {
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
  passed = { "s1/predict": true, "s1/bug": true, "s1/write": true };
  assert.strictEqual(L.isEarned(stages[0], passed), true, "predict, bug and write earn the stage, as on the server");
  assert.strictEqual(L.isStageLocked(stages, 1, passed), false);
  assert.deepStrictEqual(L.progress(stages, passed), { done: 3, total: 6, pct: 50 });
});

// ---- UI against the stubbed API ----
test("renders the stage list with locks and a progress bar", async () => {
  const server = fakeServer();
  const { root } = await mounted(server);
  assert.strictEqual(server.calls[0].url, "/api/learn/stages");
  assert.match(server.calls[0].headers["X-Learner-Id"], LEARNER, "every request names the learner");
  const items = byAttr(root, "data-stage", "quantity").concat(byAttr(root, "data-stage", "lww"));
  assert.strictEqual(items.length, 2);
  assert.strictEqual(one(root, "data-stage", "lww").getAttribute("data-locked"), "true");
  assert.strictEqual(one(root, "data-stage", "quantity").getAttribute("data-locked"), "false");
  const bar = one(root, "role", "progressbar");
  assert.strictEqual(bar.getAttribute("aria-valuenow"), "0");
  assert.strictEqual(bar.getAttribute("aria-valuemax"), "100");
  assert.ok(!byAttr(root, "data-action", "reveal").length, "code must stay locked");
  assert.strictEqual(byAttr(root, "data-case", "p1").length + byAttr(root, "data-case", "p2").length + byAttr(root, "data-case", "p3").length, 3);
});

test("the learner id is created once and kept", async () => {
  const storage = memoryStorage(), server = fakeServer();
  await mounted(server, storage);
  await mounted(server, storage);
  const ids = new Set(server.calls.map((c) => c.headers["X-Learner-Id"]));
  assert.strictEqual(ids.size, 1);
});

function answerAll(root, values) {
  Object.keys(values).forEach((id) => { one(root, "data-input", "predict:" + id).value = values[id]; });
}

test("earning predict, bug and write unlocks the next stage and reveals the real code", async () => {
  const server = fakeServer();
  const { root, app } = await mounted(server);

  answerAll(root, { p1: "9", p2: "Rice B", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "predict").textContent, /p1: the real merge gives 12/);
  assert.ok(!server.passed["quantity/predict"]);

  answerAll(root, { p1: "12" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  const p = server.calls.filter((c) => c.url === "/api/learn/quantity/predict").pop();
  assert.deepStrictEqual(p.body, { answer: { p1: 12, p2: "Rice B", p3: true } });
  assert.strictEqual(one(root, "role", "progressbar").getAttribute("aria-valuenow"), "13");

  one(root, "data-choice", "b").fire("click"); await app.idle();
  assert.ok(!server.passed["quantity/bug"]);
  one(root, "data-choice", "a").fire("click"); await app.idle();
  assert.ok(server.passed["quantity/bug"]);

  const ta = one(root, "data-input", "write");
  assert.match(ta.value, /def quantity/);
  ta.value = "def quantity(group):\n    return 0\n";
  one(root, "data-action", "write").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "write").textContent, /the real merge gives 12 and yours gives 0/);
  ta.value = "def quantity(group):\n    return sum(o.value for o in group)\n";
  one(root, "data-action", "write").fire("click"); await app.idle();
  assert.strictEqual(server.calls.filter((c) => c.url === "/api/learn/quantity/write").pop().body.source, ta.value);

  assert.strictEqual(one(root, "data-stage", "lww").getAttribute("data-locked"), "false", "the next stage opens without explain");
  one(root, "data-action", "reveal").fire("click"); await app.idle();
  assert.strictEqual(one(root, "data-code", "quantity").textContent, REAL_CODE);

  assert.ok(!byAttr(root, "data-rubric", "explain").length, "rubric stays hidden until the learner answers");
  one(root, "data-input", "explain").value = "Adjustments are signed so issuing subtracts.";
  one(root, "data-action", "explain").fire("click"); await app.idle();
  assert.match(one(root, "data-rubric", "explain").textContent, /Covered: mentions signed adjustments/);
  assert.match(one(root, "data-rubric", "explain").textContent, /Missed: mentions order does not matter/);
});

test("a stale cache is corrected by the server's progress on reload", async () => {
  const storage = memoryStorage(), server = fakeServer();
  server.passed["quantity/predict"] = true; server.passed["quantity/bug"] = true; server.passed["quantity/write"] = true;
  const { root } = await mounted(server, storage);
  assert.strictEqual(one(root, "data-stage", "lww").getAttribute("data-locked"), "false");
  one(root, "data-stage", "quantity").children[0].fire("click");
  assert.ok(byAttr(root, "data-action", "reveal").length, "an earned stage offers its real code");
});

test("works offline: stages come from the cache and predict is graded locally, then synced", async () => {
  const storage = memoryStorage();
  const server = fakeServer();
  await mounted(server, storage);              // first visit online fills the cache
  server.setOffline(true);
  const { root, app } = await mounted(server, storage);
  assert.ok(byAttr(root, "data-stage", "quantity").length, "stages render from the cache");
  assert.match(one(root, "data-status", "net").textContent, /offline/i);

  answerAll(root, { p1: "12", p2: "Rice B", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "predict").textContent, /Right/);
  assert.strictEqual(one(root, "role", "progressbar").getAttribute("aria-valuenow"), "13");
  assert.ok(!server.passed["quantity/predict"]);

  one(root, "data-action", "write").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "write").textContent, /earn the previous checkpoint/i);
  one(root, "data-choice", "a").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "bug").textContent, /signal/i);
  assert.ok(!server.passed["quantity/bug"], "the bug checkpoint is only earned when the server grades it");

  server.setOffline(false);
  await app.flush();
  assert.ok(server.passed["quantity/predict"], "queued predict reaches the server when the signal returns");
});

test("a blank predict case is not sent", async () => {
  const server = fakeServer();
  const { root, app } = await mounted(server);
  answerAll(root, { p1: "12", p2: "", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "predict").textContent, /Answer every case/);
  assert.ok(!server.calls.some((c) => c.url.endsWith("/predict")));
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
