const test = require("node:test");
const assert = require("node:assert");
const C = require("../crdt.js");
const L = require("../learn.js");
const U = require("../understand.js");

const CODE = 'def _total(op):\n    return op\n\n\ndef dedupe(ops):\n    best = {}\n    for op in ops:\n        best[op.id] = op\n    return list(best)\n';
const stage = (over) => Object.assign({ id: "s1", earned: false, code: null, checkpoints: {
  predict: { cases: [{ id: "p1", item: "x", field: "qty", ops: [
    { id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "x", value: 10 },
    { id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "x", value: 10 },
    { id: "b:1", replica: "b", lamport: 1, kind: "adjust", item: "x", value: -4 }] }] },
  bug: {}, write: { function: "dedupe", stub: 'def dedupe(ops):\n    """Doc."""\n    raise NotImplementedError\n' }, explain: {} }, order: ["predict", "bug", "write", "explain"] }, over);

test("level is the highest checkpoint passed", () => {
  const s = stage();
  assert.strictEqual(U.levelOf(s, {}), "unseen");
  assert.strictEqual(U.levelOf(s, { "s1/predict": true }), "predicted");
  assert.strictEqual(U.levelOf(s, { "s1/predict": true, "s1/bug": true }), "bug");
  assert.strictEqual(U.levelOf(s, { "s1/predict": true, "s1/bug": true, "s1/write": true }), "written");
  assert.strictEqual(U.levelOf(s, { "s1/explain": true }), "explained");
});

test("only the function you wrote reaches the written level; its neighbours stay at bug spotted", () => {
  const passed = { "s1/predict": true, "s1/bug": true, "s1/write": true };
  const lines = U.lineStates(stage(), passed, CODE);
  assert.strictEqual(lines[0].level, "bug");           // def _total
  assert.strictEqual(lines[2].level, "blank");
  assert.strictEqual(lines[4].level, "written");       // def dedupe
  assert.strictEqual(lines[8].level, "written");
  assert.strictEqual(U.functionRange(CODE, "dedupe").start, 4);
  assert.strictEqual(U.functionRange(CODE, "nope"), null);
});

test("explaining raises every line to explained", () => {
  const passed = { "s1/predict": true, "s1/bug": true, "s1/write": true, "s1/explain": true };
  const levels = U.lineStates(stage(), passed, CODE).filter((l) => l.level !== "blank").map((l) => l.level);
  assert.ok(levels.every((l) => l === "explained"));
});

test("gap score: nothing revealed is null, written-only code leaves a gap, explaining closes it", () => {
  const s = stage();
  assert.strictEqual(U.gapScore([s], {}, {}).gap, null);
  assert.strictEqual(U.gapScore([s], {}, {}).locked, 1);
  const earned = { "s1/predict": true, "s1/bug": true, "s1/write": true };
  const g = U.gapScore([s], earned, { s1: CODE });
  assert.ok(g.gap > 0 && g.gap < 100 && g.understood + g.gap === 100 && g.lines === 7);
  const full = U.gapScore([s], Object.assign({ "s1/explain": true }, earned), { s1: CODE });
  assert.strictEqual(full.gap, 0);
});

test("measured results: first-try rate, hints, lines unlocked versus written", () => {
  const s = stage();
  const st = { passed: { "s1/predict": true, "s1/bug": true, "s1/write": true }, codes: { s1: CODE },
    drafts: { "s1/write": 'def dedupe(ops):\n    # keep one\n    best = {}\n\n    return list(best)\n' },
    attempts: { "s1/predict": { tries: 1, firstTry: true }, "s1/bug": { tries: 2, firstTry: false }, "s1/write": { tries: 3, firstTry: false }, "s1/explain": { tries: 1, firstTry: true } },
    hints: { "s1/predict": 2, "s1/bug": 1 } };
  const m = U.measured([s], st);
  assert.strictEqual(m.firstTryPct, 33);        // explain is not a pass/fail checkpoint
  assert.strictEqual(m.attempts, 7);
  assert.strictEqual(m.hints, 3);
  assert.strictEqual(m.unlockedLines, 7);
  assert.strictEqual(m.writtenLines, 3);        // blank and comment lines do not count
  assert.strictEqual(m.stagesEarned, 1);
  assert.strictEqual(U.measured([s], { passed: {}, codes: {}, drafts: {}, attempts: {}, hints: {} }).firstTryPct, null);
});

test("quiz variants change the numbers and are graded by the real merge", () => {
  const s = stage();
  const before = U.buildQuiz([s], "before"), after = U.buildQuiz([s], "after");
  assert.strictEqual(before.length, 1);
  const b = before[0], a = after[0];
  assert.notDeepStrictEqual(b.ops, a.ops);
  assert.strictEqual(L.predictAnswer(b), 13 - 7);   // duplicate id counted once: +13 and -7
  assert.strictEqual(L.predictAnswer(a), 16 - 10);
  assert.strictEqual(L.gradePredict(b, "6"), true);
  assert.ok(b.ops[0].id === b.ops[1].id && C.merge(b.ops).x.qty === 6);   // retries stay identical after the change
  assert.ok(!/blank/i.test(b.question));
});

test("line diff marks what you lack and what you added", () => {
  const d = U.diffLines("a\nb\nc", "a\nc\nd");
  assert.deepStrictEqual(d.map((x) => x.t + ":" + x.text), ["same:a", "del:b", "same:c", "add:d"]);
  assert.deepStrictEqual(U.diffLines("", "").map((x) => x.t), ["same"]);
  assert.strictEqual(U.functionSource(CODE, "dedupe").split("\n").length, 5);
});

test("proof card carries only what was measured and says it is self-reported", () => {
  const s = stage();
  const st = { passed: { "s1/predict": true, "s1/bug": true, "s1/write": true }, codes: { s1: CODE }, drafts: {}, attempts: {}, hints: {},
    quiz: { before: { score: 2, total: 8 }, after: { score: 6, total: 8 } } };
  const f = U.proofFacts([s], st, "  Ryy <b> ");
  const text = U.proofText(f), svg = U.proofSvg(f);
  assert.match(text, /Earned 1 of 1 stages/);
  assert.match(text, /2\/8 before, 6\/8 after/);
  assert.match(text, /self-reported/);
  assert.ok(svg.startsWith("<svg") && svg.includes("&lt;b&gt;") && !svg.includes("<b>"));
});
