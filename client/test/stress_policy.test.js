const test = require("node:test");
const assert = require("node:assert");
const S = require("../stress.js");

const start = () => S.initAdapt();
const at = (st, t, stress, failures = 0, bpm = 72, baseline = 72) => S.adapt(st, { t: t * 1000, stress, failures, bpm, baseline });

test("calm with no failures is level 3 and says nothing", () => {
  const a = at(start(), 1, "calm");
  assert.strictEqual(a.level, 3);
  assert.strictEqual(a.changed, false);
});

test("elevated -> level 2 with an explanation containing the HR numbers", () => {
  const a = at(start(), 1, "elevated", 0, 112, 72);
  assert.strictEqual(a.level, 2);
  assert.strictEqual(a.reason, "HR 112 vs baseline 72: switching to easier steps");
});

test("high -> level 1; elevated plus repeated misses -> level 1", () => {
  assert.strictEqual(at(start(), 1, "high", 0, 120, 72).level, 1);
  assert.strictEqual(at(start(), 1, "elevated", 2, 100, 72).level, 1);
});

test("calm but 3 recent misses -> level 2 and the reason names the misses, not the heart", () => {
  const a = at(start(), 1, "calm", 3);
  assert.strictEqual(a.level, 2);
  assert.match(a.reason, /3 recent misses/);
  assert.doesNotMatch(a.reason, /HR/);
});

test("stepping down is immediate, restoring is one level per restoreMs of calm", () => {
  let a = at(start(), 10, "high", 0, 125, 72);
  assert.strictEqual(a.level, 1);
  a = at(a, 20, "calm");          // too soon
  assert.strictEqual(a.level, 1);
  assert.match(a.reason, /holding/);
  a = at(a, 10 + S.DEFAULTS.restoreMs / 1000, "calm");
  assert.strictEqual(a.level, 2);
  assert.match(a.reason, /restoring/);
  a = at(a, 11 + S.DEFAULTS.restoreMs / 1000, "calm");
  assert.strictEqual(a.level, 2);
  a = at(a, 10 + 2 * S.DEFAULTS.restoreMs / 1000, "calm");
  assert.strictEqual(a.level, 3);
});

test("a new spike during restoration drops straight back down", () => {
  let a = at(start(), 0, "high", 0, 120, 72);
  a = at(a, 25, "calm");
  assert.strictEqual(a.level, 2);
  a = at(a, 26, "high", 0, 130, 72);
  assert.strictEqual(a.level, 1);
});

test("settings per level: hint+split at 2, guided/MC + slower + breathing at 1, level 3 untouched", () => {
  const s3 = S.settings(3), s2 = S.settings(2), s1 = S.settings(1);
  assert.deepStrictEqual([s3.hint, s3.splitSteps, s3.writeMode, s3.breathing, s3.paceMultiplier], [false, false, "normal", false, 1]);
  assert.deepStrictEqual([s2.hint, s2.splitSteps, s2.writeMode, s2.breathing], [true, true, "normal", false]);
  assert.strictEqual(s1.writeMode, "guided");
  assert.ok(s1.breathing && s1.hint && s1.splitSteps);
  assert.ok(s1.paceMultiplier > s2.paceMultiplier && s2.paceMultiplier >= 1);
});

test("NEVER auto-passes: at every level and every checkpoint kind", () => {
  [1, 2, 3].forEach((lvl) => {
    assert.strictEqual(S.settings(lvl).autoPass, false);
    ["predict", "bug", "write", "explain"].forEach((k) => {
      const m = S.checkpointMode(lvl, k);
      assert.strictEqual(m.autoPass, false);
      assert.ok(["normal", "multiple_choice", "guided"].includes(m.mode));
      assert.notStrictEqual(m.mode, "passed");
    });
  });
  assert.strictEqual(S.checkpointMode(1, "write").mode, "guided");
  assert.strictEqual(S.checkpointMode(1, "explain").mode, "multiple_choice");
  assert.strictEqual(S.checkpointMode(3, "write").mode, "normal");
});

test("unknown stress or missing numbers fall back to level 3 / a plain reason", () => {
  assert.strictEqual(at(start(), 1, "weird").level, 3);
  const a = S.adapt(start(), { t: 1000, stress: "high", failures: 0 });
  assert.strictEqual(a.level, 1);
  assert.doesNotMatch(a.reason, /undefined|NaN/);
});
