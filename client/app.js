// Local-first ledger: every tap is written to a local operation log first; syncing is best effort.
(function () {
  "use strict";
  var C = window.StillCounts;
  var ITEMS = [["blankets", "Blankets"], ["rice", "Rice (kg)"], ["water", "Drinking water (L)"],
               ["paracetamol", "Paracetamol (strips)"], ["tarpaulin", "Tarpaulins"], ["baby-food", "Baby food (tins)"]];

  function load(key, fallback) {
    try { var v = JSON.parse(localStorage.getItem(key)); return v === null || v === undefined ? fallback : v; } catch (e) { return fallback; }
  }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage full: keep running in memory */ } }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

  var replica = load("sc_replica", null);
  if (!replica) { replica = "ph-" + Math.random().toString(36).slice(2, 7); save("sc_replica", replica); }
  // Everything lives in ONE record written in one call, so a kill between two writes can never leave the log and the cursor disagreeing.
  var saved = load("sc_state", {});
  var ops = saved.ops || [];
  var synced = {}; (saved.synced || []).forEach(function (id) { synced[id] = true; });
  var cursor = saved.cursor || 0;
  var epoch = saved.epoch || null;
  var rejected = saved.rejected || 0;
  var clock = new C.Clock(replica, saved.lamport || 0);
  var known = {}; ops.forEach(function (o) { known[o.id] = true; });
  var online = false, busy = false, lastSync = null;

  function persist() {
    save("sc_state", { ops: ops, synced: Object.keys(synced), cursor: cursor, epoch: epoch, rejected: rejected, lamport: clock.time });
  }

  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }

  function addOp(kind, item, value) {
    var op = { id: replica + ":" + uid(), replica: replica, lamport: clock.tick(), kind: kind, item: item, value: value };
    ops.push(op); known[op.id] = true; persist(); render(); sync();
  }

  function queuedCount() { return ops.filter(function (o) { return !synced[o.id]; }).length; }

  function setStatus() {
    var s = document.getElementById("status"), q = queuedCount();
    if (online && q === 0) { s.className = "pill ok"; s.textContent = "online · synced"; }
    else if (q > 0) { s.className = "pill queued"; s.textContent = (online ? "syncing · " : "offline · ") + q + " queued"; }
    else { s.className = "pill bad"; s.textContent = "offline · nothing queued"; }
  }

  function sync() {
    if (busy) return; busy = true;
    var unsent = ops.filter(function (o) { return !synced[o.id]; });
    fetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" },
                         body: JSON.stringify({ replica: replica, cursor: cursor, epoch: epoch, ops: unsent.slice(0, 500) }) })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (body) {
        unsent.slice(0, 500).forEach(function (o) { synced[o.id] = true; });
        body.ops.forEach(function (o) {
          if (!known[o.id]) { ops.push(o); known[o.id] = true; }
          synced[o.id] = true; clock.observe(o.lamport);
        });
        (body.rejected || []).forEach(function (r) { if (r.id) { synced[r.id] = true; rejected += 1; } });
        cursor = body.cursor; epoch = body.epoch; online = true; lastSync = new Date(); persist();
      })
      .catch(function () { online = false; })
      .then(function () { busy = false; render(); });
  }

  function render() {
    var state = C.merge(ops), stats = C.stats(ops), list = document.getElementById("list");
    var ids = ITEMS.map(function (x) { return x[0]; });
    Object.keys(state).forEach(function (k) { if (ids.indexOf(k) < 0) ids.push(k); });
    var focused = document.activeElement && document.activeElement.getAttribute("data-note");
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
      var note = el("input", "note"); note.type = "text"; note.placeholder = "note"; note.value = s.note; note.maxLength = 120;
      note.setAttribute("data-note", id); note.setAttribute("aria-label", label + " note");
      note.addEventListener("change", function () { addOp("note", id, note.value); });
      card.appendChild(note); list.appendChild(card);
      if (focused === id) { note.focus(); }
    });
    document.getElementById("who").textContent = "This phone: " + replica;
    document.getElementById("foot").textContent = stats.ops + " operations · " + stats.conflicts + " conflicts merged" +
      (lastSync ? " · last sync " + lastSync.toLocaleTimeString() : " · not synced yet") +
      (rejected ? " · " + rejected + " rejected by the server" : "");
    setStatus();
  }

  document.getElementById("addbtn").addEventListener("click", function () {
    var input = document.getElementById("newitem"), name = input.value.trim();
    if (!name) return;
    var id = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "item";
    addOp("create", id, name); input.value = "";
  });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(function () {});
  window.addEventListener("online", sync);
  window.addEventListener("offline", function () { online = false; render(); });
  setInterval(sync, 2000);
  render(); sync();
})();
