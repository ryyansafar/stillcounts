// Understanding Map, gap score, quiz variants, line diff and proof card. Pure functions: no DOM, no network.
// Every number here is computed from what the learner actually did on this device (checkpoints passed, drafts, attempts).
(function (root) {
  "use strict";
  var LEVELS = ["unseen", "predicted", "bug", "written", "explained"];
  var LEVEL_LABEL = { unseen: "Unseen", predicted: "Predicted", bug: "Bug spotted", written: "Written", explained: "Explained" };
  var WEIGHT = { unseen: 0, predicted: 0.25, bug: 0.5, written: 0.75, explained: 1 };
  var KIND_LEVEL = { predict: "predicted", bug: "bug", write: "written", explain: "explained" };
  var GRADED = ["predict", "bug", "write"];

  function nonBlank(text) {
    return String(text || "").split("\n").filter(function (l) { return l.trim() !== ""; });
  }

  function stageKey(stage, kind) { return stage.id + "/" + kind; }

  // the highest level reached in a stage. `earned` stages (server says unlocked) count predict, bug and write as done.
  function levelOf(stage, passed, kinds) {
    var best = 0;
    (kinds || ["predict", "bug", "write", "explain"]).forEach(function (k) {
      var done = passed[stageKey(stage, k)] || (stage.earned && k !== "explain");
      if (done) best = Math.max(best, LEVELS.indexOf(KIND_LEVEL[k]));
    });
    return LEVELS[best];
  }

  // line indexes of `def name` and its indented body
  function functionRange(code, name) {
    var lines = String(code || "").split("\n"), start = -1, i;
    for (i = 0; i < lines.length; i++) {
      if (new RegExp("^def\\s+" + String(name).replace(/[^\w]/g, "") + "\\b").test(lines[i])) { start = i; break; }
    }
    if (start < 0) return null;
    var end = start;
    for (i = start + 1; i < lines.length; i++) {
      if (lines[i].trim() === "") continue;
      if (/^\s/.test(lines[i])) end = i; else break;
    }
    return { start: start, end: end };
  }

  // one entry per line of real code: { text, level }. The function the learner wrote earns up to "written";
  // the lines around it only reach the level their checkpoints cover (predict, bug, explain), never "written".
  function lineStates(stage, passed, code) {
    var lines = String(code || "").split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    var fn = functionRange(code, (stage.checkpoints && stage.checkpoints.write && stage.checkpoints.write.function) || "");
    var inside = levelOf(stage, passed), outside = levelOf(stage, passed, ["predict", "bug", "explain"]);
    return lines.map(function (text, i) {
      var inFn = fn && i >= fn.start && i <= fn.end;
      return { text: text, level: text.trim() === "" ? "blank" : inFn ? inside : outside, inWritten: !!inFn };
    });
  }

  // share of revealed lines that are understood, weighted by how they were earned. gap = 100 - understood.
  function gapScore(stages, passed, codes) {
    var lines = 0, weighted = 0, byLevel = { unseen: 0, predicted: 0, bug: 0, written: 0, explained: 0 }, locked = 0;
    stages.forEach(function (s) {
      var code = codes[s.id] || s.code;
      if (!code) { locked += 1; return; }
      lineStates(s, passed, code).forEach(function (l) {
        if (l.level === "blank") return;
        lines += 1; weighted += WEIGHT[l.level]; byLevel[l.level] += 1;
      });
    });
    if (!lines) return { lines: 0, locked: locked, understood: null, gap: null, byLevel: byLevel };
    var understood = Math.round(100 * weighted / lines);
    return { lines: lines, locked: locked, understood: understood, gap: 100 - understood, byLevel: byLevel };
  }

  function writtenLines(stage, passed, drafts) {
    if (!passed[stageKey(stage, "write")]) return 0;
    var draft = drafts[stageKey(stage, "write")];
    return draft ? nonBlank(draft).filter(function (l) { return !/^\s*#/.test(l); }).length : 0;
  }

  // lines Devin's stub gives you (signature and docstring); "raise NotImplementedError" is the hole you fill
  function scaffoldLines(stage) {
    var w = stage.checkpoints && stage.checkpoints.write, stub = (w && (w.stub || w.starter)) || "";
    return nonBlank(stub).filter(function (l) { return !/NotImplementedError|^\s*pass\s*$/.test(l); }).length;
  }

  function measured(stages, st) {
    var tries = 0, firstTry = 0, hints = 0, attempts = 0, unlocked = 0, written = 0, devin = 0;
    Object.keys(st.attempts || {}).forEach(function (k) {
      var kind = k.split("/")[1], a = st.attempts[k];
      attempts += a.tries;
      if (GRADED.indexOf(kind) >= 0) { tries += 1; if (a.firstTry) firstTry += 1; }
    });
    Object.keys(st.hints || {}).forEach(function (k) { hints += st.hints[k]; });
    var perStage = stages.map(function (s) {
      var code = st.codes[s.id] || s.code, n = code ? nonBlank(code).length : 0, w = writtenLines(s, st.passed, st.drafts);
      var d = scaffoldLines(s), got = w > 0 ? d : 0;
      unlocked += n; written += w; devin += got;
      return { id: s.id, title: s.title, unlocked: n, written: w, devin: got, scaffold: d };
    });
    return {
      attempts: attempts, graded: tries, firstTry: firstTry, firstTryPct: tries ? Math.round(100 * firstTry / tries) : null,
      hints: hints, unlockedLines: unlocked, writtenLines: written, devinScaffoldLines: devin, perStage: perStage,
      stagesEarned: stages.filter(function (s) { return s.earned || GRADED.every(function (k) { return !s.checkpoints[k] || st.passed[stageKey(s, k)]; }); }).length,
      gap: gapScore(stages, st.passed, st.codes),
    };
  }

  // ---- quiz: lesson cases with the numbers changed, graded by the real merge in the browser ----
  function variantCase(c, v) {
    var ops = (c.ops || []).map(function (o) {
      var n = {}; Object.keys(o).forEach(function (k) { n[k] = o[k]; });
      if (o.kind === "adjust" && typeof o.value === "number") {
        var mag = Math.abs(o.value) + 3 * v; n.value = o.value < 0 ? -mag : mag;
      } else if ((o.kind === "create" || o.kind === "note") && typeof o.value === "string") {
        n.value = o.value + " " + String.fromCharCode(64 + v);
      }
      return n;
    });
    return { id: c.id + "-v" + v, ops: ops, item: c.item, field: c.field || "qty",
             question: "After the phones merge, what is the " + (c.field === "oversold" ? "oversold status (yes or no)" : c.field === "qty" || !c.field ? "quantity" : c.field) + " of " + c.item + "?" };
  }

  function buildQuiz(stages, phase) {
    var v = phase === "after" ? 2 : 1, out = [];
    stages.forEach(function (s) {
      var cases = (s.checkpoints.predict && s.checkpoints.predict.cases) || [];
      cases.slice(0, 2).forEach(function (c) {
        var q = variantCase(c, v); q.stage = s.id; q.qid = s.id + ":" + q.id; out.push(q);
      });
    });
    return out;
  }

  // ---- line diff (longest common subsequence), for "your code vs the real code" ----
  function diffLines(a, b) {
    var x = String(a || "").replace(/\s+$/, "").split("\n").map(function (l) { return l.replace(/\s+$/, ""); });
    var y = String(b || "").replace(/\s+$/, "").split("\n").map(function (l) { return l.replace(/\s+$/, ""); });
    var m = x.length, n = y.length, t = [], i, j;
    for (i = 0; i <= m; i++) { t.push(new Array(n + 1).fill(0)); }
    for (i = m - 1; i >= 0; i--) for (j = n - 1; j >= 0; j--) {
      t[i][j] = x[i] === y[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1]);
    }
    var out = []; i = 0; j = 0;
    while (i < m && j < n) {
      if (x[i] === y[j]) { out.push({ t: "same", text: x[i] }); i++; j++; }
      else if (t[i + 1][j] >= t[i][j + 1]) { out.push({ t: "del", text: x[i++] }); }
      else { out.push({ t: "add", text: y[j++] }); }
    }
    while (i < m) out.push({ t: "del", text: x[i++] });
    while (j < n) out.push({ t: "add", text: y[j++] });
    return out;
  }

  // the real source of one function, cut out of a stage's code
  function functionSource(code, name) {
    var r = functionRange(code, name);
    return r ? String(code).split("\n").slice(r.start, r.end + 1).join("\n") : "";
  }

  // ---- proof of understanding: plain text and a standalone SVG. Self-reported, honest about it. ----
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function proofFacts(stages, st, name) {
    var m = measured(stages, st), q = st.quiz || {};
    return { name: String(name || "").trim().slice(0, 40), stages: m.stagesEarned, total: stages.length, firstTryPct: m.firstTryPct,
             understood: m.gap.understood, gap: m.gap.gap, lines: m.gap.lines, written: m.writtenLines, hints: m.hints,
             before: q.before || null, after: q.after || null };
  }

  function proofText(f) {
    var q = f.before && f.after ? " Quiz " + f.before.score + "/" + f.before.total + " before, " + f.after.score + "/" + f.after.total + " after." : "";
    return (f.name ? f.name + ": " : "") + "Earned " + f.stages + " of " + f.total + " stages of the Still Counts merge code." +
      (f.understood === null ? "" : " Understood " + f.understood + "% of the code I was shown (gap " + f.gap + "%).") +
      (f.firstTryPct === null ? "" : " First-try pass rate " + f.firstTryPct + "%.") + " I wrote " + f.written + " lines myself with " + f.hints + " hints." + q +
      " Checked by plain code on my own device, self-reported, not an exam.";
  }

  function proofSvg(f) {
    var rows = [["Stages earned", f.stages + " of " + f.total],
      ["Understood of code shown", f.understood === null ? "none yet" : f.understood + "%"],
      ["Pasted-vs-understood gap", f.gap === null ? "n/a" : f.gap + "%"],
      ["First-try pass rate", f.firstTryPct === null ? "n/a" : f.firstTryPct + "%"],
      ["Lines I wrote / hints", f.written + " / " + f.hints],
      ["Quiz before to after", f.before && f.after ? f.before.score + "/" + f.before.total + " to " + f.after.score + "/" + f.after.total : "not taken"]];
    var body = rows.map(function (r, i) {
      var y = 190 + i * 46;
      return '<text x="40" y="' + y + '" fill="#9a9a93" font-size="20">' + esc(r[0]) + '</text><text x="680" y="' + y + '" fill="#f2f2ee" font-size="24" font-weight="700" text-anchor="end">' + esc(r[1]) + "</text>";
    }).join("");
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 500" width="720" height="500" font-family="system-ui,sans-serif">' +
      '<rect width="720" height="500" rx="28" fill="#0a0a0a"/><rect x="2" y="2" width="716" height="496" rx="27" fill="none" stroke="#3dd68c" stroke-width="3"/>' +
      '<text x="40" y="72" fill="#3dd68c" font-size="20" font-weight="700" letter-spacing="3">PROOF OF UNDERSTANDING</text>' +
      '<text x="40" y="124" fill="#f2f2ee" font-size="40" font-weight="800">' + esc(f.name || "Earn the Code") + "</text>" +
      '<text x="40" y="154" fill="#9a9a93" font-size="18">Still Counts, Course 1: an offline-first merge</text>' + body +
      '<text x="40" y="474" fill="#9a9a93" font-size="15">Graded by plain code on this device. Self-reported, not an exam.</text></svg>';
  }

  var api = { LEVELS: LEVELS, LEVEL_LABEL: LEVEL_LABEL, WEIGHT: WEIGHT, levelOf: levelOf, functionRange: functionRange,
              lineStates: lineStates, gapScore: gapScore, writtenLines: writtenLines, scaffoldLines: scaffoldLines, measured: measured,
              variantCase: variantCase, buildQuiz: buildQuiz, diffLines: diffLines, functionSource: functionSource,
              proofFacts: proofFacts, proofText: proofText, proofSvg: proofSvg };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.Understand = api;
})(typeof self !== "undefined" ? self : this);
