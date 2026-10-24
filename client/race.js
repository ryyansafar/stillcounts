// Referee Race. Pure helpers (answerKey, options, trickFor, grade, describe) run in node tests and the browser;
// the page code below them only runs in a browser. The answer always comes from the real merge in crdt.js.
(function (root) {
  "use strict";
  var C = typeof module !== "undefined" && module.exports ? require("./crdt.js") : root.StillCounts;
  var REMOVED = "removed";

  function answerKey(ops) {
    var m = C.merge(ops), out = {};
    Object.keys(m).forEach(function (k) { out[k] = m[k].alive ? m[k].qty : REMOVED; });
    return out;
  }

  function volunteer(replica) {
    var n = String(replica).replace(/^vol-/, "");
    return n.charAt(0).toUpperCase() + n.slice(1);
  }

  function describe(op, names) {
    var label = (names && names[op.item]) || op.item, who = volunteer(op.replica) + ": ", t = " · t" + op.lamport;
    if (op.kind === "adjust") return who + (op.value > 0 ? "+" : "") + op.value + " " + label + t;
    if (op.kind === "create") return who + "named " + op.item + " \u201c" + op.value + "\u201d" + t;
    if (op.kind === "delete") return who + (op.value ? "removed " : "restored ") + label + t;
    return who + "noted " + label + t;
  }

  function hash(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return h;
  }

  // Each trick is a plausible wrong way to read this phone's order, with the op that leads a person there.
  function tricks(seq, item) {
    var truth = answerKey(seq)[item], st = C.merge(seq)[item], out = [], seen = {};
    var mine = [];
    seq.forEach(function (o, i) { if (o.item === item) mine.push({ op: o, index: i }); });
    function add(value, entry, why) {
      if (value === truth || out.some(function (t) { return t.value === value; })) return;
      out.push({ value: value, op: entry.op, index: entry.index, why: why });
    }
    var naive = 0, dup = null;
    mine.forEach(function (e) {
      if (e.op.kind !== "adjust") return;
      naive += e.op.value;
      if (seen[e.op.id] && !dup) dup = e;
      seen[e.op.id] = true;
    });
    if (dup) add(st.alive ? naive : REMOVED, dup, "This is a duplicate: the same op id arrived twice, and the merge counts it once.");
    var flags = mine.filter(function (e) { return e.op.kind === "delete"; });
    if (flags.length) {
      var last = flags[flags.length - 1];
      add(last.op.value ? REMOVED : st.qty, last,
        "This arrived last but has an older Lamport time, so it does not win. Arrival order never decides.");
      var winner = flags.reduce(function (w, e) {
        return !w || e.op.lamport > w.op.lamport || (e.op.lamport === w.op.lamport && e.op.replica > w.op.replica) ? e : w;
      }, null);
      if (!st.alive) add(st.qty, winner, "This delete has the latest Lamport time, so the item is removed.");
    }
    var adj = [];
    mine.forEach(function (e) { if (e.op.kind === "adjust" && !adj.some(function (a) { return a.op.id === e.op.id; })) adj.push(e); });
    if (st.alive) {
      adj.slice().reverse().forEach(function (e) { add(st.qty - e.op.value, e, "This op still counts, wherever it sits in the order."); });
      if (adj.length) add(st.qty + adj[0].op.value, adj[0], "This op counts exactly once.");
      add(REMOVED, mine[0], "Nothing with a later Lamport time removed this item, so it is still here.");
    } else if (adj.length) {
      add(st.qty - adj[0].op.value, adj[0], "This item was removed, so it has no stock to report.");
    }
    return out;
  }

  function options(seq, item, phone) {
    var truth = answerKey(seq)[item];
    var list = [{ value: truth }].concat(tricks(seq, item).slice(0, 3).map(function (t) { return { value: t.value }; }));
    var s = hash(phone + "\u0000" + item);
    for (var j = list.length - 1; j > 0; j--) {
      s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
      var k = s % (j + 1), tmp = list[j]; list[j] = list[k]; list[k] = tmp;
    }
    return list;
  }

  function trickFor(seq, item, picked) {
    if (picked === answerKey(seq)[item]) return null;
    var hit = tricks(seq, item).filter(function (t) { return t.value === picked; })[0];
    if (hit) return { op: hit.op, index: hit.index, why: hit.why };
    for (var i = 0; i < seq.length; i++) {
      if (seq[i].item === item) return { op: seq[i], index: i, why: "Merge every op for this item, ignoring the order." };
    }
    return null;
  }

  function grade(seq, answers) {
    var key = answerKey(seq), items = {}, first = null, correct = true;
    Object.keys(key).forEach(function (item) {
      var picked = answers[item], ok = picked === key[item], trick = ok ? null : trickFor(seq, item, picked);
      if (!ok) correct = false;
      if (trick && (!first || trick.index < first.index)) first = trick;
      items[item] = { ok: ok, picked: picked, truth: key[item], trick: trick };
    });
    return { correct: correct, items: items, first: first, key: key };
  }

  var api = { answerKey: answerKey, options: options, trickFor: trickFor, grade: grade, describe: describe, REMOVED: REMOVED };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.RefereeRace = api;
  if (typeof document === "undefined") return;

  // ---------- page ----------
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function load(key, fallback) { try { var v = JSON.parse(localStorage.getItem(key)); return v === null || v === undefined ? fallback : v; } catch (e) { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* keep going in memory */ } }

  var player = load("race_player", null);
  if (!player) { player = "ph-" + Math.random().toString(36).slice(2, 10); save("race_player", player); }
  var round = load("race_round", null), picks = load("race_picks", {}), done = load("race_done", null);
  var outbox = load("race_outbox", []), shownRound = null;

  var me = $("me");
  me.value = load("race_name", load("sc_name", ""));
  me.addEventListener("input", function () { save("race_name", me.value.trim()); });

  function status(text, cls) { $("status").textContent = text; $("status").className = "pill " + cls; }
  function fmt(v) { return v === REMOVED ? "removed" : String(v); }

  function render() {
    if (!round) { $("intro").textContent = "Waiting for the host to start a round on the board."; return; }
    if (shownRound === round.round && !$("result").dataset.stale) return;
    shownRound = round.round; delete $("result").dataset.stale;
    var startKey = "race_start_" + round.round;
    if (!load(startKey, null)) save(startKey, Date.now());
    $("intro").textContent = "Round " + round.round + ". Your phone got these " + round.ops.length +
      " ops in its own order. What does the merge say each item's stock is?";
    var list = $("ops"); list.replaceChildren();
    round.ops.forEach(function (o, i) { list.appendChild(el("li", "op op-" + o.kind, describe(o, round.names))); list.lastChild.dataset.index = i; });
    var box = $("items"); box.replaceChildren();
    round.items.forEach(function (item) {
      var card = el("div", "card");
      card.appendChild(el("div", "name", round.names[item] || item));
      var row = el("div", "choices");
      options(round.ops, item, player).forEach(function (o) {
        var b = el("button", "choice", fmt(o.value)); b.type = "button";
        b.setAttribute("aria-pressed", String(picks[item] === o.value));
        b.addEventListener("click", function () {
          if (done && done.round === round.round) return;
          picks[item] = o.value; save("race_picks", picks);
          Array.prototype.forEach.call(row.children, function (c) { c.setAttribute("aria-pressed", String(c === b)); });
          ready();
        });
        row.appendChild(b);
      });
      card.appendChild(row); box.appendChild(card);
    });
    ready();
    if (done && done.round === round.round) showResult();
  }

  function ready() {
    var all = round.items.every(function (i) { return picks[i] !== undefined; });
    $("lock").disabled = !all || !!(done && done.round === round.round);
  }

  function showResult() {
    var g = grade(round.ops, picks), box = $("result"); box.replaceChildren();
    box.className = "card " + (g.correct ? "win" : "lose");
    box.appendChild(el("div", "big", g.correct ? "Correct!" : "Not quite"));
    if (g.first) {
      box.appendChild(el("div", "", "Tricked by op #" + (g.first.index + 1) + ": " + describe(g.first.op, round.names)));
      box.appendChild(el("div", "why", g.first.why));
      var li = $("ops").children[g.first.index]; if (li) li.classList.add("trick");
    }
    box.appendChild(el("div", "why", "Your phone's merge: " + round.items.map(function (i) {
      return (round.names[i] || i) + " " + fmt(g.key[i]);
    }).join(" · ")));
    box.appendChild(el("div", "score", done.points === undefined ? "Score queued: it reaches the board when you have signal."
      : (done.points + " points" + (done.rank ? " · #" + done.rank + " correct" : ""))));
    $("payoff").classList.remove("hidden");
  }

  $("lock").addEventListener("click", function () {
    var started = load("race_start_" + round.round, Date.now());
    var g = grade(round.ops, picks);
    done = { round: round.round }; save("race_done", done);
    outbox.push({ player: player, name: me.value.trim().slice(0, 40), round: round.round, answers: picks,
                  merged: g.key, elapsed_ms: Math.max(0, Date.now() - started) });
    save("race_outbox", outbox);
    ready(); showResult(); flush();
  });

  function flush() {
    if (!outbox.length) return;
    var body = outbox[0];
    fetch("/api/race/answer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.ok || r.status === 409 || r.status === 422 ? r.json().then(function (d) { return [r.ok, d]; }) : Promise.reject(); })
      .then(function (res) {
        outbox.shift(); save("race_outbox", outbox);
        if (res[0] && done && done.round === body.round) { done.points = res[1].points; done.rank = res[1].rank; save("race_done", done); if (round && round.round === body.round) showResult(); }
        flush();
      }).catch(function () { status("offline · score queued", "queued"); });
  }

  function poll() {
    fetch("/api/race/round?player=" + encodeURIComponent(player)).then(function (r) {
      if (r.status === 404) return null;
      return r.ok ? r.json() : Promise.reject();
    }).then(function (d) {
      status(outbox.length ? "live · sending score" : "live", "ok");
      if (d && (!round || d.token !== round.token)) {
        round = d; picks = {}; done = null; shownRound = null;
        save("race_round", round); save("race_picks", picks); save("race_done", done);
        $("result").className = "card hidden"; $("payoff").classList.add("hidden");
      }
      render(); flush();
    }).catch(function () { status("offline · merge runs on this phone", "queued"); render(); });
  }

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(function () {});
  render(); poll(); setInterval(poll, 2000);
})(typeof self !== "undefined" ? self : this);
