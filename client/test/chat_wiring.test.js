// The stress engine, the controller and Devin together: adaptation really reaches Devin, the why is in the thread, the dashboard gets
// the event without a heart rate, level 1 holds Devin back, and checkpoint results feed the miss counter.
const test = require("node:test");
const assert = require("node:assert");
const K = require("../chat_core.js");
const R = require("../chat_replay.js");
const Ctl = require("../chat_ctl.js");

const PLAN = [1, 2, 3].map((n) => ({ n, id: "st" + n, title: "Stage " + n, summary: "s" + n }));
const view = (n) => ({ n, id: "st" + n, title: "Stage " + n, status: "built", predict: {}, bug: {}, write: {}, explain: {}, progress: { predict: false, bug: false, write: false, explain: false }, unlocked: false, code: null });
const snap = (detail, stages) => ({ status: "running", status_detail: detail, acu_consumed: 0.4, acu_limit: 3, url: "https://app.devin.ai/sessions/s1", plan: PLAN, messages: [], pull_requests: [], stages });

function setup(opts = {}) {
  const calls = [], logged = [], timers = [], attempts = [];
  let level = 3, adapt = null;
  const hooks = { getDifficulty: () => level, onAdapt: (cb) => { adapt = cb; }, reportAttempt: (p) => attempts.push(p) };
  const bridge = {
    start: async () => ({ id: "s1", url: "https://app.devin.ai/sessions/s1", acuLimit: 3, live: true }),
    poll: async () => (opts.snap || snap("waiting_for_user", [view(1)])),
    message: async (id, text, meta) => { calls.push({ text, meta }); }, stop: async () => {}, stage: async () => null,
  };
  const ctl = Ctl.createController({
    storage: { getItem: () => null, setItem() {} }, fetch: async () => ({ ok: true, json: async () => ({ passed: false, progress: {}, code: null }) }),
    now: () => 1000, schedule: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, cancel() {}, hooks, liveBridge: bridge,
    newId: () => "c1", loadCourse: async () => [], replayFactory: () => R.createReplay({ views: [], now: () => 0 }), onChange() {},
    logAdaptation: (p) => logged.push(p),
  });
  return { ctl, calls, logged, timers, attempts, fire: (l, reason) => { const prevLevel = level; level = l; adapt({ level: l, prevLevel, reason }); } };
}

test("a heart-rate spike messages the live Devin session with the plain request, and the thread shows the why", async () => {
  const { ctl, calls, fire } = setup();
  await ctl.submit("build x");
  await ctl.pollNow("c1");
  fire(1, "HR 112 vs baseline 72: switching to guided answers and a slower pace");
  await new Promise((r) => setImmediate(r));
  const adapt = calls.filter((c) => c.meta.reason === "adapt");
  assert.strictEqual(adapt.length, 1);
  assert.match(adapt[0].text, /stressed.*smaller.*comments/i);
  assert.ok(!/112|72|HR/.test(adapt[0].text), "no heart rate goes to Devin");
  const note = ctl.active().thread.filter((t) => t.kind === "bridge").pop().text;
  assert.match(note, /HR 112 vs baseline 72/);
  assert.match(note, /Difficulty lowered to 1 \(was 3\)/);
});

test("the dashboard event carries the level change and no heart rate", async () => {
  const { ctl, logged, fire } = setup();
  await ctl.submit("build x");
  fire(2, "HR 95 vs baseline 70: switching to easier steps");
  assert.strictEqual(logged.length, 1);
  assert.deepStrictEqual([logged[0].level, logged[0].prev, logged[0].kind], [2, 3, "stress"]);
  assert.ok(!/95|70/.test(JSON.stringify(logged[0])));
});

test("calm after a spike restores gradually and tells Devin the next stage can be normal size", async () => {
  const { ctl, calls, logged, fire } = setup();
  await ctl.submit("build x");
  await ctl.pollNow("c1");
  fire(1, "HR 150 vs baseline 72: switching to guided answers and a slower pace");
  fire(2, "calm again: restoring level 1 to 2");
  fire(3, "calm again: restoring level 2 to 3");
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(ctl.active().difficulty, 3);
  assert.deepStrictEqual(logged.map((l) => [l.prev, l.level]), [[3, 1], [1, 2], [2, 3]]);
  assert.match(calls.filter((c) => c.meta.reason === "adapt").pop().text, /normal/i);
});

const earned1 = Object.assign(view(1), { progress: { predict: true, bug: true, write: true, explain: false }, unlocked: true, code: "c" });

test("at level 1 the continue message is held back (slow pace); at level 3 it goes at once", async () => {
  const slow = setup({ snap: snap("waiting_for_user", [earned1]) });
  await slow.ctl.submit("x");
  slow.fire(1, "HR 150 vs baseline 72: x");
  await slow.ctl.pollNow("c1");
  assert.strictEqual(slow.calls.filter((c) => c.meta.reason === "continue").length, 0, "held back");
  assert.ok(slow.timers.some((t) => t.ms >= 10000), "scheduled after a pause");
  assert.ok(slow.ctl.active().thread.some((t) => t.kind === "bridge" && /Slower pace/.test(t.text)));
  const fast = setup({ snap: snap("waiting_for_user", [earned1]) });
  await fast.ctl.submit("x");
  await fast.ctl.pollNow("c1");
  assert.strictEqual(fast.calls.filter((c) => c.meta.reason === "continue").length, 1);
});

test("a slow continue is not sent if the learner stopped the build while it waited", async () => {
  const s = setup({ snap: snap("waiting_for_user", [earned1]) });
  await s.ctl.submit("x");
  s.fire(1, "HR 150 vs baseline 72: x");
  await s.ctl.pollNow("c1");
  await s.ctl.stop("c1");
  for (const t of s.timers.slice()) await t.fn();
  assert.strictEqual(s.calls.filter((c) => c.meta.reason === "continue").length, 0);
});

test("every graded checkpoint result is reported to the engine so repeated misses can lower the difficulty", async () => {
  const s = setup({ snap: snap("waiting_for_user", [view(1)]) });
  await s.ctl.submit("x");
  await s.ctl.pollNow("c1");
  await s.ctl.grade("c1", 1, "predict", { answer: {} });
  await s.ctl.grade("c1", 1, "bug", { answer: "a" });
  assert.deepStrictEqual(s.attempts, [false, false]);
});
