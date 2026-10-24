(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  function cell(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; e.textContent = text; return e; }

  // The board is the alert: no internet service is needed to notice an oversell. It flashes and, if the browser allows
  // sound, beeps once when the number of oversold items grows. (Push through ntfy.sh is a bonus channel, not this.)
  var lastOver = 0;
  function alertOversell(over) {
    if (over > lastOver) {
      $("headline").className = "big alert";
      try {
        var Ctx = typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext);
        if (Ctx) { var a = new Ctx(), o = a.createOscillator(); o.frequency.value = 880; o.connect(a.destination); o.start(); o.stop(a.currentTime + 0.4); }
      } catch (e) { /* sound is optional */ }
    } else if (over === 0) { $("headline").className = "big"; }
    lastOver = over;
  }

  function renderLedger(data) {
    var rows = $("rows"); rows.replaceChildren();
    var order = Object.keys(data.state).filter(function (id) { return data.state[id].alive; });
    order.sort(function (a, b) { return (data.state[a].qty < 0 ? 0 : 1) - (data.state[b].qty < 0 ? 0 : 1); });   // items needing a human first
    order.forEach(function (id) {
      var s = data.state[id];
      var tr = document.createElement("tr");
      if (s.qty < 0) tr.className = "over";
      tr.appendChild(cell("td", "", s.name));
      tr.appendChild(cell("td", "qty" + (s.qty < 0 ? " neg" : ""), String(s.qty)));
      tr.appendChild(cell("td", "", s.qty < 0 ? "Needs a human. " + s.note : s.note));
      rows.appendChild(tr);
    });
    $("s-ops").textContent = String(data.stats.ops);
    $("s-conf").textContent = String(data.stats.conflicts);
    $("s-phones").textContent = String(data.replicas.filter(function (r) { return r !== "seed"; }).length);
    var feed = $("feed"); feed.replaceChildren();
    (data.activity || []).filter(function (a) { return a.who !== "seed"; }).slice(-6).forEach(function (a) {
      feed.appendChild(cell("span", "", a.who + ": " + a.text));
    });
    var over = data.stats.oversold.length; alertOversell(over);
    var fresh = (data.phones || []).filter(function (p) { return p.age_s < 15; });
    var behind = fresh.filter(function (p) { return !p.caught_up; }).length;
    $("s-caught").textContent = fresh.length ? (fresh.length - behind) + " / " + fresh.length : "–";
    $("headline").textContent = data.stats.ops === 0 ? "Waiting for phones"
      : over ? over + (over === 1 ? " item needs a human" : " items need a human")
      : !fresh.length ? data.stats.ops + " operations merged"
      : behind ? behind + " of " + fresh.length + (fresh.length === 1 ? " phone still catching up" : " phones still catching up")
      : "Every phone holds every operation";
  }

  function poll() {
    fetch("/api/state").then(function (r) { return r.json(); }).then(function (d) {
      $("conn").className = "pill ok"; $("conn").textContent = "live"; renderLedger(d);
    }).catch(function () { $("conn").className = "pill bad"; $("conn").textContent = "board offline"; });
    fetch("/api/chaos").then(function (r) { return r.json(); }).then(renderAttack).catch(function () {});
    fetch("/api/race/board").then(function (r) { return r.json(); }).then(renderRace).catch(function () {});
  }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  // One row per scenario in plain language, grouped by area, open failures first inside each group.
  function renderAttack(d) {
    var box = $("runs"); box.replaceChildren();
    var sum = d.summary, titles = (d.titles && d.titles.scenarios) || {}, areas = (d.titles && d.titles.areas) || {};
    if (!sum || !sum.by_scenario) {
      var runs = d.runs || [], bad = runs.filter(function (r) { return !r.ok; }).length;
      $("attack-head").textContent = runs.length ? plural(bad, "run", "runs") + " failed of " + runs.length : "No harness results yet";
      return;
    }
    var groups = {}, open = 0, total = 0;
    Object.keys(sum.by_scenario).forEach(function (id) {
      var meta = titles[id] || { area: "other", title: id }, r = sum.by_scenario[id];
      (groups[meta.area] = groups[meta.area] || []).push({ id: id, title: meta.title, r: r });
      if (meta.area !== "control") { total += 1; if (r.failed > 0) open += 1; }
    });
    Object.keys(groups).forEach(function (area) {
      box.appendChild(cell("div", "group", areas[area] || area));
      groups[area].sort(function (a, b) { return b.r.failed - a.r.failed; }).forEach(function (row) {
        var bad = row.r.failed > 0, line = document.createElement("div"); line.className = "run";
        line.appendChild(cell("span", bad ? "fail" : "ok", bad ? "OPEN " : "PASS "));
        line.appendChild(document.createTextNode(row.title + (bad ? " · " + row.r.failed + " of " + row.r.runs + " runs failed, " + plural(row.r.lost, "op", "ops") + " lost" : "")));
        line.title = row.id;
        box.appendChild(line);
      });
    });
    $("attack-head").textContent = open === 0 ? "All " + total + " scenarios pass" : plural(open, "scenario", "scenarios") + " of " + total + " still " + (open === 1 ? "fails" : "fail");
    $("attack-note").textContent = "Committed summary of a real harness run: " + sum.runs + " simulated runs (seeds " + sum.seeds[0] + " to " + (sum.seeds[1] - 1) +
      "), " + sum.failed + " runs failed, " + sum.lost_ops + " operations lost. Simulated phones, not the phones in this room. The control row is not counted." +
      ((d.runs || []).length ? " A shorter local run of " + d.runs.length + " runs is also on this machine." : "");
  }

  function renderRace(d) {
    $("race-round").textContent = d.round ? String(d.round) : "–";
    $("r-phones").textContent = String(d.phones); $("r-orders").textContent = String(d.orders);
    $("r-answers").textContent = String(d.merge_answers); $("r-answered").textContent = String(d.answered);
    $("race-head").textContent = !d.round ? "Start a round, then scan to race"
      : (d.agree ? d.payoff : d.phones + " phones racing · " + d.orders + " different orders");
    var rows = $("race-rows"); rows.replaceChildren();
    d.players.forEach(function (p, i) {
      var tr = document.createElement("tr");
      tr.appendChild(cell("td", "", String(i + 1))); tr.appendChild(cell("td", "", p.name));
      tr.appendChild(cell("td", "qty", String(p.points))); tr.appendChild(cell("td", "", String(p.correct)));
      rows.appendChild(tr);
    });
  }

  var TABS = ["ledger", "attack", "proof", "race"];
  function tab(name) {
    TABS.forEach(function (t) {
      $(t).className = name === t ? "" : "hidden"; $("tab-" + t).setAttribute("aria-pressed", String(name === t));
    });
  }
  $("tab-ledger").addEventListener("click", function () { tab("ledger"); });
  $("tab-attack").addEventListener("click", function () { tab("attack"); });
  $("tab-proof").addEventListener("click", function () { tab("proof"); });
  $("tab-race").addEventListener("click", function () { tab("race"); });
  $("break-referee").addEventListener("click", function () {
    $("break-result").textContent = "Running the referee…";
    fetch("/api/referee/break", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mutant: "lww-picks-earliest" }) })
      .then(function (r) { return r.json(); })
      .then(function (d) { $("break-result").textContent = d.caught ? "Caught: " + d.reason : "The referee did NOT catch this broken merge."; })
      .catch(function () { $("break-result").textContent = "Could not reach the referee."; });
  });
  $("race-start").addEventListener("click", function () {
    fetch("/api/race/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(poll);
  });
  $("seed").addEventListener("click", function () { fetch("/api/seed", { method: "POST" }).then(poll); });
  function panels() {
    var origin = location.origin, tok = typeof window !== "undefined" && window.StillCountsToken ? window.StillCountsToken() : null;
    var join = tok ? "?t=" + encodeURIComponent(tok) : "";         // the QR carries the join token; the text under it does not
    $("qr").src = "/api/qr.svg?text=" + encodeURIComponent(origin + "/" + join);
    $("joinurl").textContent = origin.replace(/^https?:\/\//, "");
    $("race-qr").src = "/api/qr.svg?text=" + encodeURIComponent(origin + "/race" + join);
    $("race-url").textContent = origin.replace(/^https?:\/\//, "") + "/race";
    fetch("/api/referee").then(function (r) { return r.json(); }).then(function (d) {
      $("p-referee").textContent = d.killed + " / " + d.total;
      if (d.mutmut) {
        $("p-mutmut").textContent = d.mutmut.killed + " / " + d.mutmut.total;
        $("p-mutmut-sub").textContent = "mutations of crdt.py killed by the tests (mutmut); the " + d.mutmut.survived + " survivors are equivalent mutants, explained in docs/mutation.md";
      }
    }).catch(function () {});
    fetch("/api/devin").then(function (r) { return r.json(); }).then(function (d) {
      $("p-devin").textContent = d.sessions + " sessions";
      $("p-devin-sub").textContent = d.prs + " pull requests merged" + (d.issues ? " · " + d.issues + " issues filed" : "") + " · see docs/devin.md";
    }).catch(function () {});
  }
  panels(); setInterval(panels, 15000);
  setInterval(poll, 1000); poll();
})();
