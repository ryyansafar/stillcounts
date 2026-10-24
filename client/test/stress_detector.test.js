const test = require("node:test");
const assert = require("node:assert");
const S = require("../stress.js");

// deterministic noise
function rng(seed) { return () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296); }
// series(fn(tSec), seconds) -> samples at 1 Hz starting t=0 (ms)
function series(fn, seconds, from = 0) {
  const out = [];
  for (let s = 0; s < seconds; s++) out.push({ t: (from + s) * 1000, bpm: fn(from + s) });
  return out;
}
function run(samples, opts, state) {
  let st = state || S.init(opts), trace = [];
  samples.forEach((x) => { st = S.step(st, x); trace.push(st); });
  return { st, trace };
}
const calib = (r, bpm = 72, secs = 31) => series(() => bpm + (r() - 0.5) * 4, secs);

test("calibration: baseline is the median of the first 30 s, nothing fires before it exists", () => {
  const r = rng(1);
  const early = run(calib(r, 72, 20));
  assert.strictEqual(early.st.baseline, null);
  assert.strictEqual(early.st.stress, "calm");
  const done = run(calib(rng(1), 72, 31));
  assert.ok(Math.abs(done.st.baseline - 72) <= 2, "baseline " + done.st.baseline);
});

test("a spike during calibration cannot raise an alarm (no baseline yet)", () => {
  const { trace } = run(series((s) => (s < 10 ? 70 : 120), 25));
  assert.ok(trace.every((x) => x.stress === "calm"));
});

test("sudden spike: 72 -> 112 in 3 s is flagged high within 4 s", () => {
  const r = rng(2), pre = calib(r);
  const spike = series((s) => (s < 35 ? 72 : s < 38 ? 72 + (s - 34) * 13.3 : 112), 20, 31);
  const { trace } = run(pre.concat(spike));
  const first = trace.findIndex((x) => x.stress === "high");
  assert.ok(first >= 0, "never flagged");
  assert.ok(trace[first].t - 34000 <= 4000, "too slow: " + (trace[first].t - 34000));
  assert.ok(trace[first].abrupt);
  assert.match(trace[first].why, /HR \d+ vs baseline 7\d/);
});

test("slow drift 72 -> 88 over 3 minutes never counts as an abrupt rise or stress", () => {
  const r = rng(3), pre = calib(r);
  const drift = series((s) => 72 + (s - 31) * 0.09 + (r() - 0.5) * 2, 180, 31);
  const { trace } = run(pre.concat(drift));
  assert.ok(trace.every((x) => !x.abrupt));
  assert.ok(trace.every((x) => x.stress === "calm"));
});

test("slow drift well past x1.25 is eventually 'elevated' (sustained), never 'abrupt'", () => {
  const r = rng(4), pre = calib(r);
  const drift = series((s) => 72 + (s - 31) * 0.3, 100, 31);
  const { trace } = run(pre.concat(drift));
  assert.ok(trace.every((x) => !x.abrupt));
  assert.ok(trace.some((x) => x.stress === "elevated"));
  assert.ok(trace.every((x) => x.stress !== "high"));
});

test("noisy baseline with single-sample outliers does not trigger", () => {
  const r = rng(5), pre = calib(r);
  const noisy = series((s) => 72 + (r() - 0.5) * 12 + (s % 17 === 0 ? 40 : 0), 120, 31);
  const { trace } = run(pre.concat(noisy));
  assert.ok(trace.every((x) => x.stress === "calm"));
});

test("sustained: above baseline x1.25 for 20 s is elevated, 15 s is not", () => {
  const r = rng(6), pre = calib(r);
  // ramp slowly to avoid abrupt: 72 -> 95 over 40 s, then hold
  const hold = (n) => pre.concat(series((s) => Math.min(95, 72 + (s - 31) * 0.6), n, 31));
  const short = run(hold(42)); // reaches >90 around t=62; held ~10 s
  assert.strictEqual(short.st.stress, "calm");
  const long = run(hold(110));
  assert.strictEqual(long.st.stress, "elevated");
  assert.ok(long.st.sustained && !long.st.abrupt);
});

