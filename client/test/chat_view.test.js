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
