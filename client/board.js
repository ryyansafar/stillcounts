(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }
  function cell(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; e.textContent = text; return e; }

  function renderLedger(data) {
    var rows = $("rows"); rows.replaceChildren();
    Object.keys(data.state).forEach(function (id) {
      var s = data.state[id]; if (!s.alive) return;
      var tr = document.createElement("tr");
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
    var over = data.stats.oversold.length, fresh = (data.phones || []).filter(function (p) { return p.age_s < 15; });
    var behind = fresh.filter(function (p) { return !p.caught_up; }).length;
    $("s-caught").textContent = fresh.length ? (fresh.length - behind) + " / " + fresh.length : "–";
    $("headline").textContent = data.stats.ops === 0 ? "Waiting for phones"
      : over ? over + " item(s) need a human"
      : !fresh.length ? data.stats.ops + " operations merged"
      : behind ? behind + " of " + fresh.length + " phone(s) still catching up"
      : "Every phone holds every operation";
  }

  function poll() {
    fetch("/api/state").then(function (r) { return r.json(); }).then(function (d) {
      $("conn").className = "pill ok"; $("conn").textContent = "live"; renderLedger(d);
    }).catch(function () { $("conn").className = "pill bad"; $("conn").textContent = "board offline"; });
    fetch("/api/chaos").then(function (r) { return r.json(); }).then(renderRuns).catch(function () {});
    fetch("/api/race/board").then(function (r) { return r.json(); }).then(renderRace).catch(function () {});
  }

  function harnessLine(text) { $("harness-line").textContent = text; }

  // Per-scenario rows from the committed summary, used when the full per-run file is not on this machine.
  function renderSummary(sum) {
    var box = $("runs");
    var names = Object.keys(sum.by_scenario || {}), open = 0;
    names.sort(function (a, b) { return sum.by_scenario[b].failed - sum.by_scenario[a].failed; });
    names.forEach(function (n) {
      var r = sum.by_scenario[n], bad = r.failed > 0; if (bad) open += 1;
      var line = document.createElement("div"); line.className = "run";
      line.appendChild(cell("span", bad ? "fail" : "ok", bad ? "OPEN " : "PASS "));
      line.appendChild(document.createTextNode(n + " · " + r.failed + " of " + r.runs + " runs failed · " + r.lost + " ops lost"));
      box.appendChild(line);
    });
    $("attack-head").textContent = open + " of " + names.length + " scenarios still fail";
    $("attack-note").textContent = "Committed summary of a real harness run (" + sum.runs + " simulated runs, seeds " + sum.seeds[0] + " to " + (sum.seeds[1] - 1) +
      "): " + sum.failed + " runs failed, " + sum.lost_ops + " operations lost. Simulated phones, not the phones in this room.";
    harnessLine("Attack harness (simulated, not these phones): " + sum.failed + " of " + sum.runs + " runs failed in " + open + " scenarios. See the Attack tab.");
  }

  function renderRuns(d) {
    var box = $("runs"); box.replaceChildren();
    var runs = d.runs || [], total = 0, lost = 0, bad = 0;
    if (!runs.length && d.summary && d.summary.by_scenario) { renderSummary(d.summary); return; }
    runs.forEach(function (r) {
      total += r.ops || 0; lost += r.lost || 0; if (!r.ok) bad += 1;
      var line = document.createElement("div"); line.className = "run";
      line.appendChild(cell("span", r.ok ? "ok" : "fail", r.ok ? "PASS " : "FAIL "));
      line.appendChild(document.createTextNode("seed " + r.seed + " · " + (r.scenario || "") + " · " + (r.ops || 0) +
        " ops · " + (r.conflicts || 0) + " conflicts · lost " + (r.lost || 0) + (r.label ? " · " + r.label : "")));
      box.appendChild(line);
    });
    $("attack-head").textContent = runs.length ? runs.length + " runs · " + total + " ops · " + lost + " lost in the harness · " + bad + " failed" : "No harness results yet";
    harnessLine(runs.length ? "Attack harness (simulated, not these phones): " + bad + " of " + runs.length + " runs failed, " + lost + " ops lost. See the Attack tab." : "");
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

  var TABS = ["ledger", "attack", "race"];
  function tab(name) {
    TABS.forEach(function (t) {
      $(t).className = name === t ? "" : "hidden"; $("tab-" + t).setAttribute("aria-pressed", String(name === t));
    });
  }
  $("tab-ledger").addEventListener("click", function () { tab("ledger"); });
  $("tab-attack").addEventListener("click", function () { tab("attack"); });
  $("tab-race").addEventListener("click", function () { tab("race"); });
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
    }).catch(function () {});
    fetch("/api/devin").then(function (r) { return r.json(); }).then(function (d) {
      $("p-devin").textContent = d.sessions + " sessions";
      $("p-devin-sub").textContent = d.prs + " pull requests merged" + (d.issues ? " · " + d.issues + " issues filed" : "") + " · see docs/devin.md";
    }).catch(function () {});
  }
  panels(); setInterval(panels, 15000);
  setInterval(poll, 1000); poll();
})();
