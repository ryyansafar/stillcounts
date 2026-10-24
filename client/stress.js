// Stress detector and difficulty policy. Pure functions, no DOM, no clock, no network: every function takes the
// previous state and returns a NEW one. Heart rate never leaves the device; this file cannot send anything anywhere.
//
//   init(opts) -> state                         step(state, { t, bpm }) -> state          calibrate(state, t) -> state
//   initAdapt() -> a                            adapt(a, { t, stress, failures, bpm, baseline }) -> { level, reason, changed, lastChange }
//   settings(level) -> what the chat shell should do          checkpointMode(level, kind) -> { mode, autoPass:false }
//
// Detector (stress is "calm" | "elevated" | "high"):
//   baseline   median BPM of the first calibrationMs (30 s) of data, or of the last 30 s when calibrate() is called
//   abrupt     smoothed BPM is >= +15 bpm or +20% above the lowest smoothed BPM of the last 10 s       -> high
//   sustained  smoothed BPM above baseline x1.25 for 20 s straight                                      -> elevated
//   hysteresis once stressed, it only ends after BPM is back under baseline x1.10 for calmHoldMs; leaving "high" also waits cooldownMs
//   noise      BPM is smoothed with a median of the last 3 samples, so a single outlier cannot fire it; non-numeric or
//              out-of-range (30..230) samples are rejected and counted.
// Not a medical device: this is a rough study-pace heuristic.
(function (root) {
  "use strict";
  var DEFAULTS = {
    calibrationMs: 30000, minCalSamples: 5,
    abruptBpm: 15, abruptPct: 0.20, abruptWindowMs: 10000,
    sustainedRatio: 1.25, sustainedMs: 20000, sampleGapMs: 2500,
    exitRatio: 1.10, highRatio: 1.5, calmHoldMs: 10000, cooldownMs: 15000,
    smoothN: 3, historyMs: 60000, minBpm: 30, maxBpm: 230,
    restoreMs: 20000, missesForHint: 3, missesForGuided: 2
  };
  var RANK = { calm: 0, elevated: 1, high: 2 };
  var NAMES = ["calm", "elevated", "high"];

  function median(a) {
    if (!a.length) return null;
    var s = a.slice().sort(function (x, y) { return x - y; }), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function cfgOf(opts) {
    var c = {}, k;
    for (k in DEFAULTS) c[k] = DEFAULTS[k];
    for (k in (opts || {})) if (k in DEFAULTS) c[k] = opts[k];
    return c;
  }
  function assign(a, b) { var o = {}, k; for (k in a) o[k] = a[k]; for (k in b) o[k] = b[k]; return o; }

  function init(opts) {
    return { cfg: cfgOf(opts), history: [], baseline: null, calibrating: true, calStart: null, rejected: 0,
             stress: "calm", stressSince: null, calmSince: null, abrupt: false, sustained: false, bpm: null, t: null, why: "calibrating baseline" };
  }

  function validBpm(c, x) { return x && typeof x.bpm === "number" && isFinite(x.bpm) && x.bpm >= c.minBpm && x.bpm <= c.maxBpm && typeof x.t === "number" && isFinite(x.t); }

  function describe(st, sm, stress) {
    if (st.baseline === null) return "calibrating baseline";
    return "HR " + Math.round(sm) + " vs baseline " + Math.round(st.baseline) + (stress === "calm" ? ": within the normal range" : "");
  }

  function step(prev, sample) {
    var c = prev.cfg;
    if (!validBpm(c, sample)) return assign(prev, { rejected: prev.rejected + 1 });
    var t = sample.t, last = prev.history.length ? prev.history[prev.history.length - 1] : null;
    if (last && t <= last.t) return assign(prev, { rejected: prev.rejected + 1 });   // out-of-order or duplicate timestamp
    var recent = prev.history.slice(-(c.smoothN - 1)).map(function (h) { return h.bpm; }).concat([sample.bpm]);
    var sm = median(recent);
    var history = prev.history.filter(function (h) { return h.t >= t - c.historyMs; }).concat([{ t: t, bpm: sample.bpm, sm: sm }]);
    var st = assign(prev, { history: history, bpm: sm, t: t });

    if (st.baseline === null) {
      var calStart = st.calStart === null ? t : st.calStart;
      st.calStart = calStart;
      var inCal = history.filter(function (h) { return h.t >= calStart; });
      if (t - calStart >= c.calibrationMs && inCal.length >= c.minCalSamples) {
        st.baseline = median(inCal.map(function (h) { return h.bpm; }));
        st.calibrating = false;
        st.why = describe(st, sm, "calm");
      }
      return st;
    }

    // abrupt: rise against the lowest smoothed value in the recent window
    var win = history.filter(function (h) { return h.t >= t - c.abruptWindowMs; });
    var low = Math.min.apply(null, win.map(function (h) { return h.sm; }));
    var rise = sm - low;
    var abrupt = rise >= c.abruptBpm || rise / low >= c.abruptPct;

    // sustained: every smoothed sample of the last sustainedMs above baseline x ratio, and the window really spans it
    var swin = history.filter(function (h) { return h.t >= t - c.sustainedMs; });
    var limit = st.baseline * c.sustainedRatio;
    var spans = swin.length > 1 && swin[0].t <= t - c.sustainedMs + c.sampleGapMs && history[0].t <= t - c.sustainedMs + c.sampleGapMs;
    var sustained = spans && swin.every(function (h) { return h.sm > limit; });

    var above = sm > st.baseline * c.exitRatio;
    var calmSince = above ? null : (st.calmSince === null ? t : st.calmSince);
    var calmHeld = calmSince !== null && t - calmSince >= c.calmHoldMs;

    var target = abrupt || sm >= st.baseline * c.highRatio ? 2 : (sustained ? 1 : 0);
    var cur = RANK[st.stress], next = cur, stressSince = st.stressSince;
    if (target > cur) { next = target; }
    else if (target < cur) {
      var floor = above ? 1 : (calmHeld ? 0 : 1);
      var want = Math.max(target, floor);
      var cooled = cur < 2 || (stressSince !== null && t - stressSince >= c.cooldownMs);
      if (cooled && want < cur) next = want;
    }
    if (next !== cur) stressSince = t;
    st.stress = NAMES[next]; st.stressSince = stressSince; st.calmSince = calmSince;
    st.abrupt = abrupt; st.sustained = sustained;
    st.why = describe(st, sm, st.stress);
    return st;
  }

  // "calibrate baseline" button: median of the last calibrationMs of data. Refuses with too few samples.
  function calibrate(prev, t) {
    var c = prev.cfg, win = prev.history.filter(function (h) { return h.t >= t - c.calibrationMs && h.t <= t; });
    if (win.length < c.minCalSamples) return assign(prev, { baseline: null, calibrating: true, calStart: null, stress: "calm", stressSince: null, calmSince: null, abrupt: false, sustained: false, why: "not enough data to calibrate yet" });
    var base = median(win.map(function (h) { return h.bpm; }));
    return assign(prev, { baseline: base, calibrating: false, stress: "calm", stressSince: null, calmSince: null, abrupt: false, sustained: false, why: "baseline set to " + Math.round(base) });
  }

  // ---- policy: stress + recent failures -> difficulty level 3 (normal), 2 (hint + smaller steps), 1 (guided, slower, breathing) ----
  function initAdapt() { return { level: 3, lastChange: null, reason: "", changed: false }; }

  function targetLevel(stress, failures, c) {
    var f = failures || 0;
    if (stress === "high") return 1;
    if (stress === "elevated") return f >= c.missesForGuided ? 1 : 2;
    return f >= c.missesForHint ? 2 : 3;
  }

  function hrPart(i) {
    return (typeof i.bpm === "number" && typeof i.baseline === "number") ? "HR " + Math.round(i.bpm) + " vs baseline " + Math.round(i.baseline) : "heart rate is up";
  }
  function missesPart(f) { return f + " recent miss" + (f === 1 ? "" : "es"); }

  function reasonFor(level, i) {
    var f = i.failures || 0;
    if (i.stress === "elevated" || i.stress === "high") {
      var lead = hrPart(i) + (f >= 2 ? " and " + missesPart(f) : "");
      return lead + ": " + (level === 1 ? "switching to guided answers and a slower pace" : "switching to easier steps");
    }
    return missesPart(f) + ": adding a hint and smaller steps";
  }

  function adapt(prev, input, opts) {
    var c = cfgOf(opts), t = input.t, level = prev.level;
    var stress = input.stress in RANK ? input.stress : "calm";
    var tgt = targetLevel(stress, input.failures, c), i = assign(input, { stress: stress });
    var since = prev.lastChange === null ? Infinity : t - prev.lastChange;
    if (tgt < level) return { level: tgt, lastChange: t, reason: reasonFor(tgt, i), changed: true };
    if (tgt > level) {
      if (since >= c.restoreMs) {
        var nl = level + 1;
        return { level: nl, lastChange: t, reason: "calm again: restoring level " + level + " to " + nl, changed: true };
      }
      var left = Math.ceil((c.restoreMs - since) / 1000);
      return { level: level, lastChange: prev.lastChange, reason: "calm again: holding level " + level + " for " + left + " more s", changed: false };
    }
    return { level: level, lastChange: prev.lastChange, reason: prev.reason || (level === 3 ? "normal difficulty" : ""), changed: false };
  }

  // what each level asks of the chat shell. autoPass is always false: easier means easier to ANSWER, never skipped.
  function settings(level) {
    var l = level === 1 || level === 2 ? level : 3;
    return { level: l, hint: l < 3, splitSteps: l < 3, writeMode: l === 1 ? "guided" : "normal",
             paceMultiplier: l === 1 ? 2 : 1, breathing: l === 1, autoPass: false };
  }

  function checkpointMode(level, kind) {
    var l = settings(level), mode = "normal";
    if (l.level === 1 && kind === "write") mode = "guided";
    else if (l.level === 1 && kind === "explain") mode = "multiple_choice";
    return { mode: mode, hint: l.hint, splitSteps: l.splitSteps, autoPass: false };
  }

  var api = { DEFAULTS: DEFAULTS, median: median, init: init, step: step, calibrate: calibrate,
              initAdapt: initAdapt, adapt: adapt, settings: settings, checkpointMode: checkpointMode };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Stress = api;
})(typeof self !== "undefined" ? self : this);
