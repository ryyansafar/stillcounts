// Local-first ledger: every tap is written to a local operation log first; syncing is best effort.
(function () {
  "use strict";
  var C = window.StillCounts;
  var ITEMS = [["blankets", "Blankets"], ["rice", "Rice (kg)"], ["water", "Drinking water (L)"],
               ["paracetamol", "Paracetamol (strips)"], ["tarpaulin", "Tarpaulins"], ["baby-food", "Baby food (tins)"]];

  function load(key, fallback) {
    try { var v = JSON.parse(localStorage.getItem(key)); return v === null || v === undefined ? fallback : v; } catch (e) { return fallback; }
  }
  // Returns false when the write failed (storage full or blocked); the caller decides how to cope.
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; } }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

  var replica = load("sc_replica", null);
  if (!replica) { replica = "ph-" + Math.random().toString(36).slice(2, 7); save("sc_replica", replica); }
  // Everything lives in ONE record written in one call, so a kill between two writes can never leave the log and the cursor disagreeing.
  var saved = load("sc_state", {});
  var ops = saved.ops || [];
  var synced = {}; (saved.synced || []).forEach(function (id) { synced[id] = true; });
  var cursor = saved.cursor || 0;
  var epoch = saved.epoch || null;
  var anchor = saved.anchor || null;          // id of the base-log entry just before `cursor`, so the base can check the cursor still fits
  var rejected = saved.rejected || 0;
  var clock = new C.Clock(replica, saved.lamport || 0);
  var myName = load("sc_name", "");
  var known = {}; ops.forEach(function (o) { known[o.id] = true; });
  var online = false, busy = false, lastSync = null, lastView = null, storageFull = false, denied = false;
  var undone = {}; (load("sc_undone", []) || []).forEach(function (id) { undone[id] = true; });   // taps this phone has taken back (a convenience, kept per phone)

  // Tabs of one phone share this record, so fold in whatever another tab wrote before overwriting it.
  // Only ops this tab has never seen are taken over, together with their sent marks. For ops it already knows, this
  // tab's own marks stay authoritative: a stale record must not undo a reset (see sync()).
  function absorb(stored) {
    if (!stored || !stored.ops) return false;
    var added = false, mark = {};
    (stored.synced || []).forEach(function (id) { mark[id] = true; });
    stored.ops.forEach(function (o) {
      if (known[o.id]) return;
      ops.push(o); known[o.id] = true; added = true;
      if (mark[o.id]) synced[o.id] = true;
    });
    clock.observe(stored.lamport || 0);
    rejected = Math.max(rejected, stored.rejected || 0);
    return added;
  }

  function persist() {
    absorb(load("sc_state", null));
    var rec = { ops: ops, synced: Object.keys(synced), cursor: cursor, epoch: epoch, anchor: anchor, rejected: rejected, lamport: clock.time };
    if (save("sc_state", rec)) { storageFull = false; return; }
    // Storage is full. Ops the base already has are safe elsewhere, so keep only the unsent ones on disk and forget the
    // cursor, which makes the next sync pull the whole log back. Unsent ops that still do not fit stay in memory only.
    var unsent = ops.filter(function (o) { return !synced[o.id]; });
    storageFull = !save("sc_state", { ops: unsent, synced: [], cursor: 0, epoch: null, anchor: null, rejected: rejected, lamport: clock.time });
  }

  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }

  function addOp(kind, item, value) {
    absorb(load("sc_state", null));
    var op = { id: replica + ":" + uid(), replica: replica, lamport: clock.tick(), kind: kind, item: item, value: value };
    ops.push(op); known[op.id] = true; persist(); render(); sync();
    return op;
  }

  // The latest tap on this item that this phone made and has not yet taken back. Undo adds the opposite adjustment as a
  // new operation: nothing is ever edited or deleted, so the merge and the other phones are unaffected.
  function lastUndoable(item) {
    var mine = ops.filter(function (o) { return o.replica === replica && o.kind === "adjust" && o.item === item && !undone[o.id]; });
    mine.sort(function (a, b) { return a.lamport - b.lamport; });
    return mine[mine.length - 1] || null;
  }
  function undo(item) {
    var last = lastUndoable(item);
    if (!last) return;
    undone[last.id] = true;
    var inverse = addOp("adjust", item, -last.value);
    undone[inverse.id] = true;
    save("sc_undone", Object.keys(undone)); render();
  }

  function unsentItems() {
    var by = {}; ops.forEach(function (o) { if (!synced[o.id]) by[o.item] = true; }); return by;
  }

  function shellCached() {
    return !!(typeof navigator !== "undefined" && navigator.serviceWorker && navigator.serviceWorker.controller);
  }

  function queuedCount() { return ops.filter(function (o) { return !synced[o.id]; }).length; }

  function setStatus() {
    var s = document.getElementById("status"), q = queuedCount();
    if (denied) { s.className = "pill bad"; s.textContent = "join link needed: scan the board's QR code"; }
    else if (online && q === 0) { s.className = "pill ok"; s.textContent = "online · synced"; }
    else if (q > 0) { s.className = "pill queued"; s.textContent = (online ? "syncing · " : "offline · ") + q + " queued"; }
    else { s.className = "pill bad"; s.textContent = "offline · nothing queued"; }
  }

  // The base said an op's Lamport time is unusable (far ahead of everyone, or not a number): give it a fresh time after
  // everything the base holds instead of dropping the tap. Returns true if the op was re-stamped and must be sent again.
  function restamp(r, newest) {
    if (!r.id || typeof newest !== "number" || !/^lamport/.test(r.reason || "")) return false;
    var op = ops.filter(function (o) { return o.id === r.id; })[0];
    if (!op) return false;
    op.lamport = newest + 1;
    clock.time = newest + 1;      // also repairs a clock that was pushed far ahead (for example by a bad phone clock)
    return true;
  }

  // The base already holds a DIFFERENT op under this id (a reinstalled or legacy client reused it). Keeping both under one
  // id would leave this phone and the base disagreeing for good, so this phone's op gets a fresh id and is sent again;
  // the base's op under the old id is pulled in as usual. Returns the new id, or null if this rejection is not that case.
  function reidentify(r) {
    if (!r.id || r.reason !== "id reused with different content") return null;
    var op = ops.filter(function (o) { return o.id === r.id; })[0];
    if (!op) return null;
    delete known[op.id]; delete synced[op.id];
    op.id = replica + ":" + uid(); known[op.id] = true;
    return op.id;
  }

  function sync() {
    if (busy) return; busy = true;
    var unsent = ops.filter(function (o) { return !synced[o.id]; });
    fetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" },
                         body: JSON.stringify({ replica: replica, name: myName || null, cursor: cursor, epoch: epoch, anchor: anchor,
                                                held: ops.length, queued: unsent.length, ops: unsent.slice(0, 500) }) })
      .then(function (r) { denied = r.status === 401; if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (body) {
        // The base's log is not the one this phone last read (restored, wiped, lines dropped): forget what we thought
        // it had. Below, every op the base does return is marked sent again, so only the ones it lost go back out.
        if (body.reset) synced = {};
        var again = {}, renamed = false;
        (body.rejected || []).forEach(function (r) {
          var fresh;
          if (restamp(r, body.max_lamport)) { again[r.id] = true; body.max_lamport += 1; }
          else if ((fresh = reidentify(r))) { again[fresh] = true; renamed = true; }
          else if (r.id) { rejected += 1; }
        });
        unsent.slice(0, 500).forEach(function (o) { if (!again[o.id]) synced[o.id] = true; });
        body.ops.forEach(function (o) {
          if (!known[o.id]) { ops.push(o); known[o.id] = true; }
          synced[o.id] = true; clock.observe(o.lamport);
        });
        (body.rejected || []).forEach(function (r) { if (r.id && !again[r.id]) synced[r.id] = true; });
        if (body.ops.length) anchor = body.ops[body.ops.length - 1].id; else if (body.cursor === 0) anchor = null;
        cursor = body.cursor; epoch = body.epoch; online = true;
        if (renamed) { cursor = 0; anchor = null; }       // re-read the whole log so the base's op under the old id is surely here
        lastSync = new Date(); persist();
      })
      .catch(function () { online = false; })
      .then(function () { busy = false; render(); });
  }

  function render() {
    var state = C.merge(ops), stats = C.stats(ops), list = document.getElementById("list");
    var ids = ITEMS.map(function (x) { return x[0]; });
    Object.keys(state).forEach(function (k) { if (ids.indexOf(k) < 0) ids.push(k); });
    var pending = unsentItems();
    var view = JSON.stringify(ids.map(function (id) {
      var s = state[id], u = lastUndoable(id);
      return (s ? [id, s.name, s.qty, s.note, s.alive] : [id]).concat([!!pending[id], u ? u.id : null]);
    }));
    if (view !== lastView) { lastView = view; renderList(ids, state, list, pending); }
    document.getElementById("who").textContent = "This phone: " + replica;
    document.getElementById("foot").textContent = stats.ops + " operations · " + stats.conflicts + " conflicts merged" +
      (lastSync ? " · last sync " + lastSync.toLocaleTimeString() : " · not synced yet") +
      (rejected ? " · " + rejected + " rejected by the server" : "") +
      (storageFull ? " · PHONE STORAGE FULL: taps not yet sent exist only while this page stays open" : "") +
      (shellCached() ? "" : " · app not saved for offline use yet (browsers only do that over https or localhost): keep this page open");
    setStatus();
  }

  // Rebuilt only when the ledger changes, so a finger mid-tap or a half-typed note is not thrown away.
  function renderList(ids, state, list, pending) {
    var active = document.activeElement, focused = active && active.getAttribute && active.getAttribute("data-note");
    var draft = focused ? { value: active.value, start: active.selectionStart, end: active.selectionEnd } : null;
    list.replaceChildren();
    ids.forEach(function (id) {
      var s = state[id] || { name: id, qty: 0, note: "", alive: true };
      if (!s.alive) return;
      var label = (ITEMS.filter(function (x) { return x[0] === id; })[0] || [0, s.name])[1];
      if (state[id] && state[id].name !== id) label = state[id].name;
      var card = el("div", "card" + (s.qty < 0 ? " over" : ""));
      var top = el("div", "top"); top.appendChild(el("div", "name", label));
      var q = el("div", "qty" + (s.qty < 0 ? " neg" : ""), String(s.qty)); top.appendChild(q); card.appendChild(top);
      if (s.qty < 0) card.appendChild(el("div", "flag", "Needs a human: more issued than counted in"));
      var btns = el("div", "btns");
      [[-10, "−10"], [-1, "−1"], [1, "+1"], [10, "+10"]].forEach(function (d) {
        var b = el("button", "", d[1]); b.type = "button"; b.setAttribute("aria-label", label + " " + d[1]);
        b.addEventListener("click", function () { addOp("adjust", id, d[0]); }); btns.appendChild(b);
      });
      card.appendChild(btns);
      var extra = el("div", "extra");
      if (pending[id]) extra.appendChild(el("span", "pending", "not yet sent to the base"));
      if (lastUndoable(id)) {
        var u = el("button", "undo", "Undo my last tap"); u.type = "button"; u.setAttribute("aria-label", label + " undo");
        u.addEventListener("click", function () { undo(id); }); extra.appendChild(u);
      }
      if (extra.children.length) card.appendChild(extra);
      var note = el("input", "note"); note.type = "text"; note.placeholder = "note"; note.value = s.note; note.maxLength = 120;
      note.setAttribute("data-note", id); note.setAttribute("aria-label", label + " note");
      note.addEventListener("change", function () { addOp("note", id, note.value); });
      card.appendChild(note); list.appendChild(card);
      if (focused === id) { note.value = draft.value; note.focus(); note.setSelectionRange(draft.start, draft.end); }
    });
  }

  // Unicode-aware so names in Malayalam, Hindi or Tamil get their own item instead of all collapsing to one id. Names are
  // NFC-normalised first so the same word typed on two keyboards is one item; a name with no letters or digits at all
  // (only symbols) falls back to a short hash of the name, so it is stable and still distinct.
  function hash36(text) {
    var h = 2166136261;
    Array.from(text).forEach(function (ch) { h = Math.imul(h ^ ch.codePointAt(0), 16777619) >>> 0; });
    return h.toString(36);
  }
  function itemId(name) {
    var nfc = name.normalize("NFC");
    var slug = Array.from(nfc.toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, "-").replace(/^-+/, "")).slice(0, 40).join("");
    return slug.replace(/-+$/, "") || "item-" + hash36(nfc);
  }

  document.getElementById("addbtn").addEventListener("click", function () {
    var input = document.getElementById("newitem"), name = input.value.trim();
    if (!name) return;
    var id = itemId(name);
    addOp("create", id, name); input.value = "";
  });

  var me = document.getElementById("me");
  me.value = myName;
  me.addEventListener("change", function () { myName = me.value.trim(); save("sc_name", myName); sync(); });

  // Phone to phone with no base: show my operations as text, or merge text another phone showed. Imported operations count
  // as not yet sent to the base, so they still reach it when a signal returns.
  var X = window.StillCountsExchange, box = document.getElementById("p2p-box"), msg = document.getElementById("p2p-msg");
  if (X && box && msg) {
    document.getElementById("p2p-out").addEventListener("click", function () {
      box.value = X.encode(ops); box.select && box.select();
      var copied = typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(box.value) : null;
      if (copied && copied.then) copied.then(function () { msg.textContent = "Copied " + ops.length + " operations. Send them to the other phone."; },
                                             function () { msg.textContent = "Select the text above and copy it (" + ops.length + " operations)."; });
      else msg.textContent = "Select the text above and copy it (" + ops.length + " operations).";
    });
    document.getElementById("p2p-in").addEventListener("click", function () {
      var got = X.decode(box.value);
      if (got.error) { msg.textContent = got.error; return; }
      absorb(load("sc_state", null));
      var fresh = got.ops.filter(function (o) { return !known[o.id]; });
      fresh.forEach(function (o) { ops.push(o); known[o.id] = true; clock.observe(o.lamport); });
      if (fresh.length) { persist(); render(); sync(); }
      msg.textContent = "Merged " + fresh.length + " new operations" + (got.ops.length - fresh.length ? " (" + (got.ops.length - fresh.length) + " already here)" : "") +
        (got.skipped ? ", skipped " + got.skipped + " that were not valid" : "") + ".";
    });
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
    navigator.serviceWorker.addEventListener && navigator.serviceWorker.addEventListener("controllerchange", render);
  }
  window.addEventListener("online", sync);
  window.addEventListener("storage", function (e) { if (e.key === "sc_state" && absorb(load("sc_state", null))) render(); });
  window.addEventListener("offline", function () { online = false; render(); });
  setInterval(sync, 2000);
  render(); sync();
})();
