// Chat core: the stop-until-earned state machine as pure functions (node tests it, no DOM, no network, no clock).
//
// The rule it enforces, for a REAL Devin session and for the labelled replay alike:
//   * the code of a stage is never kept until the learner earned the stage (predict, find the bug and write all passed);
//   * Devin may be at most LOOKAHEAD stages past the last stage the learner earned. That is the pipelining: while Devin
//     builds stage N+1 the learner works the checkpoint of stage N, and stage N+2 is not started until N is earned;
//   * the bridge sends Devin "continue" only from nextEffects(), only when Devin is waiting_for_user and the rule above allows it.
//
// reduce(chat, event) returns { chat, effects }. It never mutates its input. Effects are plain data for the controller:
//   { type: "send", reason: "continue" | "adapt" | "user", text, n }   message the running Devin session
//   { type: "stop" }                                                    suspend the session
//   { type: "start_replay" }                                            begin the labelled offline replay
(function (root) {
  "use strict";

  var REQUIRED = ["predict", "bug", "write"];
  var LOOKAHEAD = 1;
  var MAX_CHATS = 30, MAX_THREAD = 300, MAX_SEEN = 600;
  var LIMIT_DETAILS = ["usage_limit_exceeded", "out_of_credits", "out_of_quota", "no_quota_allocation", "payment_declined", "org_usage_limit_exceeded"];

  var REASON_TEXT = {
    quota: "the Devin usage or ACU limit was reached",
    no_token: "this server has no Devin token configured",
    no_bridge: "this server has no Devin bridge (/api/devin)",
    api_error: "the Devin API returned an error",
    network: "the Devin bridge could not be reached",
    offline_toggle: "you turned on offline replay",
    demo: "you chose the built-in demo course",
  };

  var STRESSED_TEXT = "The learner is stressed, make the next stage smaller and add comments.";
  var RELAXED_TEXT = "The learner has recovered, the next stage can go back to normal size.";

  function clone(x) { return JSON.parse(JSON.stringify(x)); }

  function devinPhase(status, detail) {
    if (LIMIT_DETAILS.indexOf(detail) >= 0) return "limit";
    if (status === "suspended") return "suspended";
    if (status === "error" || detail === "error") return "error";
    if (status === "exit" || detail === "finished") return "finished";
    if (detail === "waiting_for_user" || detail === "waiting_for_approval") return "waiting";
    if (status === "running") return "working";
    return "starting";
  }

  function titleFrom(prompt) {
    var t = String(prompt || "").replace(/\s+/g, " ").trim();
    if (!t) return "New chat";
    return t.length > 48 ? t.slice(0, 45) + "..." : t;
  }

  function createChat(o) {
    var now = o.now || 0;
    return { v: 1, id: o.id, title: titleFrom(o.prompt), prompt: o.prompt, createdAt: now, updatedAt: now,
             source: o.source || "live", course: o.course || null, replayWhy: o.source === "replay" ? (o.replayWhy || "demo") : null,
             difficulty: o.difficulty || 3, session: null, plan: [], stages: [], thread: [{ id: 0, kind: "user", text: o.prompt, ts: now }],
             seq: 1, seen: {}, prs: {}, continued: [], stopped: false };
  }

  function session(chat) { return chat.session || null; }
  function phase(chat) { return chat.session ? chat.session.phase : null; }
  function stageOf(chat, n) { return chat.stages.filter(function (s) { return s.n === n; })[0]; }

  function isEarned(st) { return !!st && REQUIRED.every(function (k) { return st.passed && st.passed[k] === true; }); }
  function builtCount(chat) { return chat.stages.filter(function (s) { return s.built; }).length; }
  function earnedCount(chat) {
    var n = 0;
    for (var i = 1; i <= chat.stages.length; i++) { if (isEarned(stageOf(chat, i))) n = i; else break; }
    return n;
  }

  function stageBuild(chat, n) {
    var st = stageOf(chat, n);
    if (st && st.built) return "built";
    var p = phase(chat);
    return (p === "working" || p === "starting") && n === builtCount(chat) + 1 ? "building" : "queued";
  }

  // May Devin build stage n? Only up to `look` stages past the last stage the learner earned.
  function devinMayBuild(chat, n, look) {
    return n <= earnedCount(chat) + 1 + (look === undefined ? LOOKAHEAD : look);
  }

  // The learner may work stage n only when Devin built it and every earlier stage is earned.
  function canWork(chat, n) {
    var st = stageOf(chat, n);
    return !!st && st.built && earnedCount(chat) >= n - 1;
  }

  function continueText(chat, n) {
    var total = chat.plan.length, item = chat.plan[n - 1] || {};
    var text = "Continue with stage " + n + " of " + total + (item.title ? ": " + item.title : "") +
      ". Keep it small, then stop again and wait for my next message. Do not start stage " + (n + 1) + " yet.";
    return chat.difficulty <= 2 ? text + " The learner is stressed: make this stage smaller and add comments." : text;
  }

  function nextEffects(chat) {
    if (chat.stopped || phase(chat) !== "waiting" || !chat.plan.length) return [];
    var built = builtCount(chat), n = built + 1;
    if (built === 0 || n > chat.plan.length || !devinMayBuild(chat, n) || chat.continued.indexOf(n) >= 0) return [];
    return [{ type: "send", reason: "continue", n: n, text: continueText(chat, n) }];
  }

  // Who is waiting for whom: the one honest status line of the thread.
  function gateState(chat) {
    var p = phase(chat), total = chat.plan.length, built = builtCount(chat), earned = earnedCount(chat);
    if (chat.stopped) return { kind: "stopped", text: "Stopped. Devin was told to stop; nothing more will be built." };
    if (!chat.session) return { kind: "idle", text: "No build running." };
    if (p === "limit") return { kind: "limit", text: "Devin hit its usage limit. Nothing more will be built." };
    if (p === "error") return { kind: "error", text: "The Devin session reported an error." };
    if (p === "suspended") return { kind: "suspended", text: "The Devin session is suspended." };
    if (total && earned === total) return { kind: "complete", text: "Every stage earned. The whole app is unlocked." };
    if (p === "finished") return { kind: "finished", text: "Devin is finished" + (earned < built ? "; earn the remaining stages to unlock the code." : ".") };
    if (p === "working" || p === "starting") {
      if (earned >= built) return { kind: "waiting_for_devin", text: "Waiting for Devin: stage " + (built + 1) + " is being built." };
      return { kind: "pipelining", text: "Devin is building stage " + (built + 1) + " while you earn stage " + (earned + 1) + "." };
    }
    if (earned < built) return { kind: "waiting_for_learner", text: "Devin has stopped. Earn stage " + (earned + 1) + " to let it go on." };
    return { kind: "continuing", text: "Stage earned. Asking Devin to continue." };
  }

  // ---- line diff (LCS) for the unlock panel ----
  function lines(s) { return s === "" || s === null || s === undefined ? [] : String(s).split("\n"); }
  function diffLines(before, after) {
    var a = lines(before), b = lines(after), out = [], i = 0, j = 0;
    if (a.length * b.length > 160000) return b.map(function (t) { return { t: "add", text: t }; });
    var dp = [], x, y;
    for (x = 0; x <= a.length; x++) dp.push(new Array(b.length + 1).fill(0));
    for (x = a.length - 1; x >= 0; x--) for (y = b.length - 1; y >= 0; y--) {
      dp[x][y] = a[x] === b[y] ? dp[x + 1][y + 1] + 1 : Math.max(dp[x + 1][y], dp[x][y + 1]);
    }
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { out.push({ t: "same", text: a[i] }); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "del", text: a[i] }); i++; }
      else { out.push({ t: "add", text: b[j] }); j++; }
    }
    for (; i < a.length; i++) out.push({ t: "del", text: a[i] });
    for (; j < b.length; j++) out.push({ t: "add", text: b[j] });
    return out;
  }

  function unlockDiff(chat, n) {
    var st = stageOf(chat, n), prev = n > 1 ? stageOf(chat, n - 1) : null;
    return diffLines(prev && isEarned(prev) && prev.code ? prev.code : "", st && st.code ? st.code : "");
  }

  // ---- naming the failure instead of hiding it ----
  function fallbackReason(err) {
    var status = err && err.status, code = err && err.code, reason;
    if (code === "quota" || status === 402 || status === 429) reason = "quota";
    else if (code === "no_token") reason = "no_token";
    else if (status === 404) reason = "no_bridge";
    else if (status >= 400) reason = "api_error";
    else reason = "network";
    return { reason: reason, text: REASON_TEXT[reason] };
  }

  function learnerId(chat) { return ("chat-" + String(chat.id).replace(/[^A-Za-z0-9_-]/g, "")).slice(0, 64); }

  function gradeUrl(chat, stage, kind) {
    if (chat.course === "builtin") return "/api/learn/" + encodeURIComponent(stage.id) + "/" + kind;
    return "/api/devin/sessions/" + encodeURIComponent(chat.session ? chat.session.id : "none") + "/stages/" + stage.n + "/" + kind;
  }

  // ---- persistence ----
  function serializeChats(chats) {
    var out = chats.slice(0, MAX_CHATS).map(function (c) {
      var copy = clone(c);
      copy.thread = copy.thread.slice(-MAX_THREAD);
      copy.stages.forEach(function (s) { if (!isEarned(s)) s.code = null; });
      return copy;
    });
    return JSON.stringify({ v: 1, chats: out });
  }

  function parseChats(text) {
    var data;
    try { data = JSON.parse(text); } catch (e) { return []; }
    var list = data && Array.isArray(data.chats) ? data.chats : [];
    return list.filter(function (c) {
      return c && typeof c === "object" && typeof c.id === "string" && Array.isArray(c.thread) && Array.isArray(c.stages) && Array.isArray(c.plan);
    });
  }

  // ---- reducer ----
  function push(c, item, now) { item.id = c.seq++; item.ts = now; c.thread.push(item); return item; }

  function newStage(p) {
    return { n: p.n, id: p.id || ("s" + p.n), title: p.title || ("Stage " + p.n), summary: p.summary || "", built: false,
             passed: { predict: false, bug: false, write: false, explain: false }, code: null, view: null, files: [], concepts: [],
             bugHint: "", announced: false };
  }

  function ensureStage(c, n) {
    var st = stageOf(c, n);
    if (!st) { st = newStage({ n: n }); c.stages.push(st); c.stages.sort(function (a, b) { return a.n - b.n; }); }
    return st;
  }

  function announceEarned(c, st, now) {
    if (isEarned(st) && !st.announced) { st.announced = true; push(c, { kind: "unlock", n: st.n }, now); }
  }

  function mergeProgress(st, progress) {
    Object.keys(progress || {}).forEach(function (k) { if (progress[k] === true) st.passed[k] = true; });
  }

  function ingestStage(c, v, now) {
    var n = Number(v.n), st = ensureStage(c, n);
    ["title", "summary"].forEach(function (k) { if (v[k]) st[k] = v[k]; });
    if (v.id) st.id = v.id;
    if (Array.isArray(v.files)) st.files = v.files.slice(0, 12);
    if (Array.isArray(v.concepts)) st.concepts = v.concepts.slice(0, 12);
    if (v.bug_hint) st.bugHint = String(v.bug_hint);
    var view = clone(v);
    delete view.code;
    st.view = view;
    if (v.unlocked === true) REQUIRED.forEach(function (k) { st.passed[k] = true; });
    mergeProgress(st, v.progress);
    if (v.status === "built" && !st.built) {
      st.built = true;
      push(c, { kind: "stage", n: n }, now);
      push(c, { kind: "checkpoint", n: n }, now);
    }
    if (isEarned(st) && typeof v.code === "string") st.code = v.code;
    announceEarned(c, st, now);
  }

  function ingestSnapshot(c, snap, now) {
    var s = Object.assign({}, c.session || {});
    s.status = snap.status; s.detail = snap.status_detail; s.phase = devinPhase(snap.status, snap.status_detail);
    if (typeof snap.acu_consumed === "number") s.acu = snap.acu_consumed;
    if (typeof snap.acu_limit === "number") s.acuLimit = snap.acu_limit;
    if (snap.url) s.url = snap.url;
    if (snap.simulated !== undefined) s.simulated = !!snap.simulated;
    c.session = s;
    if (Array.isArray(snap.plan) && snap.plan.length && !c.plan.length) {
      c.plan = snap.plan.map(function (p) { return { n: Number(p.n), title: p.title || "", summary: p.summary || "" }; });
      c.stages = c.plan.map(newStage);
      c.plan.forEach(function (p, i) { c.stages[i].id = (snap.plan[i] && snap.plan[i].id) || c.stages[i].id; });
      push(c, { kind: "plan" }, now);
    }
    (snap.messages || []).forEach(function (m) {
      if (m.id === undefined || c.seen[m.id]) return;
      c.seen[m.id] = true;
      push(c, { kind: "log", role: m.source === "user" ? "user" : "devin", text: String(m.text || "") }, now);
    });
    var ids = Object.keys(c.seen);
    if (ids.length > MAX_SEEN) ids.slice(0, ids.length - MAX_SEEN).forEach(function (k) { delete c.seen[k]; });
    (snap.stages || []).forEach(function (v) { ingestStage(c, v, now); });
    (snap.pull_requests || []).forEach(function (p) {
      if (!p.url) return;
      var known = !!c.prs[p.url];
      c.prs[p.url] = { url: p.url, title: p.title || "", state: p.state || "open", ci: p.ci || "none", stage: p.stage || null,
                       review: p.review || { state: "none", comments: 0 }, simulated: !!p.simulated };
      if (!known) push(c, { kind: "pr", url: p.url }, now);
    });
  }

  function reduce(chat, ev) {
    var c = clone(chat), effects = [], now = ev.now || 0, st;
    c.updatedAt = now || c.updatedAt;
    switch (ev.type) {
      case "session":
        c.session = { id: ev.id, url: ev.url || null, live: ev.live !== false, status: "new", detail: null, phase: "starting",
                      acu: 0, acuLimit: ev.acuLimit || null, simulated: ev.live === false };
        break;
      case "snapshot":
        ingestSnapshot(c, ev.snap, now);
        break;
      case "checkpoint":
        st = stageOf(c, ev.n);
        if (!st) break;
        if (ev.passed) st.passed[ev.kind] = true;
        mergeProgress(st, ev.progress);
        if (isEarned(st) && typeof ev.code === "string") st.code = ev.code;
        announceEarned(c, st, now);
        break;
      case "continue_sent":
        if (c.continued.indexOf(ev.n) < 0) c.continued.push(ev.n);
        push(c, { kind: "bridge", text: ev.delayMs > 0 ? "Slower pace (difficulty 1): Devin will be told to continue with stage " + ev.n + " in " + Math.round(ev.delayMs / 1000) + " s."
                                                      : "Bridge told Devin to continue with stage " + ev.n + "." }, now);
        break;
      case "continue_failed":
        c.continued = c.continued.filter(function (n) { return n !== ev.n; });
        break;
      case "adapt":
        if (ev.level === c.difficulty) break;
        var lower = ev.level < c.difficulty, prevLevel = c.difficulty;
        c.difficulty = ev.level;
        // The reason (for example "HR 112 vs baseline 72: switching to easier steps") is shown HERE, on this device. What goes to Devin
        // is the plain request below: no heart rate ever leaves the page.
        push(c, { kind: "bridge", text: (ev.reason ? ev.reason + ". " : "Stress engine: ") + "Difficulty " + (lower ? "lowered" : "restored") + " to " + ev.level + " (was " + prevLevel + ")." +
          (lower ? " Telling Devin to make the next stage smaller and add comments." : " Telling Devin the next stage can be normal size.") }, now);
        if (c.session && !c.stopped && ["working", "waiting", "starting"].indexOf(c.session.phase) >= 0) {
          effects.push({ type: "send", reason: "adapt", level: ev.level,
                         text: lower ? STRESSED_TEXT + (ev.level <= 1 ? " Go slowly and keep it as short as you can." : "") : RELAXED_TEXT });
        }
        break;
      case "user":
        push(c, { kind: "user", text: ev.text }, now);
        if (c.session && !c.stopped && c.source === "live") {
          effects.push({ type: "send", reason: "user", text: ev.text + "\n\n(Do not start the next stage until the bridge sends 'continue'.)" });
        }
        break;
      case "notice":
        push(c, { kind: "notice", cls: ev.cls || "warn", text: ev.text }, now);
        break;
      case "stage_view":
        ingestStage(c, ev.view, now);
        break;
      case "stop":
        c.stopped = true;
        push(c, { kind: "notice", cls: "warn", text: "You stopped the build." }, now);
        effects.push({ type: "stop" });
        break;
      case "fallback":
        c.source = "replay"; c.course = "builtin"; c.replayWhy = ev.reason; c.session = null;
        c.plan = []; c.stages = []; c.continued = []; c.seen = {}; c.prs = {}; c.stopped = false;
        push(c, { kind: "notice", cls: "warn", text: "Live Devin is not available: " + (ev.text || REASON_TEXT[ev.reason] || ev.reason) +
          ". Switched to a labelled OFFLINE REPLAY: a simulated build of the built-in course, not your prompt and not a live Devin session." }, now);
        effects.push({ type: "start_replay" });
        break;
      default:
        break;
    }
    return { chat: c, effects: effects.concat(ev.type === "snapshot" || ev.type === "checkpoint" ? nextEffects(c) : []) };
  }

  var api = { REQUIRED: REQUIRED, LOOKAHEAD: LOOKAHEAD, REASON_TEXT: REASON_TEXT, STRESSED_TEXT: STRESSED_TEXT, devinPhase: devinPhase,
              createChat: createChat, titleFrom: titleFrom, session: session, stageOf: stageOf, isEarned: isEarned, builtCount: builtCount,
              earnedCount: earnedCount, stageBuild: stageBuild, devinMayBuild: devinMayBuild, canWork: canWork, nextEffects: nextEffects,
              gateState: gateState, diffLines: diffLines, unlockDiff: unlockDiff, fallbackReason: fallbackReason, learnerId: learnerId,
              gradeUrl: gradeUrl, serializeChats: serializeChats, parseChats: parseChats, reduce: reduce, clone: clone };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.ChatCore = api;
})(typeof self !== "undefined" ? self : this);
