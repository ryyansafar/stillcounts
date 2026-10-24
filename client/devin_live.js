// window.DevinLive: everything the chat page needs to treat Devin as a LIVE, steerable agent, as plain functions that node can test.
//   create({fetch})            the server bridge client (start, poll, message, stop, stage): the browser never holds a Devin token
//   statusView(chat)           one honest status model for the live bar: phase, raw status_detail, ACU of cap, session link,
//                              pull requests with CI and review counts, Devin's last words, and who is waiting for whom
//   levelProfile(level)        what each difficulty level changes in the checkpoint cards and in Devin's pace (never auto-passes)
//   stepsFromStub / guidedSkeleton / assembleChoice   how the easier Write variants are made from the task's own text
//   adaptPayload(ev, chat)     the adaptation event sent to the results dashboard: level change and a reason with NO numbers
// Heart rate itself never leaves the page. See client/hr.js and client/hr_widget.js.
(function (root) {
  "use strict";
  var isNode = typeof module !== "undefined" && module.exports;
  var K = isNode ? require("./chat_core.js") : root.ChatCore;
  var B = isNode ? require("./chat_bridge.js") : root.ChatBridge;

  var PHASE_TEXT = {
    starting: "Devin is starting", working: "Devin is working", waiting: "Devin has stopped and is waiting for you",
    finished: "Devin is finished", suspended: "The Devin session is suspended", limit: "Devin hit its usage limit", error: "The Devin session errored",
  };
  var PHASE_CLS = { starting: "warn", working: "live", waiting: "wait", finished: "good", suspended: "warn", limit: "bad", error: "bad" };
  var SLOW_PACE_MS = 15000;

  // ---- difficulty levels: 3 normal, 2 hint + smaller steps, 1 guided answers + slower Devin + breathing ----
  function levelProfile(level) {
    var l = level === 1 || level === 2 ? level : 3;
    return { level: l, hint: l < 3, steps: l < 3, oneCaseAtATime: l < 3, writeMode: l === 1 ? "guided" : "normal", starters: l === 1,
             breathing: l === 1, paceMs: l === 1 ? SLOW_PACE_MS : 0, autoPass: false,
             label: l === 3 ? "normal" : l === 2 ? "easier steps" : "guided and slower" };
  }

  // The docstring of a stub is the task. Split it into the small steps it already contains; no new answers are invented.
  function stepsFromStub(stub) {
    var m = /"""([\s\S]*?)"""/.exec(String(stub || "")), text = m ? m[1] : "", out = [];
    text.split(/\n\s*\n/).forEach(function (para) {
      para.replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+/).forEach(function (s) { if (s.trim().length > 3) out.push(s.trim()); });
    });
    return out.slice(0, 6);
  }

  // Guided fill-in: the stub with the placeholder body swapped for one commented line per step. The learner still writes the code.
  function guidedSkeleton(stub, steps) {
    var body = steps.map(function (s, i) { return "    # step " + (i + 1) + ": " + s.slice(0, 90); }).join("\n");
    var s = String(stub || "");
    return /\n\s*raise NotImplementedError\s*\n?$/.test(s) ? s.replace(/\n\s*raise NotImplementedError\s*\n?$/, "\n" + body + "\n    ...\n") : s;
  }

  // Multiple choice for Write: the chosen body replaces the placeholder, and the result goes to the SAME grader as typed code.
  function assembleChoice(stub, body) {
    var s = String(stub || "");
    return s.replace(/\n\s*raise NotImplementedError\s*\n?$/, "\n" + String(body || ""));
  }

  var STARTERS = ["The code ...", "It works because ...", "Without it, ...", "The tricky case is ..."];

  // ---- adaptation events for the results dashboard: no heart rate, no numbers ----
  function sanitizeReason(reason) {
    var r = String(reason || "");
    if (/\bHR\b|heart/i.test(r)) return "heart rate up vs baseline: switching to easier steps";
    if (/calm again/i.test(r)) return "calm again: restoring difficulty";
    if (/miss/i.test(r)) return "repeated misses: adding a hint and smaller steps";
    return r.replace(/\d+(\.\d+)?/g, "").replace(/\s+/g, " ").trim().slice(0, 160);
  }
  function eventKind(reason, level, prev) {
    var r = String(reason || "");
    if (/calm again/i.test(r) || level > prev) return "calm";
    if (/\bHR\b|heart/i.test(r)) return "stress";
    if (/miss/i.test(r)) return "misses";
    return "manual";
  }
  function adaptPayload(ev, chat) {
    var prev = ev.prevLevel !== undefined ? ev.prevLevel : ev.previous;
    var open = chat && chat.stages ? chat.stages.filter(function (s) { return s.built && !K.isEarned(s); })[0] : null;
    return { chat: K.learnerId(chat || { id: "none" }), level: ev.level, prev: prev, kind: eventKind(ev.reason, ev.level, prev),
             stage: open ? open.n : null, reason: sanitizeReason(ev.reason) };
  }

  // ---- the live status model ----
  function lastWords(chat) {
    for (var i = chat.thread.length - 1; i >= 0; i--) if (chat.thread[i].kind === "log" && chat.thread[i].role === "devin") return chat.thread[i].text;
    return "";
  }
  function statusView(chat) {
    if (!chat || !chat.session) return null;
    var s = chat.session, live = chat.source === "live", phase = s.phase || "starting", built = K.builtCount(chat);
    var prs = Object.keys(chat.prs).map(function (u) { return chat.prs[u]; });
    var text = PHASE_TEXT[phase] || "Devin";
    if (phase === "working" || phase === "starting") text += chat.plan.length ? ": stage " + Math.min(built + 1, chat.plan.length) + " of " + chat.plan.length : "";
    if (chat.stopped) text = "Stopped by you";
    var last = lastWords(chat);
    return {
      live: live, cls: chat.stopped ? "warn" : PHASE_CLS[phase] || "", phase: phase, text: (live ? "" : "Simulated. ") + text,
      detail: s.detail || s.status || "", gate: K.gateState(chat), last: last.length > 140 ? last.slice(0, 137) + "..." : last,
      link: typeof s.url === "string" && /^https:\/\//.test(s.url) ? s.url : null,
      acu: typeof s.acuLimit === "number" ? { used: typeof s.acu === "number" ? s.acu : 0, cap: s.acuLimit } : null,
      prs: { total: prs.length, passing: prs.filter(function (p) { return p.ci === "passing"; }).length, failing: prs.filter(function (p) { return p.ci === "failing"; }).length,
             pending: prs.filter(function (p) { return p.ci === "pending"; }).length, reviewComments: prs.reduce(function (n, p) { return n + ((p.review && p.review.comments) || 0); }, 0) },
      level: chat.difficulty, slow: chat.difficulty <= 1,
    };
  }

  function create(o) { return B.createLiveBridge(o); }

  var api = { create: create, statusView: statusView, levelProfile: levelProfile, stepsFromStub: stepsFromStub, guidedSkeleton: guidedSkeleton,
              assembleChoice: assembleChoice, STARTERS: STARTERS, adaptPayload: adaptPayload, sanitizeReason: sanitizeReason, SLOW_PACE_MS: SLOW_PACE_MS };
  if (isNode) { module.exports = api; return; }
  root.DevinLive = api;
})(typeof self !== "undefined" ? self : this);
