// Phone-to-phone exchange with no base: one phone shows its operations as text, another pastes them in. The text is plain
// JSON, so it can travel by any channel that survives with no internet (copy and paste into a chat that works offline,
// a file shared over Bluetooth or a nearby-share, a typed-out QR code). Everything pasted is validated like the base does.
(function (root) {
  "use strict";
  var FIELDS = ["id", "lamport", "kind", "item", "replica", "value"];
  var KINDS = ["adjust", "create", "note", "delete"];
  var MAX_OPS = 5000, MAX_TEXT = 200, MAX_QTY = 1e9, MAX_LAMPORT = 9007199254740991;

  function encode(ops) { return JSON.stringify({ stillcounts: 1, ops: ops }); }

  function text(v) { return typeof v === "string" && v.length >= 1 && v.length <= 64 && !/[\u0000-\u001f]/.test(v); }

  function valid(o) {
    if (!o || typeof o !== "object" || Array.isArray(o)) return false;
    var keys = Object.keys(o).sort();
    if (keys.join() !== FIELDS.slice().sort().join()) return false;
    if (!text(o.id) || !text(o.replica) || !text(o.item) || KINDS.indexOf(o.kind) < 0) return false;
    if (typeof o.lamport !== "number" || Math.floor(o.lamport) !== o.lamport || o.lamport < 1 || o.lamport > MAX_LAMPORT) return false;
    if (o.kind === "adjust") return typeof o.value === "number" && Math.floor(o.value) === o.value && o.value !== 0 && Math.abs(o.value) <= MAX_QTY;
    if (o.kind === "delete") return typeof o.value === "boolean";
    return typeof o.value === "string" && o.value.length <= MAX_TEXT && !/[\u0000-\u001f]/.test(o.value) && (o.kind !== "create" || o.value.length > 0);
  }

  // Returns { ops: [valid ops], skipped: n } or { error: "reason" } when the text is not a bundle at all.
  function decode(input) {
    var data;
    try { data = JSON.parse(String(input).trim()); } catch (e) { return { error: "That is not a Still Counts bundle (it is not valid JSON)." }; }
    var list = data && data.stillcounts === 1 ? data.ops : null;
    if (!Array.isArray(list)) return { error: "That is not a Still Counts bundle." };
    if (list.length > MAX_OPS) return { error: "That bundle has more than " + MAX_OPS + " operations." };
    var ops = list.filter(valid);
    return { ops: ops, skipped: list.length - ops.length };
  }

  var api = { encode: encode, decode: decode, valid: valid };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.StillCountsExchange = api;
})(typeof window !== "undefined" ? window : this);
