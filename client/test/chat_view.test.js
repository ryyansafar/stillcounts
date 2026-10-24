// The view model: what the thread shows. The important property: locked code can never reach a block.
const test = require("node:test");
const assert = require("node:assert");
const K = require("../chat_core.js");
const V = require("../chat_view.js");

const PLAN = [1, 2].map((n) => ({ n, id: "st" + n, title: "Stage " + n, summary: "s" + n }));
const view = (n, extra) => Object.assign({ n, id: "st" + n, title: "Stage " + n, status: "built", files: ["a.py"], concepts: ["c"], bug_hint: "off by one",
  predict: {}, bug: {}, write: {}, explain: {}, progress: { predict: false, bug: false, write: false, explain: false }, unlocked: false, code: null }, extra || {});
function chat() {
  let c = K.reduce(K.createChat({ id: "c1", prompt: "make a thing", now: 1, source: "live" }), { type: "session", id: "s1", url: "https://app.devin.ai/sessions/s1", live: true, acuLimit: 3 }).chat;
  c = K.reduce(c, { type: "snapshot", now: 2, snap: { status: "running", status_detail: "waiting_for_user", acu_consumed: 1, acu_limit: 3, plan: PLAN,
    messages: [{ id: "a", source: "devin", text: "l1" }, { id: "b", source: "devin", text: "l2" }],
    pull_requests: [{ url: "https://github.com/o/r/pull/1", title: "S1", state: "open", ci: "passing", stage: 1, review: { state: "commented", comments: 2 } }],
    stages: [view(1, { code: "SECRET" })] } }).chat;
  return c;
}

test("blocks follow the thread: user, plan, grouped log, stage, checkpoint, pr", () => {
  const b = V.blocks(chat(), { results: {} });
  assert.deepStrictEqual(b.map((x) => x.type), ["user", "plan", "log", "stage", "checkpoint", "pr"]);
  assert.deepStrictEqual(b.find((x) => x.type === "log").lines, ["l1", "l2"]);
  assert.strictEqual(new Set(b.map((x) => x.key)).size, b.length, "keys are unique");
});

test("a locked stage has no code anywhere in its block, only a locked flag", () => {
  const b = V.blocks(chat(), { results: {} }).find((x) => x.type === "stage");
  assert.strictEqual(b.locked, true);
  assert.ok(!JSON.stringify(b).includes("SECRET"));
  assert.strictEqual(b.diff, null);
});

test("an earned stage shows the highlighted diff and a one line unlock message", () => {
  let c = chat();
  c = K.reduce(c, { type: "checkpoint", n: 1, kind: "write", passed: true, progress: { predict: true, bug: true, write: true }, code: "a = 1\nb = 2", now: 5 }).chat;
  const b = V.blocks(c, { results: {} });
  const st = b.find((x) => x.type === "stage");
  assert.strictEqual(st.locked, false);
  assert.deepStrictEqual(st.diff.map((d) => d.t), ["add", "add"]);
  assert.ok(b.some((x) => x.type === "unlock" && x.n === 1));
});

test("the checkpoint block says which kind is current and whether the learner may work on it", () => {
  const b = V.blocks(chat(), { results: {} }).find((x) => x.type === "checkpoint");
  assert.strictEqual(b.canWork, true);
  assert.strictEqual(b.current, "predict");
  assert.deepStrictEqual(b.passed, { predict: false, bug: false, write: false, explain: false });
});

test("the block signature changes when something the card shows changes", () => {
  const c = chat();
  const s1 = V.blocks(c, { results: {} }).find((x) => x.type === "checkpoint").sig;
  const s2 = V.blocks(c, { results: { "1/predict": { cls: "bad", text: "no" } } }).find((x) => x.type === "checkpoint").sig;
  assert.notStrictEqual(s1, s2);
  assert.strictEqual(V.blocks(c, { results: {} }).find((x) => x.type === "checkpoint").sig, s1);
});

test("badge: live is labelled live, replay is labelled simulated and carries the reason", () => {
  assert.deepStrictEqual([V.badge(chat()).cls, V.badge(chat()).text], ["live", "LIVE DEVIN"]);
  const r = K.reduce(chat(), { type: "fallback", reason: "quota", now: 9 }).chat;
  const b = V.badge(r);
  assert.strictEqual(b.cls, "replay");
  assert.match(b.text, /SIMULATED/);
  assert.match(b.why, /limit/);
});

test("ACU meter shows consumed over cap and marks simulated numbers", () => {
  assert.deepStrictEqual(V.acu(chat()), { text: "ACU 1 of 3", pct: 33, simulated: false });
  const r = K.reduce(chat(), { type: "fallback", reason: "network", now: 9 }).chat;
  assert.strictEqual(V.acu(r), null, "no session yet, no meter");
});

test("CI and review chips are honest about what is known", () => {
  assert.strictEqual(V.ciLabel({ ci: "passing" }).text, "CI passing");
  assert.strictEqual(V.ciLabel({ ci: "failing" }).cls, "bad");
  assert.match(V.ciLabel({ ci: "simulated" }).text, /simulated/i);
  assert.match(V.ciLabel({}).text, /no CI/i);
  assert.strictEqual(V.reviewLabel({ review: { state: "commented", comments: 2 } }), "Devin Review: 2 comments");
  assert.match(V.reviewLabel({ review: { state: "none", comments: 0 } }), /no review/i);
  assert.match(V.reviewLabel({ simulated: true, review: {} }), /simulated/i);
});

