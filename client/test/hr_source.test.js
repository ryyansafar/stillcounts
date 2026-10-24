const test = require("node:test");
const assert = require("node:assert");
const HR = require("../hr.js");
const W = require("../hr_widget.js");

const dv = (bytes) => new DataView(new Uint8Array(bytes).buffer);

test("parses the Heart Rate Measurement characteristic (8 and 16 bit)", () => {
  assert.strictEqual(HR.parseHeartRate(dv([0x00, 72])), 72);
  assert.strictEqual(HR.parseHeartRate(dv([0x01, 0x2c, 0x01])), 300);
  assert.strictEqual(HR.parseHeartRate(dv([0x16, 88, 0, 0])), 88);
  assert.strictEqual(HR.parseHeartRate(dv([0x00])), null);
});

test("Web Bluetooth is feature-detected, and absent means 'unavailable'", () => {
  assert.strictEqual(HR.bleSupported({}), false);
  assert.strictEqual(HR.bleSupported({ bluetooth: { requestDevice() {} } }), true);
  assert.match(HR.BLE_NOTE, /untested/i);
});

test("simulator follows the slider with slew and small noise, and is labelled simulated", () => {
  const sim = HR.createSimulator({ rng: () => 0.5, rest: 72 });
  assert.strictEqual(sim.label, "SIMULATOR");
  let s;
  for (let i = 0; i < 3; i++) s = sim.tick(1000);
  assert.ok(Math.abs(s.bpm - 72) <= 1);
  sim.setTarget(150);
  const first = sim.tick(1000).bpm;
  assert.ok(first > 72 && first < 150, "slewed: " + first);
  for (let i = 0; i < 40; i++) s = sim.tick(1000);
  assert.ok(Math.abs(s.bpm - 150) <= 1);
});

test("spike jumps +35 fast; calm returns to rest slowly", () => {
  const sim = HR.createSimulator({ rng: () => 0.5, rest: 72 });
  sim.tick(1000);
  sim.spike();
  let b = 0; for (let i = 0; i < 5; i++) b = sim.tick(1000).bpm;
  assert.ok(b >= 105);
  sim.calm();
  const c1 = sim.tick(1000).bpm;
  assert.ok(c1 < b && c1 > 80);
});

test("slider is clamped to 40..190", () => {
  const sim = HR.createSimulator({ rng: () => 0.5 });
  sim.setTarget(10); assert.strictEqual(sim.target(), 40);
  sim.setTarget(999); assert.strictEqual(sim.target(), 190);
});

test("controller: calibrates, adapts, calls onAdapt only on level change, restores gradually", () => {
  let t = 0;
  const c = W.createController({ now: () => t });
  const seen = [];
  c.hooks.onAdapt((e) => seen.push(e));
  assert.strictEqual(c.hooks.getDifficulty(), 3);
  const feed = (secs, bpm) => { for (let i = 0; i < secs; i++) { t += 1000; c.push({ t, bpm: typeof bpm === "function" ? bpm(i) : bpm }); } };
  feed(31, 72);
  assert.strictEqual(seen.length, 0);
  feed(4, (i) => 72 + (i + 1) * 12);
  assert.strictEqual(c.hooks.getDifficulty(), 1);
  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].level, 1);
  assert.strictEqual(seen[0].prevLevel, 3);
  assert.match(seen[0].reason, /^HR \d+ vs baseline 72: /);
  feed(120, 72);
  assert.strictEqual(c.hooks.getDifficulty(), 3);
  assert.deepStrictEqual(seen.map((e) => e.level), [1, 2, 3]);
});

test("controller: reported misses lower difficulty, successes clear them", () => {
  let t = 0;
  const c = W.createController({ now: () => t });
  c.push({ t: (t += 1000), bpm: 72 });
  c.hooks.reportAttempt(false); c.hooks.reportAttempt(false); c.hooks.reportAttempt(false);
  c.push({ t: (t += 1000), bpm: 72 });
  assert.strictEqual(c.hooks.getDifficulty(), 2);
  assert.match(c.hooks.getAdaptation().reason, /3 recent misses/);
});

test("controller never emits heart-rate data anywhere but its local log (no network calls)", () => {
  const src = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "hr_widget.js"), "utf8")
    + require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "hr.js"), "utf8")
    + require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "stress.js"), "utf8");
  assert.doesNotMatch(src, /fetch\(|XMLHttpRequest|sendBeacon|WebSocket/);
});

test("adaptation log keeps events with timestamp, level and reason", () => {
  let t = 0;
  const store = {}; const storage = { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } };
  const c = W.createController({ now: () => t, storage });
  for (let i = 0; i < 31; i++) { t += 1000; c.push({ t, bpm: 72 }); }
  for (let i = 0; i < 4; i++) { t += 1000; c.push({ t, bpm: 72 + (i + 1) * 12 }); }
  const log = c.hooks.getAdaptLog();
  assert.strictEqual(log.length, 1);
  assert.ok(log[0].reason && log[0].level === 1 && log[0].at);
  assert.ok(JSON.parse(store["sc_hr_log"]).length === 1);
});

test("sparkline points scale into the box", () => {
  const p = W.sparkPoints([60, 120, 90], 100, 20);
  assert.strictEqual(p.split(" ").length, 3);
  assert.strictEqual(W.sparkPoints([], 100, 20), "");
});
