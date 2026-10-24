// View model for the chat thread: pure functions from chat state to plain blocks. The DOM code (chat.js) only turns blocks
// into elements, so everything that decides what the learner may see is testable in node.
// A block is { key, sig, type, ... }. `sig` changes whenever the block's content changes, so unchanged blocks keep their DOM
// (and the learner's half-typed answers) while a poll arrives.
(function (root) {
  "use strict";
  var K = typeof module !== "undefined" && module.exports ? require("./chat_core.js") : root.ChatCore;
  var KINDS = ["predict", "bug", "write", "explain"];

  function safeLink(url) { return typeof url === "string" && /^https:\/\//.test(url) ? url : null; }

  function currentKind(st) {
    for (var i = 0; i < 3; i++) if (!st.passed[KINDS[i]]) return KINDS[i];
    return st.passed.explain ? null : "explain";
  }

  function stageBlock(chat, st) {
    var earned = K.isEarned(st), diff = earned && st.code ? K.unlockDiff(chat, st.n) : null;
    return { key: "stage" + st.n, type: "stage", n: st.n, title: st.title, summary: st.summary, files: st.files, concepts: st.concepts,
             locked: !earned, needCode: earned && !st.code, diff: diff, sig: [earned, !!st.code, st.title, st.summary, st.files.join(), (diff || []).length].join("|") };
  }

  function checkpointBlock(chat, st, ui) {
    var results = {};
    KINDS.forEach(function (k) { var r = (ui.results || {})[st.n + "/" + k]; if (r) results[k] = r; });
    var canWork = K.canWork(chat, st.n), cur = currentKind(st);
    return { key: "cp" + st.n, type: "checkpoint", n: st.n, view: st.view, bugHint: st.bugHint, passed: Object.assign({}, st.passed), canWork: canWork,
             current: cur, results: results, easy: chat.difficulty <= 1,
             sig: JSON.stringify([st.passed, canWork, cur, results, chat.difficulty <= 1, (ui.busy || {})[st.n]]) };
  }

  function blocks(chat, ui) {
    ui = ui || {};
    var out = [], pendingLog = null;
    function flush() { if (pendingLog) { out.push(pendingLog); pendingLog = null; } }
    chat.thread.forEach(function (t) {
      if (t.kind === "log") {
        if (!pendingLog) pendingLog = { key: "log" + t.id, type: "log", lines: [], sig: "" };
        pendingLog.lines.push(t.text); pendingLog.sig = String(pendingLog.lines.length);
        return;
      }
      flush();
      var st = t.n ? K.stageOf(chat, t.n) : null;
      if (t.kind === "user") out.push({ key: "u" + t.id, type: "user", text: t.text, sig: "1" });
      else if (t.kind === "plan") {
        out.push({ key: "plan", type: "plan", stages: chat.plan.map(function (p) { return { n: p.n, title: p.title, summary: p.summary, state: K.stageBuild(chat, p.n),
          earned: K.isEarned(K.stageOf(chat, p.n)) }; }),
          sig: chat.plan.map(function (p) { return K.stageBuild(chat, p.n) + K.isEarned(K.stageOf(chat, p.n)); }).join() });
      } else if (t.kind === "stage" && st) out.push(stageBlock(chat, st));
      else if (t.kind === "checkpoint" && st) out.push(checkpointBlock(chat, st, ui));
      else if (t.kind === "unlock" && st) out.push({ key: "unlock" + t.n, type: "unlock", n: t.n, text: "Stage " + t.n + " earned. Its code is unlocked above.", sig: "1" });
      else if (t.kind === "pr" && chat.prs[t.url]) out.push({ key: "pr" + t.url, type: "pr", pr: chat.prs[t.url], sig: JSON.stringify(chat.prs[t.url]) });
      else if (t.kind === "bridge" || t.kind === "notice") out.push({ key: "n" + t.id, type: t.kind, cls: t.cls || "", text: t.text, sig: "1" });
    });
    flush();
    return out;
  }

  function badge(chat) {
    if (chat.source === "live") return { cls: "live", text: "LIVE DEVIN", why: "A real Devin session, driven through the server bridge." };
    var why = chat.replayWhy === "demo" ? K.REASON_TEXT.demo : "Live Devin was not used: " + K.REASON_TEXT[chat.replayWhy];
    return { cls: "replay", text: "OFFLINE REPLAY · SIMULATED", why: why + ". Every log line, pull request and ACU number here is simulated." };
  }

  function acu(chat) {
    var s = chat.session;
    if (!s || typeof s.acuLimit !== "number") return null;
    var used = typeof s.acu === "number" ? s.acu : 0;
    return { text: (s.simulated ? "Simulated ACU " : "ACU ") + used + " of " + s.acuLimit, pct: Math.min(100, Math.round(100 * used / s.acuLimit)), simulated: !!s.simulated };
  }

  function ciLabel(pr) {
    if (pr.ci === "passing") return { cls: "good", text: "CI passing" };
    if (pr.ci === "failing") return { cls: "bad", text: "CI failing" };
    if (pr.ci === "pending") return { cls: "warn", text: "CI running" };
    if (pr.ci === "simulated") return { cls: "sim", text: "CI simulated" };
    return { cls: "", text: "no CI reported" };
  }

  function reviewLabel(pr) {
    if (pr.simulated) return "Devin Review: simulated, none";
    var r = pr.review || {};
    if (r.comments > 0) return "Devin Review: " + r.comments + " comment" + (r.comments === 1 ? "" : "s");
    return r.state && r.state !== "none" ? "Devin Review: " + r.state : "Devin Review: no review yet";
  }

  // Never repeat the expected answer or the right option back at the learner: that would let them copy it and skip the earning.
  function failureText(r, kind) {
    if (r.cases && typeof r.cases === "object") {
      var wrong = Object.keys(r.cases).filter(function (k) { return r.cases[k] && r.cases[k].passed === false; });
      if (wrong.length) return "case" + (wrong.length === 1 ? " " : "s ") + wrong.join(", ") + (wrong.length === 1 ? " is" : " are") + " not right.";
    }
    if (kind === "bug") return "that is not the bug.";
    if (r.error) return r.error;
    var c = r.counterexample;
    if (c) return "on " + (c.ops || []).length + " operations the real code gives " + JSON.stringify(c.expected) + " and yours gives " + JSON.stringify(c.got) + ".";
    return r.failure || r.message || r.detail || r.hint || "the grader found a difference.";
  }

  function outcome(kind, res, cp) {
    var r = res.result || {}, points = [];
    (r.covered || []).forEach(function (p) { points.push("Covered: " + (p.point || p)); });
    (r.missed || []).forEach(function (p) { points.push("Missed: " + (p.point || p)); });
    if (res.passed === true) return { cls: "good", text: kind === "explain" ? "Good. Compare your words with the points below." : "Passed. " + (r.explanation || ""), points: points };
    var hint = (cp && cp.hint) ? " Hint: " + cp.hint : "";
    return { cls: "bad", text: "Not yet: " + failureText(r, kind) + hint + " Try again; Devin stays stopped until you earn it.", points: points };
  }

  function sidebarRow(chat) {
    var earned = chat.stages.filter(function (s) { return K.isEarned(s); }).length;
    return { id: chat.id, title: chat.title, badge: chat.source === "live" ? "LIVE" : "REPLAY",
             progress: chat.plan.length ? earned + " of " + chat.plan.length + " earned" : "starting" };
  }

  var api = { KINDS: KINDS, blocks: blocks, badge: badge, acu: acu, ciLabel: ciLabel, reviewLabel: reviewLabel, outcome: outcome,
              safeLink: safeLink, sidebarRow: sidebarRow, currentKind: currentKind };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.ChatView = api;
})(typeof self !== "undefined" ? self : this);
