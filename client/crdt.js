// JavaScript twin of stillcounts/crdt.py. Checked against golden vectors made by the Python reference.
(function (root) {
  "use strict";
  var KINDS = { adjust: 1, create: 1, note: 1, "delete": 1 };

  function isInt(v) { return typeof v === "number" && isFinite(v) && Math.floor(v) === v; }

  function validate(op) {
    if (!KINDS[op.kind]) throw new Error("unknown op kind: " + op.kind);
    if (!isInt(op.lamport) || op.lamport < 1) throw new Error("lamport must be a positive integer");
    if (op.kind === "adjust") {
      if (!isInt(op.value) || op.value === 0) throw new Error("adjust needs a non-zero integer");
    } else if (op.kind === "delete") {
      if (typeof op.value !== "boolean") throw new Error("delete needs a boolean");
    } else if (typeof op.value !== "string") {
      throw new Error(op.kind + " needs a string");
    }
  }

  // later (lamport, replica) wins; strings compare by UTF-16 code units, same as Python for BMP text
  function cmpOrder(a, b) {
    if (a.lamport !== b.lamport) return a.lamport < b.lamport ? -1 : 1;
    if (a.replica === b.replica) return 0;
    return a.replica < b.replica ? -1 : 1;
  }

  function cmpTotal(a, b) {
    var c = cmpOrder(a, b);
    if (c) return c;
    var ka = [a.kind, a.item, JSON.stringify(a.value)], kb = [b.kind, b.item, JSON.stringify(b.value)];
    for (var i = 0; i < 3; i++) if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1;
    return 0;
  }

  function dedupe(ops) {
    var best = {};
    ops.forEach(function (op) {
      validate(op);
      var cur = best[op.id];
      if (!cur || cmpTotal(op, cur) > 0) best[op.id] = op;
    });
    return Object.keys(best).sort().map(function (k) { return best[k]; });
  }

  function latest(ops) {
    return ops.reduce(function (w, o) { return !w || cmpOrder(o, w) > 0 ? o : w; }, null);
  }

  function merge(ops) {
    var byItem = {};
    dedupe(ops).forEach(function (op) { (byItem[op.item] = byItem[op.item] || []).push(op); });
    var state = {};
    Object.keys(byItem).sort().forEach(function (item) {
      var g = byItem[item];
      var names = g.filter(function (o) { return o.kind === "create"; });
      var notes = g.filter(function (o) { return o.kind === "note"; });
      var flags = g.filter(function (o) { return o.kind === "delete"; });
      var qty = 0;
      g.forEach(function (o) { if (o.kind === "adjust") qty += o.value; });
      var n = latest(names), t = latest(notes), f = latest(flags);
      state[item] = { name: n ? n.value : item, qty: qty, note: t ? t.value : "", alive: f ? !f.value : true };
    });
    return state;
  }

  function stats(ops) {
    var unique = dedupe(ops), state = merge(unique), conflicts = 0, groups = {};
    unique.forEach(function (o) {
      if (o.kind === "create" || o.kind === "note" || o.kind === "delete") {
        var key = o.item + "\u0000" + o.kind;
        (groups[key] = groups[key] || []).push(o);
      }
    });
    Object.keys(groups).forEach(function (k) {
      var g = groups[k], w = latest(g);
      g.forEach(function (o) { if (o.replica !== w.replica) conflicts++; });
    });
    var oversold = Object.keys(state).filter(function (i) { return state[i].qty < 0 && state[i].alive; });
    return { ops: unique.length, items: Object.keys(state).length, conflicts: conflicts, oversold: oversold };
  }

  function Clock(replica, start) { this.replica = replica; this.time = start || 0; }
  Clock.prototype.tick = function () { this.time += 1; return this.time; };
  Clock.prototype.observe = function (remote) { this.time = Math.max(this.time, remote); };

  var api = { merge: merge, stats: stats, dedupe: dedupe, validate: validate, Clock: Clock };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.StillCounts = api;
})(typeof self !== "undefined" ? self : this);
