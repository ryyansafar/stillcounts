// The controller wires the pure state machine to the bridge, the replay, the grader, storage and the stress engine.
const test = require("node:test");
const assert = require("node:assert");
const K = require("../chat_core.js");
const R = require("../chat_replay.js");
const Ctl = require("../chat_ctl.js");

const PLAN = [1, 2, 3].map((n) => ({ n, id: "st" + n, title: "Stage " + n, summary: "s" + n }));
const view = (n, extra) => Object.assign({ n, id: "st" + n, title: "Stage " + n, status: "built", predict: {}, bug: {}, write: {}, explain: {},
  progress: { predict: false, bug: false, write: false, explain: false }, unlocked: false, code: null }, extra || {});
const snap = (detail, stages, extra) => Object.assign({ status: "running", status_detail: detail, acu_consumed: 0.4, acu_limit: 3, url: "https://app.devin.ai/sessions/s1",
  plan: PLAN, messages: [], pull_requests: [], stages }, extra || {});
const httpErr = (status, code) => Object.assign(new Error("HTTP " + status), { status, code });

function memory() { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, dump: m }; }
function scheduler() {
  const q = []; let id = 0;
  return { schedule: (fn, ms) => { const h = ++id; q.push({ h, fn, ms }); return h; }, cancel: (h) => { const i = q.findIndex((x) => x.h === h); if (i >= 0) q.splice(i, 1); },
           pending: () => q.length, async runAll(max = 50) { for (let i = 0; i < max && q.length; i++) { const x = q.shift(); await x.fn(); } },
           async runOne() { const x = q.shift(); if (x) await x.fn(); } };
}
function hooks(level = 2) { return { level, getDifficulty() { return this.level; }, onAdapt(cb) { this.cb = cb; }, emit(l) { const p = this.level; this.level = l; this.cb({ level: l, previous: p }); } }; }

function fakeBridge(script) {
  const b = { calls: [], script: script.slice(), startError: null, pollError: null };
  b.start = async (req) => { b.calls.push(["start", req]); if (b.startError) throw b.startError; return { id: "s1", url: "https://app.devin.ai/sessions/s1", acuLimit: 3, live: true }; };
  b.poll = async (id) => { b.calls.push(["poll", id]); if (b.pollError) throw b.pollError; return b.script.length > 1 ? b.script.shift() : b.script[0]; };
  b.message = async (id, text, meta) => { b.calls.push(["message", id, text, meta]); };
  b.stop = async (id) => { b.calls.push(["stop", id]); };
  b.stage = async (id, n) => { b.calls.push(["stage", id, n]); return view(n, { unlocked: true, code: "code" + n, progress: { predict: true, bug: true, write: true } }); };
  return b;
}

function setup(o = {}) {
  const storage = o.storage || memory(), sch = scheduler(), h = hooks(), clock = { t: 1000 };
  const bridge = o.bridge || fakeBridge([snap("working", [])]);
  const grades = [];
  const fetch = async (url, init) => {
    grades.push({ url, body: JSON.parse(init.body), learner: init.headers["X-Learner-Id"] });
    const next = o.grade ? o.grade(url, JSON.parse(init.body)) : { passed: true, progress: { predict: true, bug: true, write: true, explain: false }, code: "CODE" };
    return { ok: true, status: 200, json: async () => next };
  };
  const courseViews = [1, 2, 3].map((n) => ({ n, id: "demo" + n, title: "Demo " + n, summary: "d" + n, predict: {}, bug: {}, write: {}, explain: {}, progress: { predict: false, bug: false, write: false, explain: false }, unlocked: false, code: null }));
  const ctl = Ctl.createController({ storage, fetch, now: () => clock.t, schedule: sch.schedule, cancel: sch.cancel, hooks: h, liveBridge: bridge,
    newId: (() => { let i = 0; return () => "c" + (++i); })(), loadCourse: async () => courseViews, replayFactory: (views, state) => R.createReplay({ views, now: () => clock.t, stepMs: 100, state }),
    onChange: () => {} });
  return { ctl, storage, sch, h, bridge, grades, clock };
}

const sent = (bridge, reason) => bridge.calls.filter((c) => c[0] === "message" && c[3].reason === reason);