test("flapping around the threshold does not flap the state", () => {
  const r = rng(7), pre = calib(r);
  // up to elevated first
  const up = series((s) => Math.min(100, 72 + (s - 31) * 0.8), 70, 31);
  const base = run(pre.concat(up));
  assert.strictEqual(base.st.stress, "elevated");
  // flap between 86 and 94 every 3 s (inside the hysteresis band 79..90 / 90+)
  const flap = series((s) => (Math.floor(s / 3) % 2 ? 94 : 86), 120, 101);
  const { trace } = run(flap, null, base.st);
  const changes = trace.filter((x, i) => i && x.stress !== trace[i - 1].stress).length;
  assert.ok(changes <= 1, "changes: " + changes);
  assert.strictEqual(trace[trace.length - 1].stress, "elevated");
});

test("flapping from calm never reaches sustained", () => {
  const r = rng(8), pre = calib(r);
  const flap = series((s) => (Math.floor(s / 5) % 2 ? 96 : 76), 120, 31);
  const { trace } = run(pre.concat(flap));
  assert.ok(trace.every((x) => !x.sustained));
});

test("cooldown: stress is held for at least cooldownMs after it fires", () => {
  const r = rng(9), pre = calib(r);
  const s1 = series((s) => (s < 35 ? 72 : 118), 10, 31);
  const s2 = series(() => 72, 60, 41);
  const { trace } = run(pre.concat(s1, s2));
  const i = trace.findIndex((x) => x.stress === "high"), j = trace.findIndex((x, k) => k > i && x.stress !== "high");
  assert.ok(j > i);
  assert.ok(trace[j].t - trace[i].t >= S.DEFAULTS.cooldownMs);
});

test("recovery goes high -> elevated/calm only after calm is held", () => {
  const r = rng(10), pre = calib(r);
  const s = series((t) => (t < 35 ? 72 : t < 50 ? 115 : 72), 90, 31);
  const { trace } = run(pre.concat(s));
  assert.strictEqual(trace[trace.length - 1].stress, "calm");
  const hi = trace.filter((x) => x.stress === "high").length;
  assert.ok(hi >= 12 && hi < 40);
});

test("calibrate button: baseline = median of the last 30 s; too few samples is refused", () => {
  const few = S.calibrate(run(series(() => 80, 2)).st, 2000);
  assert.strictEqual(few.baseline, null);
  assert.ok(few.calibrating);
  const st = run(series((s) => (s < 20 ? 60 : 90), 60)).st; // fresh baseline from the last 30 s only
  const cal = S.calibrate(st, 59000);
  assert.ok(cal.baseline >= 88 && cal.baseline <= 91, String(cal.baseline));
  assert.strictEqual(cal.stress, "calm");
});

test("invalid samples (NaN, 0, 300, strings) are ignored, not treated as stress", () => {
  const r = rng(11), pre = calib(r);
  let st = run(pre).st;
  [{ t: 40000, bpm: NaN }, { t: 41000, bpm: 0 }, { t: 42000, bpm: 300 }, { t: 43000, bpm: "x" }, { t: 44000 }].forEach((x) => { st = S.step(st, x); });
  assert.strictEqual(st.stress, "calm");
  assert.ok(st.rejected >= 5);
});

test("step does not mutate the previous state", () => {
  const st = run(calib(rng(12))).st, snap = JSON.stringify(st);
  S.step(st, { t: 50000, bpm: 130 });
  assert.strictEqual(JSON.stringify(st), snap);
});

test("a stalled sensor (gap in time) does not create a fake abrupt rise", () => {
  const r = rng(13), pre = calib(r);
  const st = run(pre).st;
  const after = S.step(S.step(S.step(st, { t: 120000, bpm: 74 }), { t: 121000, bpm: 75 }), { t: 122000, bpm: 74 });
  assert.strictEqual(after.stress, "calm");
});
