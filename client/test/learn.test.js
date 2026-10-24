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
const BUG_EXPLANATION = "Taking abs() counts a withdrawal as stock coming in.";

const BUG_LINES = ["total = 0", "for o in group:", "total += abs(o.value)", "return total", "# end"];
const scaled = (k) => OPS.map((o) => ({ ...o, value: o.value * k }));

function fakeServer() {
  const passed = {};
  const calls = [];
  const state = { p1: 12, lockedUntil: 0, now: 0 };
  let offline = false;
  const cases = () => [{ ...CASES[0], ops: scaled(state.p1 / 12) }, CASES[1], CASES[2]];
  const progressOf = (id) => ({ predict: !!passed[id + "/predict"], bug: !!passed[id + "/bug"],
                               write: !!passed[id + "/write"], explain: !!passed[id + "/explain"] });
  const unlocked = (id) => passed[id + "/predict"] && passed[id + "/bug"] && passed[id + "/write"];
  const stage = (id, title, order) => ({
    id, order, title, summary: "A lesson.", unlocked: !!unlocked(id), progress: progressOf(id),
    code: unlocked(id) ? REAL_CODE : null,
    predict: { prompt: "Predict the merged result.", cases: cases() },
    bug: { prompt: "What is wrong?", excerpt: BUG_LINES,
           options: [{ id: "a", text: "counts magnitudes" }, { id: "b", text: "ignores replica" }] },
    write: { function: "quantity", prompt: "Write quantity(group)", stub: "def quantity(group):\n    pass\n" },
    explain: { prompt: "Why sum signed values?", min_points: 1 },
  });
  async function fetchStub(url, init) {
    const headers = (init && init.headers) || {};
    calls.push({ url, method: (init && init.method) || "GET", headers, credentials: init && init.credentials, body: init && init.body ? JSON.parse(init.body) : null });
    if (offline) throw new TypeError("Failed to fetch");
    const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
    if (url === "/api/learn/stages") return json({ stages: [stage("quantity", "Quantities add up", 1), stage("lww", "Last writer wins", 2)] });
    const m = url.match(/^\/api\/learn\/([^/]+)\/(predict|bug|write|explain)$/);
    if (!m) return json({ detail: "not found" }, 404);
    const [, id, kind] = m; const body = JSON.parse(init.body);
    let ok, result = {};
    if (kind === "predict") {
      const right = { p1: state.p1, p2: "Rice B", p3: true };
      const wrong = Object.keys(right).filter((k) => body.answer[k] !== right[k]);
      ok = wrong.length === 0;
      if (!ok) {                                   // a wrong case is named, never answered, and re-rolled
        result = { wrong };
        if (wrong.includes("p1")) { state.p1 *= 2; result.new_cases = [{ ...CASES[0], ops: scaled(state.p1 / 12) }]; }
      }
    }
    if (kind === "bug") {      // same nesting as server/learn_api.py grade(): everything the grader says sits under result
      if (state.lockedUntil > state.now) return { ok: false, status: 429, headers: { get: () => "30" }, json: async () => ({ detail: "locked" }) };
      ok = !!body.answer && body.answer.option === "a" && body.answer.line === 2;
      if (ok) result = { answer: "abs-of-value", mutant: "abs", explanation: BUG_EXPLANATION, counterexample: { case: "p1", ops: OPS, expected: 12, got: 18 } };
      else { state.lockedUntil = state.now + 30; result = { locked_for: 30, options: [{ id: "b", text: "ignores replica" }, { id: "a", text: "counts magnitudes" }] }; }
    }
    if (kind === "write") {
      ok = /sum\(/.test(body.source);
      if (!ok) result = { passed: false, error: "the result differs from the real merge", counterexample: { ops: OPS, expected: 12, got: 0 } };
    }
    if (kind === "explain") {
      ok = body.text.trim().split(/\s+/).length >= 25;
      result = { passed: ok, covered: [{ id: "signed", point: "mentions signed adjustments" }], missed: [{ id: "order", point: "mentions order does not matter", needs: "quote numbers from your own case p1" }],
                 reasons: ["write at least 25 words (you wrote 6)"], words: 6, min_words: 25 };
    }
    if (ok) passed[id + "/" + kind] = true;
    return json({ stage: id, checkpoint: kind, passed: !!ok, result: Object.assign({ passed: !!ok }, result),
                  progress: progressOf(id), unlocked: !!unlocked(id), code: unlocked(id) ? REAL_CODE : null });
  }
  return { fetch: fetchStub, calls, passed, state, setOffline: (v) => { offline = v; } };
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

test("the learner is the server's HttpOnly cookie: no id is invented, stored or sent by the page", async () => {
  const storage = memoryStorage(), server = fakeServer();
  server.passed["quantity/predict"] = true;
  const { root, app } = await mounted(server, storage);
  await bug(root, app, "a");
  assert.ok(server.calls.length >= 2);
  server.calls.forEach((c) => assert.ok(!("X-Learner-Id" in c.headers), "the header is gone"));
  assert.ok(!/learner/i.test(JSON.stringify(storage.dump)), "nothing learner-shaped in local storage");
  assert.ok(!/X-Learner-Id/.test(read("learn.js").replace(/^\/\/.*$/gm, "")), "learn.js has no learner header code");
});

test("the browser is told to send the cookie with every request", async () => {
  const server = fakeServer();
  await mounted(server);
  server.calls.forEach((c) => assert.strictEqual(c.credentials, "same-origin"));
});

// click the changed line (index 2 in BUG_LINES), pick the cause, press Check
async function bug(root, app, option, line = 2) {
  one(root, "data-line", String(line)).fire("click");
  one(root, "data-choice", option).fire("click");
  one(root, "data-action", "bug").fire("click"); await app.idle();
}
// what a learner who worked each case out would type, read from the cases the page is showing now
function solved(app, stageId = "quantity") {
  const out = {};
  app.state.stages.find((s) => s.id === stageId).checkpoints.predict.cases.forEach((c) => { out[c.id] = String(L.predictAnswer(c)).replace("true", "yes").replace("false", "no"); });
  return out;
}
function answerAll(root, values) {
  Object.keys(values).forEach((id) => { one(root, "data-input", "predict:" + id).value = values[id]; });
}

test("earning predict, bug and write unlocks the next stage and reveals the real code", async () => {
  const server = fakeServer();
  const { root, app } = await mounted(server);

  answerAll(root, { p1: "9", p2: "Rice B", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  const miss = one(root, "data-result", "predict").textContent;
  assert.match(miss, /Not right: p1/);
  assert.ok(!/12|24/.test(miss), "a wrong guess never reveals the right answer: " + miss);
  assert.match(miss, /replaced with new numbers/);
  assert.ok(!server.passed["quantity/predict"]);
  assert.strictEqual(L.predictAnswer(app.state.stages[0].checkpoints.predict.cases[0]), 24, "p1 was swapped for the server's new case");

  answerAll(root, solved(app));
  one(root, "data-action", "predict").fire("click"); await app.idle();
  const p = server.calls.filter((c) => c.url === "/api/learn/quantity/predict").pop();
  assert.deepStrictEqual(p.body, { answer: { p1: 24, p2: "Rice B", p3: true } });
  assert.strictEqual(one(root, "role", "progressbar").getAttribute("aria-valuenow"), "13");

  await bug(root, app, "b");
  assert.ok(!server.passed["quantity/bug"]);
  delete app.state.locks.quantity; server.state.now += 31;                       // the 30 s lock runs out
  await bug(root, app, "a");
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

test("a correct find-the-bug shows the explanation the API nests under result", async () => {
  const server = fakeServer();
  server.passed["quantity/predict"] = true;
  const { root, app } = await mounted(server);
  await bug(root, app, "b");
  assert.ok(!/abs\(\)/.test(one(root, "data-result", "bug").textContent), "a wrong pick does not reveal it");
  delete app.state.locks.quantity; server.state.now += 31;
  await bug(root, app, "a");
  const text = one(root, "data-result", "bug").textContent;
  assert.match(text, /Found it\./);
  assert.ok(text.includes(BUG_EXPLANATION), text);
});

test("find-the-bug still copes with an un-nested explanation", async () => {
  const server = fakeServer(), inner = server.fetch;
  server.fetch = async (url, init) => {
    const r = await inner(url, init), body = await r.json();
    if (/\/bug$/.test(url)) { body.explanation = "flat"; body.result = { passed: body.passed }; }
    return { ok: r.ok, status: r.status, json: async () => body };
  };
  server.passed["quantity/predict"] = true;
  const { root, app } = await mounted(server);
  await bug(root, app, "a");
  assert.match(one(root, "data-result", "bug").textContent, /Found it\. flat/);
});

test("a failed write shows the counterexample first, then the error string", async () => {
  const server = fakeServer();
  server.passed["quantity/predict"] = true; server.passed["quantity/bug"] = true;
  const { root, app } = await mounted(server);
  one(root, "data-input", "write").value = "def quantity(group):\n    return 0\n";
  one(root, "data-action", "write").fire("click"); await app.idle();
  const text = one(root, "data-result", "write").textContent;
  assert.match(text, /^Not yet: /);
  const at = (re) => text.search(re);
  assert.ok(at(/a:1/) > 0 && at(/real merge gives 12 and yours gives 0/) > at(/a:1/), "the operations come before expected and got: " + text);
  assert.ok(at(/the result differs from the real merge/) > at(/yours gives 0/), "the error string comes after the counterexample: " + text);
  assert.ok(text.includes("b:2") && text.includes("-3"), "every operation is shown");
});

test("failureText falls back to the error alone when there is no counterexample", () => {
  assert.strictEqual(L.failureText({ error: "SyntaxError: bad", counterexample: null }), "SyntaxError: bad");
  assert.match(L.failureText({}), /referee/);
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
  await bug(root, app, "a");
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
  assert.ok(read("ledger.html").includes('id="addbtn"'), "the old phone app lives on at ledger.html");
  assert.ok(read("board.html").includes('href="/static/learn.html"'));
});

// ---- the front door ----
async function earnStageOne(server, root, app) {
  one(root, "data-view", "course").fire("click");
  answerAll(root, { p1: "12", p2: "Rice B", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  await bug(root, app, "a");
  one(root, "data-input", "write").value = "def quantity(group):\n    return sum(o.value for o in group)\n";
  one(root, "data-action", "write").fire("click"); await app.idle();
}
const view = (root, v) => one(root, "data-view", v).fire("click");

test("the front door pitches the product, links Course 1 and shows four tabs", async () => {
  const { root } = await mounted(fakeServer());
  assert.match(one(root, "data-hero", "pitch").textContent, /Do not paste code you do not understand/);
  assert.deepStrictEqual(all(root, (n) => n.getAttribute("role") === "tab").map((n) => n.getAttribute("data-view")), ["course", "map", "results", "proof"]);
  const html = read("index.html");
  for (const href of ["/static/ledger.html", "/board", "/race", "/static/learn.html"]) assert.ok(html.includes('href="' + href + '"'), href);
  assert.ok(html.indexOf("https://ryyansafar.site") < html.indexOf("https://github.com/ryyansafar"));
  assert.ok(html.indexOf("/static/understand.js") < html.indexOf("/static/learn.js") && html.indexOf("/static/learn_views.js") < html.indexOf("/static/learn.js"));
});

test("the Understanding Map starts empty and colours code once a stage is earned", async () => {
  const server = fakeServer(), { root, app } = await mounted(server);
  view(root, "map");
  assert.strictEqual(one(root, "data-gap", "none").textContent.includes("no code yet"), true);
  assert.match(one(root, "data-map", "quantity").textContent, /Not revealed yet/);
  await earnStageOne(server, root, app);
  one(root, "data-action", "reveal").fire("click"); await app.idle();
  view(root, "map");
  const gap = all(root, (n) => n.getAttribute("data-gap") !== null && n.getAttribute("data-gap") !== "none");
  assert.ok(gap.length, "a gap score appears once code is unlocked");
  assert.ok(Number(gap[0].getAttribute("data-gap")) > 0);
  assert.ok(byAttr(root, "data-level", "written").length + byAttr(root, "data-level", "bug").length > 0);
  assert.strictEqual(one(root, "data-map", "quantity").textContent, REAL_CODE);
});

test("hints and first-try results are counted and shown on Results", async () => {
  const server = fakeServer(), { root, app } = await mounted(server);
  one(root, "data-hint-btn", "predict").fire("click");
  assert.match(one(root, "data-hint", "predict").textContent, /^Hint: p1: 4 operations, 3 distinct ids/);
  answerAll(root, { p1: "9", p2: "Rice B", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();           // wrong first
  answerAll(root, solved(app));
  one(root, "data-action", "predict").fire("click"); await app.idle();
  await bug(root, app, "a");                  // right first time
  assert.deepStrictEqual(app.state.attempts["quantity/predict"], { tries: 2, firstTry: false });
  assert.deepStrictEqual(app.state.attempts["quantity/bug"], { tries: 1, firstTry: true });
  view(root, "results");
  assert.match(root.textContent, /50%/);
  assert.match(root.textContent, /1\s*hints used/i);
  assert.ok(byAttr(root, "data-fade", "none").length, "no write passed yet, so no scaffold share");
});

test("before quiz is graded by the real merge, kept, and the after quiz opens after a stage is earned", async () => {
  const server = fakeServer(), storage = memoryStorage();
  const { root, app } = await mounted(server, storage);
  view(root, "results");
  assert.ok(!byAttr(root, "data-action", "quiz-after").length, "after quiz is locked until a stage is earned");
  one(root, "data-action", "quiz-before").fire("click");
  const inputs = all(root, (n) => (n.getAttribute("data-input") || "").indexOf("quiz:") === 0);
  assert.strictEqual(inputs.length, 4);
  one(root, "data-action", "quiz-submit").fire("click");
  assert.match(one(root, "data-result", "quiz").textContent, /Answer every question/);
  inputs.forEach((i) => { i.value = i.type === "number" ? "0" : "yes"; });
  one(root, "data-action", "quiz-submit").fire("click");
  assert.match(one(root, "data-result", "quiz").textContent, /You scored \d of 4/);
  assert.strictEqual(app.state.quiz.before.total, 4);
  assert.ok(JSON.parse(storage.getItem("sc_learn")).quiz.before, "the baseline is saved on the phone");
  one(root, "data-action", "quiz-done").fire("click");
  assert.ok(!byAttr(root, "data-action", "quiz-before").length, "the baseline can only be taken once");
  await earnStageOne(server, root, app);
  view(root, "results");
  assert.ok(byAttr(root, "data-action", "quiz-after").length);
});

test("reveal shows the real code with your function diffed against it and the bug line marked", () => {
  const V = require("../learn_views.js");
  const h = (tag, attrs, kids) => {
    const e = doc.createElement(tag);
    Object.keys(attrs || {}).forEach((k) => { if (k === "text") e.textContent = attrs[k]; else if (k === "className") e.className = attrs[k]; else e.setAttribute(k, attrs[k]); });
    (kids || []).forEach((c) => e.appendChild(typeof c === "string" ? doc.createTextNode(c) : c));
    return e;
  };
  const code = "def quantity(ops):\n    return sum(o.value for o in ops)\n";
  const stage = { id: "q", earned: true, checkpoints: { write: { function: "quantity" }, bug: { snippet: "x = 1\nreturn abs(v)\n", line: "return abs(v)" } } };
  const st = { passed: { "q/predict": true, "q/bug": true, "q/write": true }, drafts: { "q/write": "def quantity(ops):\n    total = 0\n    return sum(o.value for o in ops)\n" } };
  const root = doc.createElement("div"); root.replaceChildren(...V.revealView({ h, st }, stage, code));
  assert.strictEqual(one(root, "data-code", "q").textContent, "def quantity(ops):\n    return sum(o.value for o in ops)");
  const marks = byAttr(root, "data-diff", "del").concat(byAttr(root, "data-diff", "add"));
  assert.deepStrictEqual(marks.map((n) => n.getAttribute("data-diff") + ":" + n.textContent), ["del:    total = 0"]);
  assert.strictEqual(one(root, "data-bug-line", "q").children.filter((c) => c.getAttribute && c.getAttribute("data-m") === "!").length, 1);
});

test("the proof card holds only measured facts, can be named, copied and downloaded", async () => {
  const server = fakeServer(), copied = [];
  const root = doc.createElement("div"), storage = memoryStorage();
  const app = L.mount(root, { doc, fetch: server.fetch, storage, nav: { clipboard: { writeText: async (t) => { copied.push(t); } } } });
  await app.ready;
  view(root, "proof");
  assert.match(one(root, "data-status", "proof-empty").textContent, /Nothing to prove yet/);
  await earnStageOne(server, root, app); view(root, "proof");
  const name = one(root, "data-input", "proof-name"); name.value = "Asha"; name.fire("input");
  assert.strictEqual(app.state.name, "Asha");
  one(root, "data-action", "copy").fire("click"); await app.idle();
  assert.strictEqual(copied.length, 1);
  assert.match(copied[0], /Earned 1 of 2 stages/);
  assert.match(copied[0], /self-reported, not an exam/);
  assert.match(one(root, "data-action", "download").getAttribute("href"), /^data:image\/svg\+xml/);
  assert.match(one(root, "data-result", "proof").textContent, /Copied/);
});

test("copy without a clipboard says so instead of failing", async () => {
  const { root, app } = await mounted(fakeServer());
  view(root, "proof");
  one(root, "data-action", "copy").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "proof").textContent, /not available/);
});

test("the old ledger app is still reachable from the front door and the cache holds the new files", () => {
  const sw = read("sw.js");
  for (const f of ["/static/ledger.html", "/static/understand.js", "/static/learn_views.js", "/board"]) assert.ok(sw.includes('"' + f + '"'), f);
  assert.match(read("ledger.html"), /href="\/"/);
});

// ---- an honest lock: no grader on the global, server is the judge, wrong picks cost time ----
test("the browser global has no grader: Learn.predictAnswer and Learn.gradePredict do not exist", () => {
  const vm = require("node:vm");
  const sandbox = { self: null };
  sandbox.self = sandbox;
  vm.runInNewContext(read("learn.js"), sandbox);
  assert.ok(sandbox.Learn && typeof sandbox.Learn.mount === "function");
  assert.strictEqual(sandbox.Learn.predictAnswer, undefined);
  assert.strictEqual(sandbox.Learn.gradePredict, undefined);
  assert.ok(!/root\.Learn\s*=\s*Object\.assign/.test(read("learn.js")));
});

test("with a signal the server grades predict and a wrong answer names the case only", async () => {
  const server = fakeServer(), { root, app } = await mounted(server);
  answerAll(root, { p1: "12", p2: "Rice A", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.ok(server.calls.some((c) => c.url === "/api/learn/quantity/predict"), "even a locally right p1 goes to the server");
  assert.match(one(root, "data-result", "predict").textContent, /^Not right: p2\./);
  assert.ok(!/Rice B/.test(one(root, "data-result", "predict").textContent));
  assert.ok(!app.state.passed["quantity/predict"]);
});

test("an offline pass is provisional, replayed on reconnect, and revoked when the server disagrees", async () => {
  const storage = memoryStorage(), server = fakeServer();
  await mounted(server, storage);
  server.setOffline(true);
  const { root, app } = await mounted(server, storage);
  answerAll(root, solved(app));
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "predict").textContent, /counts only when the server agrees/);
  assert.strictEqual(app.state.passed["quantity/predict"], true);
  assert.strictEqual(app.state.provisional["quantity/predict"], true);
  assert.ok(JSON.parse(storage.getItem("sc_learn")).provisional["quantity/predict"], "the provisional mark survives a reload");
  server.state.p1 = 24;                                    // the server's case for this learner changed meanwhile
  server.setOffline(false);
  await app.flush();
  assert.ok(!server.passed["quantity/predict"]);
  assert.strictEqual(app.state.passed["quantity/predict"], undefined, "the pass the phone gave itself is taken back");
  assert.deepStrictEqual(app.state.provisional, {});
  assert.strictEqual(app.state.queue.length, 0);
});

test("an offline pass the server confirms becomes a real one", async () => {
  const storage = memoryStorage(), server = fakeServer();
  await mounted(server, storage);
  server.setOffline(true);
  const { root, app } = await mounted(server, storage);
  answerAll(root, solved(app));
  one(root, "data-action", "predict").fire("click"); await app.idle();
  server.setOffline(false);
  await app.flush();
  assert.ok(server.passed["quantity/predict"]);
  assert.strictEqual(app.state.passed["quantity/predict"], true);
  assert.deepStrictEqual(app.state.provisional, {});
});

test("offline, a wrong answer is not queued and does not show the right one", async () => {
  const storage = memoryStorage(), server = fakeServer();
  await mounted(server, storage);
  server.setOffline(true);
  const { root, app } = await mounted(server, storage);
  answerAll(root, { p1: "9", p2: "Rice B", p3: "yes" });
  one(root, "data-action", "predict").fire("click"); await app.idle();
  const text = one(root, "data-result", "predict").textContent;
  assert.match(text, /Not right: p1/);
  assert.ok(!/12/.test(text));
  assert.strictEqual(app.state.queue.length, 0);
  assert.ok(!app.state.passed["quantity/predict"]);
});

test("a server error does not let the phone pass predict by itself", async () => {
  const server = fakeServer(), inner = server.fetch;
  server.fetch = async (url, init) => (/\/predict$/.test(url) ? { ok: false, status: 500, json: async () => ({}) } : inner(url, init));
  const { root, app } = await mounted(server);
  answerAll(root, solved(app));
  one(root, "data-action", "predict").fire("click"); await app.idle();
  assert.ok(!app.state.passed["quantity/predict"]);
  assert.strictEqual(app.state.queue.length, 0);
});

test("find-the-bug needs a changed line and a cause before anything is sent", async () => {
  const server = fakeServer();
  server.passed["quantity/predict"] = true;
  const { root, app } = await mounted(server);
  assert.strictEqual(byAttr(root, "data-line", "0").length + byAttr(root, "data-line", "4").length, 2, "every excerpt line is clickable");
  assert.ok(all(root, (n) => n.getAttribute && n.getAttribute("data-line") !== null).length <= 5, "an excerpt of at most five lines");
  one(root, "data-choice", "a").fire("click");
  one(root, "data-action", "bug").fire("click"); await app.idle();
  assert.match(one(root, "data-result", "bug").textContent, /Click the changed line, pick the cause/);
  assert.ok(!server.calls.some((c) => /\/bug$/.test(c.url)));
  one(root, "data-line", "2").fire("click");
  assert.strictEqual(one(root, "data-line", "2").getAttribute("aria-pressed"), "true");
  one(root, "data-action", "bug").fire("click"); await app.idle();
  const sent = server.calls.filter((c) => /\/bug$/.test(c.url)).pop();
  assert.deepStrictEqual(sent.body, { answer: { option: "a", line: 2 } });
});

test("a wrong find-the-bug pick locks the options for 30 s, reshuffles them and shows no answer", async () => {
  const server = fakeServer();
  server.passed["quantity/predict"] = true;
  const { root, app } = await mounted(server);
  const order = () => all(root, (n) => n.getAttribute && n.getAttribute("data-choice") !== null).map((n) => n.getAttribute("data-choice"));
  assert.deepStrictEqual(order(), ["a", "b"]);
  await bug(root, app, "b", 0);
  assert.deepStrictEqual(order(), ["b", "a"], "the server's new order is shown");
  const text = one(root, "data-result", "bug").textContent;
  assert.match(text, /locked for \d+ seconds/);
  assert.ok(!/abs|magnitud/i.test(text));
  const left = Math.round((app.state.locks.quantity - Date.now()) / 1000);
  assert.ok(left >= 28 && left <= 30, "the lock is about 30 s: " + left);
  assert.strictEqual(one(root, "data-choice", "a").disabled, true);
  assert.strictEqual(one(root, "data-line", "2").disabled, true);
  const before = server.calls.length;
  await bug(root, app, "a");                                  // still locked: the page does not even ask
  assert.strictEqual(server.calls.length, before);
  assert.match(one(root, "data-result", "bug").textContent, /Locked for \d+ more seconds/);
});

test("the server's 429 starts the countdown, and a reload restores it from locked_for", async () => {
  const server = fakeServer();
  server.passed["quantity/predict"] = true;
  const { root, app } = await mounted(server);
  await bug(root, app, "b", 0);
  delete app.state.locks.quantity;                            // the page forgot (a reload), the server did not
  await bug(root, app, "a");
  assert.ok(app.state.locks.quantity > Date.now(), "429 with Retry-After starts the lock again");
  assert.match(one(root, "data-result", "bug").textContent, /Wait 30 seconds/);
  const inner = server.fetch;
  server.fetch = async (url, init) => {
    const r = await inner(url, init);
    if (url !== "/api/learn/stages") return r;
    const body = await r.json(); body.stages[0].bug.locked_for = 17;
    return { ok: true, status: 200, json: async () => body };
  };
  const again = await mounted(server);
  const left = Math.round((again.app.state.locks.quantity - Date.now()) / 1000);
  assert.ok(left >= 15 && left <= 17, "locked_for from the stage list is honoured: " + left);
});

test("explain says why it failed and a failed explain is not recorded as passed", async () => {
  const server = fakeServer();
  ["predict", "bug", "write"].forEach((k) => { server.passed["quantity/" + k] = true; });
  const { root, app } = await mounted(server);
  one(root, "data-stage", "quantity").children[0].fire("click");
  one(root, "data-input", "explain").value = "too short";
  one(root, "data-action", "explain").fire("click"); await app.idle();
  const text = one(root, "data-rubric", "explain").textContent;
  assert.match(text, /To pass: write at least 25 words/);
  assert.match(text, /quote numbers from your own case p1/);
  assert.match(one(root, "data-result", "explain").textContent, /never unlocks the reference code/);
  assert.ok(!app.state.passed["quantity/explain"]);
});

test("the page tells the truth about what is gated", () => {
  const src = read("learn.js") + read("learn_views.js");
  assert.match(src, /merge code is public/);
  assert.match(src, /nothing here is secret/);
  assert.ok(!/stays locked|code stays locked|real code is unlocked/i.test(src));
});