test("a prompt starts a real session, plans, and then Devin's build log fills the thread", async () => {
  const bridge = fakeBridge([snap("working", [], { messages: [{ id: "m1", source: "devin", text: "reading" }] })]);
  const { ctl } = setup({ bridge });
  await ctl.submit("build a tip calculator");
  const c = ctl.active();
  assert.strictEqual(c.source, "live");
  assert.strictEqual(bridge.calls[0][0], "start");
  assert.strictEqual(bridge.calls[0][1].prompt, "build a tip calculator");
  assert.strictEqual(K.session(c).url, "https://app.devin.ai/sessions/s1");
  await ctl.pollNow(c.id);
  const now = ctl.active();
  assert.ok(now.thread.some((t) => t.kind === "plan"));
  assert.ok(now.thread.some((t) => t.kind === "log" && t.text === "reading"));
  assert.strictEqual(K.session(now).acuLimit, 3);
});

test("Devin stops after each stage and is sent nothing until the learner earns it", async () => {
  const bridge = fakeBridge([snap("waiting_for_user", [view(1)]), snap("waiting_for_user", [view(1)])]);
  const { ctl } = setup({ bridge });
  await ctl.submit("x");
  const id = ctl.active().id;
  await ctl.pollNow(id); await ctl.pollNow(id);
  assert.deepStrictEqual(sent(bridge, "continue"), [], "stage 2 waits until stage 1 is earned");
  assert.strictEqual(K.gateState(ctl.active()).kind, "waiting_for_learner");
});

test("earning stage 1 reveals its code and Devin is told to go on to stage 2", async () => {
  const bridge = fakeBridge([snap("waiting_for_user", [view(1)])]);
  const { ctl, grades } = setup({ bridge });
  await ctl.submit("x");
  const id = ctl.active().id;
  await ctl.pollNow(id);
  await ctl.grade(id, 1, "predict", { answer: { p1: 3 } });
  assert.strictEqual(grades[0].url, "/api/devin/sessions/s1/stages/1/predict");
  const c = ctl.active();
  assert.strictEqual(K.isEarned(K.stageOf(c, 1)), true);
  assert.strictEqual(K.stageOf(c, 1).code, "CODE");
  assert.ok(sent(bridge, "continue").some((m) => m[3].n === 2));
});

test("a wrong answer keeps Devin stopped: nothing is sent, nothing unlocks", async () => {
  const bridge = fakeBridge([snap("waiting_for_user", [view(1), view(2)])]);
  const { ctl } = setup({ bridge, grade: () => ({ passed: false, progress: { predict: false, bug: false, write: false, explain: false }, code: null, result: { hint: "look again" } }) });
  await ctl.submit("x");
  const id = ctl.active().id;
  await ctl.pollNow(id);
  const before = sent(bridge, "continue").length;
  const res = await ctl.grade(id, 1, "bug", { answer: "a" });
  assert.strictEqual(res.passed, false);
  assert.strictEqual(K.isEarned(K.stageOf(ctl.active(), 1)), false);
  assert.strictEqual(sent(bridge, "continue").length, before);
});

test("the controller refuses to grade a stage the learner may not work on yet", async () => {
  const bridge = fakeBridge([snap("waiting_for_user", [view(1), view(2)])]);
  const { ctl, grades } = setup({ bridge });
  await ctl.submit("x");
  await ctl.pollNow(ctl.active().id);
  await assert.rejects(() => ctl.grade(ctl.active().id, 2, "predict", { answer: {} }), /locked/i);
  assert.strictEqual(grades.length, 0);
});

test("the stress engine lowering difficulty messages the running Devin session", async () => {
  const { ctl, bridge, h } = setup();
  await ctl.submit("x");
  await ctl.pollNow(ctl.active().id);
  h.emit(1);
  await new Promise((r) => setImmediate(r));
  const m = sent(bridge, "adapt");
  assert.strictEqual(m.length, 1);
  assert.match(m[0][2], /stressed.*smaller.*comments/);
  assert.ok(ctl.active().thread.some((t) => t.kind === "bridge" && /stress/i.test(t.text)));
});

