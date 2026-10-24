// Top-bar heart-rate widget + the controller that wires sensor -> detector -> policy -> window.EarnHooks.
// createController() has no DOM and is what the tests drive; mount() builds the widget around it.
//
// window.EarnHooks (the HR side, merged into whatever the chat shell already put there):
//   getDifficulty() -> 3 | 2 | 1            getAdaptation() -> { level, reason }        getSettings() -> stress.settings(level)
//   onAdapt(cb) -> unsubscribe              cb({ level, prevLevel, reason, t, at }) runs only when the level changes
//   reportAttempt(passed)                   the chat shell tells us about checkpoint results so repeated misses count
//   checkpointMode(kind) -> { mode, hint, splitSteps, autoPass:false }        getAdaptLog() -> [{ at, level, prevLevel, reason }]
// Privacy: heart rate stays on this device. The adaptation log lives in localStorage only and is never sent anywhere.
(function (root) {
  "use strict";
  var S = typeof module !== "undefined" && module.exports ? require("./stress.js") : root.Stress;
  var HR = typeof module !== "undefined" && module.exports ? require("./hr.js") : root.HR;
  var LOG_KEY = "sc_hr_log", LOG_MAX = 100, SPARK_N = 60;

  function loadLog(storage) {
    try { var v = JSON.parse(storage && storage.getItem(LOG_KEY)); return Array.isArray(v) ? v : []; } catch (e) { return []; }
  }

  function createController(opts) {
    var o = opts || {}, now = o.now || Date.now, storage = o.storage || null;
    var det = S.init(o.detector), ad = S.initAdapt(), misses = 0, listeners = [], log = loadLog(storage), last = null, spark = [];

    function persist() { try { if (storage) storage.setItem(LOG_KEY, JSON.stringify(log)); } catch (e) { /* storage full or blocked: the log is best effort */ } }

    function evaluate(t) {
      var prevLevel = ad.level;
      ad = S.adapt(ad, { t: t, stress: det.stress, failures: misses, bpm: det.bpm, baseline: det.baseline });
      if (ad.changed) {
        var ev = { level: ad.level, prevLevel: prevLevel, reason: ad.reason, t: t, at: new Date(t).toISOString() };
        log = log.concat([{ at: ev.at, level: ev.level, prevLevel: prevLevel, reason: ev.reason }]).slice(-LOG_MAX);
        persist();
        listeners.slice().forEach(function (cb) { try { cb(ev); } catch (e) { if (root.console) root.console.error("EarnHooks.onAdapt listener failed", e); } });
      }
    }

    var hooks = {
      getDifficulty: function () { return ad.level; },
      getAdaptation: function () { return { level: ad.level, reason: ad.reason }; },
      getSettings: function () { return S.settings(ad.level); },
      checkpointMode: function (kind) { return S.checkpointMode(ad.level, kind); },
      onAdapt: function (cb) {
        if (typeof cb !== "function") return function () {};
        listeners.push(cb);
        return function () { listeners = listeners.filter(function (f) { return f !== cb; }); };
      },
      reportAttempt: function (passed) { misses = passed ? 0 : misses + 1; evaluate(last === null ? now() : last); },
      getAdaptLog: function () { return log.slice(); }
    };

    return {
      hooks: hooks,
      push: function (sample) {
        det = S.step(det, sample);
        if (det.t !== null) last = det.t;
        if (typeof sample.bpm === "number" && det.bpm !== null) spark = spark.concat([det.bpm]).slice(-SPARK_N);
        evaluate(sample.t);
      },
      calibrate: function () { det = S.calibrate(det, last === null ? now() : last); ad = S.initAdapt(); },
      reset: function () { det = S.init(o.detector); ad = S.initAdapt(); misses = 0; last = null; spark = []; },
      view: function () { return { detector: det, level: ad.level, reason: ad.reason, misses: misses, spark: spark.slice(), log: log.slice() }; }
    };
  }

  function sparkPoints(values, w, h) {
    if (!values.length) return "";
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values), span = Math.max(hi - lo, 20), pad = 2;
    return values.map(function (v, i) {
      var x = values.length === 1 ? w : (i / (values.length - 1)) * w;
      var y = h - pad - ((v - lo) / span) * (h - 2 * pad);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
  }

  // ---------------- DOM ----------------
  var SVGNS = "http://www.w3.org/2000/svg";
  function h(doc, tag, attrs, kids) {
    var el = doc.createElement(tag), k;
    for (k in (attrs || {})) {
      if (k === "text") el.textContent = attrs[k];
      else if (k === "class") el.className = attrs[k];
      else el.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { el.appendChild(c); });
    return el;
  }

  function mount(host, opts) {
    var o = opts || {}, doc = o.document || root.document, nav = o.navigator || root.navigator;
    var storage = null;
    try { storage = o.storage || root.localStorage; } catch (e) { storage = null; }
    var ctrl = createController({ storage: storage }), sim = HR.createSimulator(), strap = null;

    var bpmEl = h(doc, "span", { class: "hrw-bpm", text: "--" });
    var chip = h(doc, "span", { class: "hrw-chip calm", text: "calibrating" });
    var svg = doc.createElementNS(SVGNS, "svg"); svg.setAttribute("class", "hrw-spark"); svg.setAttribute("viewBox", "0 0 120 28"); svg.setAttribute("aria-hidden", "true");
    var line = doc.createElementNS(SVGNS, "polyline"); line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "2"); svg.appendChild(line);
    var srcEl = h(doc, "span", { class: "hrw-src", text: "SIMULATOR" });
    var toggle = h(doc, "button", { class: "hrw-toggle", type: "button", "aria-expanded": "false", "aria-label": "Heart rate controls" }, [
      h(doc, "span", { class: "hrw-heart", "aria-hidden": "true", text: "♥" }), bpmEl, h(doc, "span", { class: "hrw-unit", text: "bpm" }), svg, chip, srcEl]);

    var whyEl = h(doc, "p", { class: "hrw-why", "aria-live": "polite", text: "Calibrating baseline for 30 s." });
    var slider = h(doc, "input", { type: "range", min: String(HR.MIN_TARGET), max: String(HR.MAX_TARGET), value: "72", "aria-label": "Simulated heart rate" });
    var sliderVal = h(doc, "output", { text: "72" });
    function mkBtn(label, fn, cls) { var b = h(doc, "button", { type: "button", class: "hrw-btn" + (cls ? " " + cls : ""), text: label }); b.addEventListener("click", fn); return b; }
    var spikeBtn = mkBtn("Sudden spike", function () { sim.spike(); setSlider(sim.target()); });
    var calmBtn = mkBtn("Calm", function () { sim.calm(); setSlider(sim.target()); });
    var calBtn = mkBtn("Calibrate baseline", function () { ctrl.calibrate(); render(); });
    var strapBtn = mkBtn("Connect strap", connectStrapClick);
    var strapNote = h(doc, "p", { class: "hrw-note", text: HR.bleSupported(nav) ? HR.BLE_NOTE : "Web Bluetooth is not available in this browser, so only the simulator works here. " + HR.BLE_NOTE });
    if (!HR.bleSupported(nav)) strapBtn.disabled = true;
    var breathing = h(doc, "div", { class: "hrw-breath hidden", role: "status" }, [h(doc, "span", { class: "hrw-orb" }), h(doc, "span", { text: "Breathe in for 4, out for 6. The next step waits for you." })]);
    var logEl = h(doc, "ul", { class: "hrw-log" });
    var panel = h(doc, "div", { class: "hrw-panel hidden" }, [
      h(doc, "p", { class: "hrw-sim", text: "Simulated heart rate. Drag the slider or use the buttons to try how Devin adapts." }),
      h(doc, "label", { class: "hrw-slide" }, [h(doc, "span", { text: "Simulator" }), slider, sliderVal]),
      h(doc, "div", { class: "hrw-row" }, [spikeBtn, calmBtn, calBtn]),
      h(doc, "div", { class: "hrw-row" }, [strapBtn]), strapNote, whyEl,
      h(doc, "h4", { text: "Adaptation log" }), logEl,
      h(doc, "p", { class: "hrw-note", text: "Your heart rate stays on this device and is never sent to the server. Not a medical device." })]);
    var bar = h(doc, "div", { class: "hrw level3", role: "region", "aria-label": "Heart rate adaptation" }, [toggle, breathing, panel]);
    host.appendChild(bar);

    function setSlider(v) { slider.value = String(v); sliderVal.textContent = String(v); }
    slider.addEventListener("input", function () { sim.setTarget(Number(slider.value)); sliderVal.textContent = String(sim.target()); });
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      panel.className = "hrw-panel" + (open ? "" : " hidden");
    });

    function render() {
      var v = ctrl.view(), d = v.detector, s = d.baseline === null ? "calibrating" : d.stress;
      bpmEl.textContent = d.bpm === null ? "--" : String(Math.round(d.bpm));
      if (d.baseline === null && d.calStart !== null && d.t !== null) s = "calibrating " + Math.min(30, Math.round((d.t - d.calStart) / 1000)) + "/30 s";
      chip.textContent = s; chip.className = "hrw-chip " + (d.baseline === null ? "calibrating" : d.stress);
      line.setAttribute("points", sparkPoints(v.spark, 120, 28));
      bar.className = "hrw level" + v.level;
      breathing.className = "hrw-breath" + (v.level === 1 ? "" : " hidden");
      whyEl.textContent = (v.reason || d.why) + (d.baseline !== null ? "  [difficulty level " + v.level + " of 3, baseline " + Math.round(d.baseline) + "]" : "");
      logEl.replaceChildren.apply(logEl, v.log.slice(-6).reverse().map(function (e) {
        return h(doc, "li", { text: e.at.slice(11, 19) + "  level " + e.prevLevel + " to " + e.level + ": " + e.reason });
      }));
    }

    function onSample(sample) { ctrl.push(sample); render(); }
    function useSimulator() { strap = null; srcEl.textContent = "SIMULATOR"; srcEl.className = "hrw-src"; strapBtn.textContent = "Connect strap"; [slider, spikeBtn, calmBtn].forEach(function (b) { b.disabled = false; }); ctrl.reset(); sim.start(onSample); }
    function connectStrapClick() {
      if (strap) { strap.stop(); useSimulator(); return; }
      strapBtn.disabled = true; strapNote.textContent = "Choose your strap in the browser prompt...";
      HR.connectStrap(nav, onSample).then(function (s) {
        sim.stop(); strap = s; ctrl.reset();
        srcEl.textContent = "STRAP (untested)"; srcEl.className = "hrw-src strap"; strapBtn.textContent = "Disconnect strap";
        [slider, spikeBtn, calmBtn].forEach(function (b) { b.disabled = true; });
        strapNote.textContent = "Connected to " + s.name + ". " + HR.BLE_NOTE; strapBtn.disabled = false;
      }, function (err) { strapNote.textContent = "Could not connect: " + (err && err.message ? err.message : "cancelled") + ". " + HR.BLE_NOTE; strapBtn.disabled = false; });
    }

    var existing = root.EarnHooks || {}, merged = {}, k;
    for (k in existing) merged[k] = existing[k];
    for (k in ctrl.hooks) merged[k] = ctrl.hooks[k];
    root.EarnHooks = merged;
    render(); sim.start(onSample);
    return { controller: ctrl, simulator: sim, element: bar };
  }

  var api = { createController: createController, sparkPoints: sparkPoints, mount: mount };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HRWidget = api;
  if (typeof document !== "undefined" && root.document && !(typeof module !== "undefined" && module.exports)) {
    var go = function () { var host = root.document.getElementById("hr-widget"); if (host) mount(host); };
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", go); else go();
  }
})(typeof self !== "undefined" ? self : this);
