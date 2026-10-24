// DevinLive: the live status model, the difficulty profiles and the easier Write variants.
const test = require("node:test");
const assert = require("node:assert");
const K = require("../chat_core.js");
const L = require("../devin_live.js");

const STUB = 'def total(xs):\n    """Return the sum of xs.\n\n    xs is a list of ints. Skip nothing. Return 0 for an empty list.\n    """\n    raise NotImplementedError\n';

test("levels: 3 is normal, 2 adds hint and smaller steps, 1 adds guided write, slower Devin and breathing; nothing ever auto-passes", () => {
  const p3 = L.levelProfile(3), p2 = L.levelProfile(2), p1 = L.levelProfile(1);
  assert.deepStrictEqual([p3.hint, p3.steps, p3.writeMode, p3.breathing, p3.paceMs], [false, false, "normal", false, 0]);
  assert.deepStrictEqual([p2.hint, p2.steps, p2.oneCaseAtATime, p2.writeMode, p2.breathing, p2.paceMs], [true, true, true, "normal", false, 0]);
  assert.deepStrictEqual([p1.hint, p1.steps, p1.writeMode, p1.breathing, p1.starters], [true, true, "guided", true, true]);
  assert.ok(p1.paceMs > 0);
  [1, 2, 3, 99, undefined].forEach((l) => assert.strictEqual(L.levelProfile(l).autoPass, false));
  assert.strictEqual(L.levelProfile(99).level, 3, "nonsense falls back to normal");
});

test("smaller steps come from the task's own docstring, not invented answers", () => {
  const steps = L.stepsFromStub(STUB);
  assert.deepStrictEqual(steps, ["Return the sum of xs.", "xs is a list of ints.", "Skip nothing.", "Return 0 for an empty list."]);
  assert.deepStrictEqual(L.stepsFromStub("no docstring"), []);
});

test("guided fill-in swaps the placeholder for one commented line per step and keeps the signature", () => {
  const sk = L.guidedSkeleton(STUB, ["add them", "empty is 0"]);
  assert.match(sk, /^def total\(xs\):/);
  assert.match(sk, /# step 1: add them/);
  assert.match(sk, /# step 2: empty is 0/);
  assert.ok(!/NotImplementedError/.test(sk));
});

test("a multiple-choice pick becomes real source that goes to the same grader", () => {
  const src = L.assembleChoice(STUB, "    return sum(xs)\n");
  assert.match(src, /return sum\(xs\)/);
  assert.ok(!/NotImplementedError/.test(src));
  assert.match(src, /^def total\(xs\):/);
});

test("adaptation events for the dashboard carry the level change and a reason with no heart rate in it", () => {
  const chat = K.createChat({ id: "c9", prompt: "x", now: 1 });
  const p = L.adaptPayload({ level: 1, prevLevel: 3, reason: "HR 112 vs baseline 72: switching to guided answers and a slower pace" }, chat);
  assert.deepStrictEqual([p.level, p.prev, p.kind], [1, 3, "stress"]);
  assert.ok(!/\d/.test(p.reason.replace(/^chat-/, "")), p.reason);
  assert.ok(!/112|72|bpm/i.test(JSON.stringify(p)));
  const up = L.adaptPayload({ level: 2, prevLevel: 1, reason: "calm again: restoring level 1 to 2" }, chat);
  assert.strictEqual(up.kind, "calm");
  const miss = L.adaptPayload({ level: 2, prevLevel: 3, reason: "3 recent misses: adding a hint and smaller steps" }, chat);
  assert.strictEqual(miss.kind, "misses");
  assert.ok(!/\d/.test(miss.reason));
});

test("status view: phase, raw status_detail, ACU of cap, link, PR and CI counts, Devin's last words, who waits for whom", () => {
  let c = K.createChat({ id: "c1", prompt: "x", now: 1 });
  assert.strictEqual(L.statusView(c), null, "no session yet");
  const snap = { status: "running", status_detail: "waiting_for_user", acu_consumed: 0.7, acu_limit: 3, url: "https://app.devin.ai/sessions/s1",
    plan: [{ n: 1, title: "A", summary: "" }, { n: 2, title: "B", summary: "" }], messages: [{ id: "m1", source: "devin", text: "Stage 1 done" }],
    pull_requests: [{ url: "https://github.com/o/r/pull/1", title: "s1", state: "open", ci: "passing", review: { state: "reviewed", comments: 2 } }],
    stages: [{ n: 1, id: "s1", title: "A", status: "built", progress: {}, predict: {}, bug: {}, write: {}, explain: {} }] };
  c = K.reduce(K.reduce(c, { type: "session", id: "s1", url: snap.url, live: true, acuLimit: 3, now: 2 }).chat, { type: "snapshot", snap, now: 3 }).chat;
  const v = L.statusView(c);
  assert.strictEqual(v.phase, "waiting");
  assert.strictEqual(v.detail, "waiting_for_user");
  assert.deepStrictEqual(v.acu, { used: 0.7, cap: 3 });
  assert.strictEqual(v.link, "https://app.devin.ai/sessions/s1");
  assert.deepStrictEqual([v.prs.total, v.prs.passing, v.prs.reviewComments], [1, 1, 2]);
  assert.strictEqual(v.last, "Stage 1 done");
  assert.strictEqual(v.gate.kind, "waiting_for_learner");
  assert.ok(!/^Simulated/.test(v.text));
});

test("a replay is labelled simulated in the status text, and a javascript: link is never offered", () => {
  let c = K.createChat({ id: "c2", prompt: "x", now: 1, source: "replay" });
  c = K.reduce(c, { type: "session", id: "r1", url: null, live: false, acuLimit: 3, now: 2 }).chat;
  assert.match(L.statusView(c).text, /^Simulated\./);
  c.session.url = "javascript:alert(1)";
  assert.strictEqual(L.statusView(c).link, null);
});

test("the live bridge client is the server bridge: it never sends or keeps a token", () => {
  const calls = [];
  const bridge = L.create({ fetch: async (url, init) => { calls.push([url, init]); return { ok: true, json: async () => ({ session_id: "s", url: null }) }; } });
  return bridge.start({ prompt: "hi", difficulty: 3 }).then(() => {
    assert.strictEqual(calls[0][0], "/api/devin/sessions");
    assert.ok(!/bearer|authorization|devin_token/i.test(JSON.stringify(calls[0][1])));
  });
});