test("a new chat starts at the difficulty the stress engine reports", async () => {
  const { ctl, h, bridge } = setup();
  h.level = 1;
  await ctl.submit("x");
  assert.strictEqual(ctl.active().difficulty, 1);
  assert.strictEqual(bridge.calls[0][1].difficulty, 1);
});

test("no Devin token on the server: labelled replay, with the reason in the thread", async () => {
  const bridge = fakeBridge([]); bridge.startError = httpErr(503, "no_token");
  const { ctl } = setup({ bridge });
  await ctl.submit("build a game");
  const c = ctl.active();
  assert.strictEqual(c.source, "replay");
  assert.strictEqual(c.replayWhy, "no_token");
  const note = c.thread.find((t) => t.kind === "notice");
  assert.match(note.text, /token/);
  assert.match(note.text, /replay/i);
  assert.ok(K.session(c) && K.session(c).live === false);
  assert.strictEqual(c.plan.length, 3, "the replay course plan is shown");
});

test("quota exceeded at start falls back with the quota reason", async () => {
  const bridge = fakeBridge([]); bridge.startError = httpErr(429, "quota");
  const { ctl } = setup({ bridge });
  await ctl.submit("x");
  assert.strictEqual(ctl.active().replayWhy, "quota");
});

test("a missing bridge (404) falls back and says so", async () => {
  const bridge = fakeBridge([]); bridge.startError = httpErr(404);
  const { ctl } = setup({ bridge });
  await ctl.submit("x");
  assert.strictEqual(ctl.active().replayWhy, "no_bridge");
});

test("one failed poll is retried; three in a row fall back to the labelled replay", async () => {
  const bridge = fakeBridge([snap("working", [])]);
  const { ctl } = setup({ bridge });
  await ctl.submit("x");
  const id = ctl.active().id;
  bridge.pollError = httpErr(500);
  await ctl.pollNow(id);
  assert.strictEqual(ctl.active().source, "live");
  bridge.pollError = null; await ctl.pollNow(id);
  bridge.pollError = httpErr(500); await ctl.pollNow(id); await ctl.pollNow(id);
  assert.strictEqual(ctl.active().source, "live", "the success in between reset the counter");
  await ctl.pollNow(id);
  assert.strictEqual(ctl.active().source, "replay");
  assert.strictEqual(ctl.active().replayWhy, "api_error");
});

test("offline replay toggle: new chats replay without calling Devin; flipping it mid build falls back visibly", async () => {
  const a = setup();
  a.ctl.setOffline(true);
  await a.ctl.submit("x");
  assert.strictEqual(a.bridge.calls.length, 0);
  assert.strictEqual(a.ctl.active().replayWhy, "offline_toggle");
  const b = setup();
  await b.ctl.submit("x");
  b.ctl.setOffline(true);
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(b.ctl.active().source, "replay");
  assert.strictEqual(b.ctl.active().replayWhy, "offline_toggle");
  assert.ok(b.ctl.active().thread.some((t) => t.kind === "notice" && /offline replay/i.test(t.text)));
});

test("the built-in demo is a labelled replay and never touches Devin", async () => {
  const { ctl, bridge } = setup();
  await ctl.submit("demo", { demo: true });
  assert.strictEqual(bridge.calls.length, 0);
  assert.strictEqual(ctl.active().replayWhy, "demo");
  assert.strictEqual(ctl.active().course, "builtin");
});

test("a usage limit in the middle of a live build is shown, not silently swapped for a replay", async () => {
  const bridge = fakeBridge([snap("usage_limit_exceeded", [view(1)], { status: "suspended" })]);
  const { ctl } = setup({ bridge });
  await ctl.submit("x");
  await ctl.pollNow(ctl.active().id);
  assert.strictEqual(ctl.active().source, "live");
  assert.strictEqual(K.gateState(ctl.active()).kind, "limit");
  await ctl.fallbackNow(ctl.active().id, "quota");
  assert.strictEqual(ctl.active().source, "replay");
});

test("stop suspends the Devin session and stops all polling", async () => {
  const { ctl, bridge, sch } = setup();
  await ctl.submit("x");
  await ctl.stop(ctl.active().id);
  assert.ok(bridge.calls.some((c) => c[0] === "stop"));
  assert.strictEqual(K.gateState(ctl.active()).kind, "stopped");
  assert.strictEqual(sch.pending(), 0);
});

