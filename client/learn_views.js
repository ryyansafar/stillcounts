// The non-lesson views of Learn: Understanding Map, Results dashboard, Proof card, Quiz and the reveal of the real code.
// Each builder takes ctx = { h, st, stages, U, ... } and returns an array of nodes. No network here; everything shown is
// computed from what this learner did on this device.
(function (root) {
  "use strict";
  var NODE = typeof module !== "undefined" && module.exports;
  var U = NODE ? require("./understand.js") : root.Understand;
  var MARK = { unseen: "·", predicted: "P", bug: "B", written: "W", explained: "E" };

  function legend(ctx) {
    return ctx.h("ul", { className: "legend", "aria-label": "Colour key" }, U.LEVELS.map(function (l) {
      return ctx.h("li", { className: "lv-" + l }, [ctx.h("span", { className: "swatch" }), U.LEVEL_LABEL[l] + " (" + MARK[l] + ")"]);
    }));
  }

  // one <pre> of real code, one span per line, coloured by what the learner earned. textContent stays the exact code.
  function codePre(ctx, stage, code, attrs) {
    var h = ctx.h, lines = U.lineStates(stage, ctx.st.passed, code), kids = [];
    lines.forEach(function (l, i) {
      if (i) kids.push("\n");
      kids.push(h("span", { className: "ln lv-" + l.level, "data-level": l.level, "data-m": MARK[l.level] || "", text: l.text }));
    });
    var a = Object.assign({ className: "code real map" }, attrs || {});
    return h("pre", a, kids);
  }

  function stat(h, label, value, note) {
    return h("div", { className: "stat" }, [h("div", { className: "num", text: String(value) }), h("div", { className: "lab", text: label }),
      note ? h("div", { className: "note2", text: note }) : null]);
  }

  function meter(h, label, a, b, aLabel, bLabel) {
    var total = Math.max(a + b, 1);
    return h("div", { className: "meter", "data-meter": label }, [
      h("div", { className: "mlabel", text: label }),
      h("div", { className: "mbar", role: "img", "aria-label": aLabel + " " + a + ", " + bLabel + " " + b }, [
        h("span", { className: "m-a", style: "width:" + Math.round(100 * a / total) + "%" }),
        h("span", { className: "m-b", style: "width:" + Math.round(100 * b / total) + "%" })]),
      h("div", { className: "mtext", text: aLabel + " " + a + " · " + bLabel + " " + b })]);
  }

  // ---- Understanding Map ----
  function mapView(ctx) {
    var h = ctx.h, g = U.gapScore(ctx.stages, ctx.st.passed, ctx.st.codes), kids = [];
    kids.push(h("h2", { text: "Understanding Map" }));
    kids.push(h("p", { className: "sub", text: "Every line of the app's merge code, coloured by what you earned. Pasted code is all grey. Earned code lights up." }));
    kids.push(h("div", { className: "card gap", "data-gap": g.gap === null ? "none" : String(g.gap) }, [
      h("div", { className: "gapnum", text: g.gap === null ? "no code yet" : g.gap + "%" }),
      h("div", { className: "gaplab", text: "Pasted-vs-understood gap" }),
      h("p", { className: "sub", text: g.gap === null
        ? "No reference code revealed yet. Earn a stage (predict, find the bug, write) and its reference code appears here."
        : "Of the " + g.lines + " lines you have been shown, you understand " + g.understood + "%. Lines count 25% for predicting, 50% for the bug, 75% for writing and 100% for explaining." +
          (g.locked ? " " + g.locked + " stage" + (g.locked === 1 ? " is" : "s are") + " not revealed yet, so their lines are not counted." : "") })]));
    kids.push(legend(ctx));
    ctx.stages.forEach(function (s) {
      var code = ctx.st.codes[s.id] || s.code, lvl = U.levelOf(s, ctx.st.passed);
      var head = h("h3", {}, [s.title, h("span", { className: "badge lv-" + lvl, text: U.LEVEL_LABEL[lvl] })]);
      var body = code ? codePre(ctx, s, code, { "data-map": s.id })
        : h("div", { className: "locked-code", "data-map": s.id }, [h("span", { className: "bars" }), h("p", { className: "sub", text: "Not revealed yet. Predict, find the bug and write to get the reference code." })]);
      kids.push(h("section", { className: "card mapstage", "data-mapstage": s.id }, [head, body]));
    });
    return kids;
  }

  // ---- Results dashboard ----
  function quizCard(ctx) {
    var h = ctx.h, q = ctx.st.quiz || {}, any = ctx.stages.some(function (s) { return ctx.earned(s); }), touched = Object.keys(ctx.st.passed).length > 0;
    var kids = [h("h3", { text: "Before and after quiz" })];
    function line(label, r) { return h("p", { className: "qline", "data-quiz": label, text: label + ": " + (r ? r.score + " of " + r.total : "not taken") }); }
    kids.push(line("Before", q.before), line("After", q.after));
    if (q.before && q.after) {
      var d = q.after.score - q.before.score;
      kids.push(h("p", { className: "result " + (d > 0 ? "good" : "warn"), "data-quiz": "delta", text: (d >= 0 ? "+" : "") + d + " questions between your before and after." }));
    }
    kids.push(h("p", { className: "sub", text: "Questions are lesson cases with the numbers changed, graded by the real merge in your browser. The after quiz uses different numbers from the before quiz." }));
    var row = [];
    if (!q.before && !touched) row.push(ctx.button("Take the before quiz", "quiz-before", function () { ctx.actions.startQuiz("before"); }));
    else if (!q.before) kids.push(h("p", { className: "sub", text: "A before quiz is only fair before you earn anything, so none was taken on this phone." }));
    if (any) row.push(ctx.button(q.after ? "Retake the after quiz" : "Take the after quiz", "quiz-after", function () { ctx.actions.startQuiz("after"); }));
    else if (q.before || !touched) kids.push(h("p", { className: "sub", text: "The after quiz opens when you earn your first stage." }));
    if (row.length) kids.push(h("div", { className: "row" }, row));
    return h("section", { className: "card", "data-card": "quiz" }, kids);
  }

  function resultsView(ctx) {
    var h = ctx.h, m = U.measured(ctx.stages, ctx.st), kids = [];
    kids.push(h("h2", { text: "Measured results" }));
    kids.push(h("p", { className: "sub", text: "Counted on this phone from your own attempts. Nothing is estimated and nothing leaves the device." }));
    kids.push(h("div", { className: "stats" }, [
      stat(h, "first-try pass rate", m.firstTryPct === null ? "n/a" : m.firstTryPct + "%", m.graded ? m.firstTry + " of " + m.graded + " graded checkpoints" : "no attempts yet"),
      stat(h, "hints used", m.hints, "each hint is counted"),
      stat(h, "stages earned", m.stagesEarned + "/" + ctx.stages.length),
      stat(h, "attempts", m.attempts)]));
    kids.push(quizCard(ctx));
    kids.push(h("section", { className: "card", "data-card": "lines" }, [h("h3", { text: "Lines unlocked vs lines written" }),
      meter(h, "All stages", m.unlockedLines, m.writtenLines, "unlocked", "written by you"),
      h("p", { className: "sub", text: "Unlocked is the reference code revealed to you after you earned the stage. Written is what you typed that the referee passed." })]));
    var fade = [h("h3", { text: "Fading scaffolds: Devin's lines vs yours" }),
      h("p", { className: "sub", text: "Devin gives each stage a signature and a docstring. You write the body. These are the real counts per stage, so you can see whether the help fades." })];
    m.perStage.forEach(function (p) {
      fade.push(meter(h, p.title, p.devin, p.written, "Devin gave", "you wrote"));
    });
    var dv = m.devinScaffoldLines, you = m.writtenLines, share = dv + you ? Math.round(100 * dv / (dv + you)) : null;
    fade.push(h("p", { className: "result " + (share === null ? "" : "good"), "data-fade": share === null ? "none" : String(share),
      text: share === null ? "Pass a write checkpoint to see Devin's share shrink." : "Devin's share of the lines you have earned: " + share + "%. Yours: " + (100 - share) + "%." }));
    kids.push(h("section", { className: "card", "data-card": "fade" }, fade));
    kids.push(h("section", { className: "card", "data-card": "evidence" }, [h("h3", { text: "Where this came from" }),
      h("p", { className: "sub", text: "Devin built the ledger, the referee and these lessons. Its sessions, merged PRs and rejected work are listed on the Board's Proof tab, wins and misses alike. Cloud Devin quota ran out, so later work is labelled Devin CLI, local." }),
      h("a", { className: "navlink", href: "/board", text: "Open the Board" })]));
    return kids;
  }

  // ---- Proof card ----
  function proofView(ctx) {
    var h = ctx.h, f = U.proofFacts(ctx.stages, ctx.st, ctx.st.name), kids = [];
    kids.push(h("h2", { text: "Proof of understanding" }));
    kids.push(h("p", { className: "sub", text: "A card you can share. It shows only what the app measured. It is self-reported and graded on your own device, so it is not an exam." }));
    var input = h("input", { type: "text", className: "field", maxlength: "40", placeholder: "Your name (optional)", "aria-label": "Name on the card", "data-input": "proof-name", value: ctx.st.name || "" });
    input.addEventListener("input", function () { ctx.actions.setName(input.value); });
    kids.push(input);
    if (!f.stages && f.understood === null) {
      kids.push(h("p", { className: "card empty", "data-status": "proof-empty", text: "Nothing to prove yet. Earn your first stage and your card fills in." }));
    }
    var rows = [["Stages earned", f.stages + " of " + f.total], ["Understood of code shown", f.understood === null ? "none yet" : f.understood + "%"],
      ["Pasted-vs-understood gap", f.gap === null ? "n/a" : f.gap + "%"], ["First-try pass rate", f.firstTryPct === null ? "n/a" : f.firstTryPct + "%"],
      ["Lines I wrote", String(f.written)], ["Hints used", String(f.hints)],
      ["Quiz before to after", f.before && f.after ? f.before.score + "/" + f.before.total + " to " + f.after.score + "/" + f.after.total : "not taken"]];
    kids.push(h("section", { className: "card proof", "data-proof": "card" }, [h("div", { className: "kicker", text: "PROOF OF UNDERSTANDING" }), h("div", { className: "who", text: f.name || "Earn the Code" }),
      h("div", { className: "sub", text: "Still Counts, Course 1: an offline-first merge" })].concat(rows.map(function (r) {
        return h("div", { className: "prow" }, [h("span", { text: r[0] }), h("b", { text: r[1] })]); }))));
    kids.push(h("div", { className: "row" }, [
      ctx.button("Share", "share", function () { ctx.actions.share(U.proofText(f)); }),
      ctx.button("Copy text", "copy", function () { ctx.actions.copy(U.proofText(f)); }),
      h("a", { className: "navlink", "data-action": "download", download: "earn-the-code-proof.svg", href: "data:image/svg+xml;charset=utf-8," + encodeURIComponent(U.proofSvg(f)), text: "Download card (SVG)" })]));
    kids.push(h("p", { className: "result", "data-result": "proof", "aria-live": "polite" }));
    return kids;
  }

  // ---- Quiz ----
  function quizView(ctx) {
    var h = ctx.h, qz = ctx.quiz, kids = [];
    kids.push(h("h2", { text: qz.phase === "before" ? "Before quiz" : "After quiz" }));
    kids.push(h("p", { className: "sub", text: "Predict what the app shows after the phones merge. No hints, no help. The real merge checks you when you finish." + (qz.phase === "before" ? " This is your baseline and can only be taken once." : "") }));
    qz.questions.forEach(function (q, i) {
      var number = typeof ctx.predictAnswer(q) === "number";
      var input = h("input", { type: number ? "number" : "text", className: "field", "data-input": "quiz:" + q.qid, "aria-label": "Answer to question " + (i + 1), placeholder: ctx.hintFor(q) });
      if (number) input.setAttribute("inputmode", "numeric");
      qz.inputs[q.qid] = input;
      var seen = {};
      kids.push(h("div", { className: "case", "data-qcase": q.qid }, [h("p", { text: (i + 1) + ". " + q.question }),
        h("ol", { className: "ops" }, q.ops.map(function (op) { return h("li", { text: ctx.describeOp(op, seen) }); })), input,
        h("p", { className: "qmark", "data-qmark": q.qid })]));
    });
    kids.push(h("p", { className: "result", "data-result": "quiz", "aria-live": "polite" }));
    kids.push(h("div", { className: "row" }, [ctx.button("Finish quiz", "quiz-submit", function () { ctx.actions.submitQuiz(); }),
      ctx.button("Cancel", "quiz-cancel", function () { ctx.actions.setView("results"); }, "ghost")]));
    return kids;
  }

  // ---- reveal the real code, with your version diffed against it ----
  function revealView(ctx, stage, code) {
    var h = ctx.h, kids = [codePre(ctx, stage, code, { "data-code": stage.id })];
    kids.push(legend(ctx));
    var w = stage.checkpoints.write || {}, real = U.functionSource(code, w.function), draft = ctx.st.drafts[stage.id + "/write"];
    if (real && draft && ctx.st.passed[stage.id + "/write"]) {
      var diff = U.diffLines(draft, real), kidsD = [];
      diff.forEach(function (d, i) {
        if (i) kidsD.push("\n");
        kidsD.push(h("span", { className: "ln df-" + d.t, "data-diff": d.t, "data-m": d.t === "add" ? "+" : d.t === "del" ? "-" : " ", text: d.text }));
      });
      var same = diff.every(function (d) { return d.t === "same"; });
      kids.push(h("h4", { text: "Your function vs the real one" }));
      kids.push(h("p", { className: "sub", text: same ? "Identical to the real code."
        : "Green plus lines are in the real code only. Red minus lines are only in yours. Both passed the referee, so differences are style or extra safety." }));
      kids.push(h("pre", { className: "code diff", "data-diff-of": stage.id }, kidsD));
    }
    var b = stage.checkpoints.bug || {};
    var shown = Array.isArray(b.excerpt) ? b.excerpt : (b.snippet ? String(b.snippet).split("\n") : null);
    if (shown && (typeof b.line_index === "number" || b.line) && ctx.st.passed[stage.id + "/bug"]) {
      var bk = [];
      shown.forEach(function (l, i) {
        var changed = typeof b.line_index === "number" ? i === b.line_index : l.trim() === String(b.line).trim();
        if (i) bk.push("\n");
        bk.push(h("span", { className: "ln " + (changed ? "df-del" : ""), "data-m": changed ? "!" : " ", text: l }));
      });
      kids.push(h("h4", { text: "The bug you found" }), h("pre", { className: "code diff", "data-bug-line": stage.id }, bk));
    }
    return kids;
  }

  var api = { mapView: mapView, resultsView: resultsView, proofView: proofView, quizView: quizView, revealView: revealView, codePre: codePre };
  if (NODE) { module.exports = api; return; }
  root.LearnViews = api;
})(typeof self !== "undefined" ? self : this);