test("only https links are ever linked", () => {
  assert.strictEqual(V.safeLink("https://github.com/x"), "https://github.com/x");
  assert.strictEqual(V.safeLink("javascript:alert(1)"), null);
  assert.strictEqual(V.safeLink("replay:pr-1"), null);
  assert.strictEqual(V.safeLink(null), null);
});

test("outcome text: a pass says so, a fail gives the hint and invites a retry", () => {
  assert.strictEqual(V.outcome("bug", { passed: true, result: {} }, {}).cls, "good");
  const f = V.outcome("write", { passed: false, result: { counterexample: { ops: [1, 2], expected: 3, got: 4 } } }, { hint: "think about ids" });
  assert.strictEqual(f.cls, "bad");
  assert.match(f.text, /2 operations/);
  assert.match(f.text, /think about ids/);
  assert.match(f.text, /try again/i);
  assert.match(V.outcome("explain", { passed: true, result: { covered: [{ point: "a" }], missed: [{ point: "b" }] } }, {}).points.join("|"), /Covered: a.*Missed: b/);
});

test("sidebar rows show the source and progress", () => {
  const row = V.sidebarRow(chat());
  assert.deepStrictEqual([row.title, row.badge, row.progress], ["make a thing", "LIVE", "0 of 2 earned"]);
});

test("a failed answer never repeats the expected value or the right option", () => {
  const p = V.outcome("predict", { passed: false, result: { cases: { p1: { passed: false, expected: 15, guess: 1 }, p2: { passed: true, expected: 16 } } } }, {});
  assert.match(p.text, /case p1 is not right/);
  assert.ok(!/15|16/.test(p.text));
  const b = V.outcome("bug", { passed: false, result: { answer: "b", explanation: "Without dedupe() every retry is added again" } }, {});
  assert.match(b.text, /not the bug/);
  assert.ok(!/dedupe|\bb\b/.test(b.text.replace(/not the bug/, "")));
});

// ---------- design review round 1: rail, relative time, level dots, mode chip, honest replay label ----------
test("stageRail: earned, then the one being worked, then locked, with a 'Stage X of N' label", () => {
  let c = chat();
  let rail = V.stageRail(c);
  assert.strictEqual(rail.label, "Stage 1 of 2");
  assert.deepStrictEqual(rail.items.map((i) => i.state), ["working", "locked"]);
  c = K.reduce(c, { type: "checkpoint", n: 1, kind: "write", passed: true, progress: { predict: true, bug: true, write: true }, code: "a = 1", now: 5 }).chat;
  rail = V.stageRail(c);
  assert.deepStrictEqual(rail.items.map((i) => i.state), ["earned", "working"]);
  assert.strictEqual(rail.label, "Stage 2 of 2");
});

test("stageRail: when every stage is earned the label says so and nothing is working", () => {
  let c = chat();
  c = K.reduce(c, { type: "checkpoint", n: 1, kind: "write", passed: true, progress: { predict: true, bug: true, write: true }, code: "a = 1", now: 5 }).chat;
  c.plan = [c.plan[0]];
  const rail = V.stageRail(c);
  assert.strictEqual(rail.label, "All stages earned");
  assert.ok(rail.items.every((i) => i.state === "earned"));
});

test("stageRail of an empty plan is empty, never a crash", () => {
  const c = K.createChat({ id: "x", prompt: "p", now: 1 });
  assert.deepStrictEqual(V.stageRail(c), { label: "Planning", items: [], current: 0 });
});

test("relTime: coarse, never in the future, tolerant of garbage", () => {
  assert.strictEqual(V.relTime(1000, 1000), "just now");
  assert.strictEqual(V.relTime(0, 5 * 60000), "5m ago");
  assert.strictEqual(V.relTime(0, 3 * 3600000), "3h ago");
  assert.strictEqual(V.relTime(0, 2 * 86400000), "2d ago");
  assert.strictEqual(V.relTime(5000, 1000), "just now");
  assert.strictEqual(V.relTime(undefined, 1000), "");
});

test("levelDots: difficulty 1..3 maps to filled dots, clamped", () => {
  assert.deepStrictEqual(V.levelDots(3), [true, true, true]);
  assert.deepStrictEqual(V.levelDots(1), [true, false, false]);
  assert.deepStrictEqual(V.levelDots(0), [true, false, false]);
  assert.deepStrictEqual(V.levelDots(9), [true, true, true]);
});

test("courseChip: matches the built-in ledger course by topic, otherwise says it is generated, and is honest about replay", () => {
  assert.match(V.courseChip("build an offline inventory ledger", "live").text, /Field ledger/);
  assert.strictEqual(V.courseChip("build an offline inventory ledger", "live").matched, true);
  const other = V.courseChip("a tip calculator", "live");
  assert.strictEqual(other.matched, false);
  assert.match(other.text, /generated/i);
  assert.match(V.courseChip("a tip calculator", "replay").text, /built-in demo/i);
  assert.strictEqual(V.courseChip("", "live").text, "Course: chosen after you send");
});

test("replayLabel: a replay is never called a real session", () => {
  assert.match(V.replayLabel({ source: "replay" }), /simulated/i);
  assert.ok(!/replayed from a real/i.test(V.replayLabel({ source: "replay" })));
  assert.match(V.replayLabel({ source: "replay" }), /not a real/i);
  assert.match(V.replayLabel({ source: "live" }), /live/i);
});

test("sidebarRow carries a relative time", () => {
  const c = chat();
  c.updatedAt = 0;
  assert.strictEqual(V.sidebarRow(c, 2 * 3600000).when, "2h ago");
});
