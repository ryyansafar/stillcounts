// Heart-rate sources. Two of them, both produce { t, bpm, source } samples and nothing else:
//   simulator   default. A fake heart rate you steer with a slider / spike / calm. Always labelled SIMULATOR on screen.
//   strap       optional Web Bluetooth Heart Rate Service (0x180D, characteristic 0x2A37). Feature-detected; written to the
//               public GATT spec but UNTESTED without hardware, and the UI says so.
// Samples stay in the page. This file has no network code.
(function (root) {
  "use strict";
  var HR_SERVICE = 0x180d, HR_MEASUREMENT = 0x2a37;
  var MIN_TARGET = 40, MAX_TARGET = 190;
  var BLE_NOTE = "Strap support follows the Bluetooth Heart Rate spec but is untested without hardware.";

  // Heart Rate Measurement: byte 0 = flags (bit 0: 0 = uint8 value, 1 = uint16 little-endian), then the value.
  function parseHeartRate(view) {
    if (!view || view.byteLength < 2) return null;
    var flags = view.getUint8(0);
    if (flags & 1) return view.byteLength >= 3 ? view.getUint16(1, true) : null;
    return view.getUint8(1);
  }

  function bleSupported(nav) { return !!(nav && nav.bluetooth && typeof nav.bluetooth.requestDevice === "function"); }

  function clamp(v) { return Math.max(MIN_TARGET, Math.min(MAX_TARGET, Math.round(v))); }

  // tick(dtMs) moves the fake heart rate toward its target at `rate` bpm per second and adds +-1 bpm of noise.
  function createSimulator(opts) {
    var o = opts || {}, rest = clamp(o.rest || 72), rng = o.rng || Math.random, now = o.now || null;
    var cur = rest, tgt = rest, rate = 4, clock = 0, timer = null;
    var sim = {
      label: "SIMULATOR", rest: rest,
      target: function () { return tgt; },
      setTarget: function (v) { tgt = clamp(v); rate = 4; return tgt; },
      spike: function () { tgt = clamp(cur + 35); rate = 12; return tgt; },
      calm: function () { tgt = rest; rate = 2; return tgt; },
      tick: function (dtMs) {
        var d = tgt - cur, stepMax = rate * dtMs / 1000;
        cur += Math.abs(d) <= stepMax ? d : (d > 0 ? stepMax : -stepMax);
        clock += dtMs;
        return { t: now ? now() : clock, bpm: Math.round(cur + (rng() - 0.5) * 2), source: "simulator" };
      },
      start: function (onSample, intervalMs) {
        var every = intervalMs || 1000;
        if (timer) return;
        timer = setInterval(function () { onSample(sim.tick(every)); }, every);
      },
      stop: function () { if (timer) { clearInterval(timer); timer = null; } }
    };
    return sim;
  }

  // Must be called from a user gesture (the Connect strap button). Resolves to { name, stop() }.
  function connectStrap(nav, onSample, now) {
    if (!bleSupported(nav)) return Promise.reject(new Error("Web Bluetooth is not available in this browser"));
    var device, server, ch, handler;
    return nav.bluetooth.requestDevice({ filters: [{ services: [HR_SERVICE] }] })
      .then(function (d) { device = d; return d.gatt.connect(); })
      .then(function (s) { server = s; return s.getPrimaryService(HR_SERVICE); })
      .then(function (svc) { return svc.getCharacteristic(HR_MEASUREMENT); })
      .then(function (c) {
        ch = c;
        handler = function (ev) {
          var bpm = parseHeartRate(ev.target.value);
          if (bpm !== null) onSample({ t: now ? now() : Date.now(), bpm: bpm, source: "strap" });
        };
        ch.addEventListener("characteristicvaluechanged", handler);
        return ch.startNotifications();
      })
      .then(function () {
        return { name: device.name || "heart-rate strap", stop: function () {
          try { ch.removeEventListener("characteristicvaluechanged", handler); } catch (e) { /* already gone */ }
          try { if (server && server.connected) device.gatt.disconnect(); } catch (e2) { /* already gone */ }
        } };
      });
  }

  var api = { HR_SERVICE: HR_SERVICE, HR_MEASUREMENT: HR_MEASUREMENT, MIN_TARGET: MIN_TARGET, MAX_TARGET: MAX_TARGET, BLE_NOTE: BLE_NOTE,
              parseHeartRate: parseHeartRate, bleSupported: bleSupported, createSimulator: createSimulator, connectStrap: connectStrap };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HR = api;
})(typeof self !== "undefined" ? self : this);