test("the replay obeys the same gate end to end: stage 2 only after stage 1 is earned", async () => {
  const { ctl, clock, grades } = setup();
  ctl.setOffline(true);
  await ctl.submit("x");
  const id = ctl.active().id;
  clock.t += 5000; await ctl.pollNow(id);
  assert.strictEqual(K.builtCount(ctl.active()), 1);
  clock.t += 60000; await ctl.pollNow(id); await ctl.pollNow(id);
  assert.strictEqual(K.builtCount(ctl.active()), 1, "stage 2 never starts by itself");
  assert.strictEqual(K.gateState(ctl.active()).kind, "waiting_for_learner");
  await ctl.grade(id, 1, "write", { source: "x" });
  assert.match(grades[0].url, /^\/api\/learn\/demo1\/write$/);
  clock.t += 5000; await ctl.pollNow(id);
  assert.strictEqual(K.builtCount(ctl.active()), 2);
});

test("chats persist, never store locked code, and a reload resumes polling a live session", async () => {
  const bridge = fakeBridge([snap("waiting_for_user", [view(1, { code: "LEAK" })])]);
  const s = setup({ bridge });
  await s.ctl.submit("first");
  await s.ctl.pollNow(s.ctl.active().id);
  const raw = JSON.stringify(s.storage.dump);
  assert.ok(!raw.includes("LEAK"));
  const again = setup({ storage: s.storage, bridge: fakeBridge([snap("working", [])]) });
  assert.strictEqual(again.ctl.chats().length, 1);
  assert.strictEqual(again.ctl.chats()[0].prompt, "first");
  again.ctl.resume();
  assert.ok(again.sch.pending() >= 1, "polling resumed");
});

test("chats are listed newest first and can be selected and removed", async () => {
  const { ctl } = setup();
  await ctl.submit("one"); await ctl.submit("two", { forceNew: true });
  assert.deepStrictEqual(ctl.chats().map((c) => c.prompt), ["two", "one"]);
  ctl.select(ctl.chats()[1].id);
  assert.strictEqual(ctl.active().prompt, "one");
  ctl.newChat();
  assert.strictEqual(ctl.active(), null);
  ctl.remove(ctl.chats()[0].id);
  assert.strictEqual(ctl.chats().length, 1);
});

test("a message typed during a live build goes to Devin with the do-not-skip-ahead rule", async () => {
  const { ctl, bridge } = setup();
  await ctl.submit("x");
  await ctl.submit("please use tabs");
  const m = sent(bridge, "user");
  assert.strictEqual(m.length, 1);
  assert.match(m[0][2], /please use tabs/);
  assert.match(m[0][2], /Do not start the next stage/);
  assert.strictEqual(ctl.chats().length, 1);
});

test("EarnHooks: installed when missing, adopted when a real engine appears later, difficulty clamped", () => {
  const host = {};
  const late = Ctl.lateHooks(host);
  assert.strictEqual(late.getDifficulty(), 3, "no engine yet: normal");
  const got = []; late.onAdapt((e) => got.push(e));
  Ctl.installEarnHooks(host);
  late.bind();
  assert.strictEqual(typeof host.EarnHooks.getDifficulty, "function");
  host.EarnHooks.setDifficulty(1, { reason: "hr" });
  assert.deepStrictEqual(got.map((e) => [e.level, e.previous]), [[1, 3]]);
  assert.strictEqual(late.getDifficulty(), 1);
  let real;
  host.EarnHooks = { getDifficulty: () => 3, onAdapt: (cb) => { real = cb; } };
  late.bind();
  assert.strictEqual(late.getDifficulty(), 3, "the real engine took over");
  real({ level: 2, previous: 3 });
  assert.strictEqual(got.length, 2);
  host.EarnHooks = { getDifficulty: () => 99, onAdapt() {} };
  assert.strictEqual(late.getDifficulty(), 3, "nonsense is ignored");
  const keep = { EarnHooks: { getDifficulty: () => 1, onAdapt() {} } };
  assert.strictEqual(Ctl.installEarnHooks(keep), keep.EarnHooks, "an existing engine is never replaced");
});
