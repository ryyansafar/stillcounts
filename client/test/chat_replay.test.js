// The offline replay is a simulated Devin. It must obey the same gate as the real one: it stops after every stage and
// only builds the next one when it receives the bridge's continue message.
const test = require("node:test");
const assert = require("node:assert");
const R = require("../chat_replay.js");

const VIEWS = [1, 2, 3].map((n) => ({ id: "s" + n, n, title: "Stage " + n, summary: "sum " + n, predict: {}, bug: {}, write: {}, explain: {},
  progress: { predict: false, bug: false, write: false, explain: false }, unlocked: false, code: null }));
function make() {
  let t = 1000;
  const r = R.createReplay({ views: VIEWS, now: () => t, stepMs: 100 });
  return { r, tick: (ms) => { t += ms; } };
}

test("start returns a clearly simulated session with no link", () => {
  const { r } = make();
  const s = r.start("build a todo cli");
  assert.strictEqual(s.live, false);
  assert.strictEqual(s.url, null);
  assert.match(s.id, /^replay-/);
  const snap = r.poll();
  assert.strictEqual(snap.simulated, true);
  assert.strictEqual(snap.plan.length, 3);
  assert.strictEqual(snap.status_detail, "working");
  assert.deepStrictEqual(snap.stages, []);
});

test("it builds stage 1, logs lines labelled simulated, then stops waiting_for_user", () => {
  const { r, tick } = make();
  r.start("x");
  tick(250);
  const mid = r.poll();
  assert.ok(mid.messages.length >= 2 && mid.messages.every((m) => /simulated/i.test(m.text)));
  assert.strictEqual(mid.status_detail, "working");
  tick(2000);
  const done = r.poll();
  assert.strictEqual(done.status_detail, "waiting_for_user");
  assert.deepStrictEqual(done.stages.map((s) => [s.n, s.status]), [[1, "built"]]);
  assert.strictEqual(done.pull_requests.length, 1);
  assert.strictEqual(done.pull_requests[0].simulated, true);
  assert.strictEqual(done.pull_requests[0].ci, "simulated");
});

test("without a continue message it never starts the next stage, however long you wait", () => {
  const { r, tick } = make();
  r.start("x"); tick(5000); r.poll(); tick(60000);
  const snap = r.poll();
  assert.strictEqual(snap.status_detail, "waiting_for_user");
  assert.strictEqual(snap.stages.length, 1);
});

test("a continue message starts the next stage; the last stage ends finished", () => {
  const { r, tick } = make();
  r.start("x"); tick(5000); r.poll();
  r.message("Continue with stage 2", { reason: "continue", n: 2 });
  assert.strictEqual(r.poll().status_detail, "working");
  tick(5000);
  assert.strictEqual(r.poll().stages.length, 2);
  r.message("Continue with stage 3", { reason: "continue", n: 3 }); tick(5000);
  const end = r.poll();
  assert.strictEqual(end.status, "exit");
  assert.strictEqual(end.status_detail, "finished");
  assert.strictEqual(end.stages.length, 3);
});

test("a continue while Devin is still working is ignored", () => {
  const { r, tick } = make();
  r.start("x"); tick(100);
  r.message("Continue", { reason: "continue", n: 2 }); tick(5000);
  assert.strictEqual(r.poll().stages.length, 1);
});

test("the stressed message makes the next stage visibly smaller (simulated)", () => {
  const { r, tick } = make();
  r.start("x"); tick(5000); r.poll();
  r.message("The learner is stressed, make the next stage smaller and add comments.", { reason: "adapt" });
  r.message("go", { reason: "continue", n: 2 }); tick(5000);
  const snap = r.poll();
  assert.ok(snap.messages.some((m) => /smaller/i.test(m.text) && /simulated/i.test(m.text)));
});

test("stop suspends it for good", () => {
  const { r, tick } = make();
  r.start("x"); r.stop(); tick(9000);
  const snap = r.poll();
  assert.strictEqual(snap.status, "suspended");
  r.message("go", { reason: "continue", n: 2 }); tick(9000);
  assert.strictEqual(r.poll().stages.length, 0);
});

test("the replay state survives a reload and picks up where it was", () => {
  const { r, tick } = make();
  r.start("x"); tick(5000); r.poll();
  const saved = JSON.parse(JSON.stringify(r.state()));
  const again = R.createReplay({ views: VIEWS, now: () => 99999, stepMs: 100, state: saved });
  const snap = again.poll();
  assert.strictEqual(snap.status_detail, "waiting_for_user");
  assert.strictEqual(snap.stages.length, 1);
});

test("replay snapshots never carry code unless the view itself was unlocked", () => {
  const { r, tick } = make();
  r.start("x"); tick(5000);
  assert.ok(r.poll().stages.every((s) => s.code === null));
});

test("simulated ACU grows with the work and stays under the displayed cap", () => {
  const { r, tick } = make();
  r.start("x"); tick(5000);
  const s = r.poll();
  assert.ok(s.acu_consumed > 0 && s.acu_consumed < s.acu_limit);
});
