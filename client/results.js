// Results dashboard: reads /api/learn/results and shows earned stages and the adaptation log. Plain DOM, no heart rates.
(function (root) {
  "use strict";
  var KINDS = ["predict", "bug", "write", "explain"];

  function summarize(data) {
    var s = (data && data.summary) || { events: 0, lowered: 0, restored: 0 };
    return s.events + " adaptation" + (s.events === 1 ? "" : "s") + " logged: " + s.lowered + " lowered the difficulty, " + s.restored + " restored it.";
  }

  function eventLine(e) {
    var when = new Date(e.at * 1000).toISOString().slice(11, 19);
    return when + "  difficulty " + e.prev + " to " + e.level + (e.stage ? " (stage " + e.stage + ")" : "") + "  [" + e.kind + "]  " + e.reason;
  }

  function learnerLine(l) {
    var stages = Object.keys(l.stages || {}).sort().map(function (id) {
      var p = l.stages[id];
      return id + ": " + (KINDS.filter(function (k) { return p[k]; }).join("+") || "none");
    });
    return l.learner + "  " + l.earned + " earned  " + stages.join(" | ");
  }

  function render(doc, data) {
    function fill(id, items, empty) {
      var box = doc.getElementById(id);
      box.replaceChildren.apply(box, items.length ? items.map(function (t) { var p = doc.createElement("p"); p.className = "row"; p.textContent = t; return p; })
        : [Object.assign(doc.createElement("p"), { className: "sub", textContent: empty })]);
    }
    doc.getElementById("summary").textContent = summarize(data);
    fill("learners", (data.learners || []).map(learnerLine), "Nobody has earned a stage yet.");
    fill("events", (data.adaptations || []).slice().reverse().map(eventLine), "No difficulty changes yet. Move the heart-rate simulator in the chat to see one.");
  }

  var api = { summarize: summarize, eventLine: eventLine, learnerLine: learnerLine, render: render };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.Results = api;
  if (root.document && root.document.getElementById("events")) {
    root.fetch("/api/learn/results").then(function (r) { return r.json(); }).then(function (d) { render(root.document, d); },
      function () { root.document.getElementById("summary").textContent = "Needs a signal: results live on the server."; });
  }
})(typeof self !== "undefined" ? self : this);
