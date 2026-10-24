// Chat controller: wires the pure state machine (chat_core.js) to the Devin bridge, the labelled replay, the checkpoint
// graders, localStorage and the stress engine. Everything with a side effect is injected, so node can test it with fakes.
(function (root) {
  "use strict";
  var K = typeof module !== "undefined" && module.exports ? require("./chat_core.js") : root.ChatCore;
  var B = typeof module !== "undefined" && module.exports ? require("./chat_bridge.js") : root.ChatBridge;
  var R = typeof module !== "undefined" && module.exports ? require("./chat_replay.js") : root.ChatReplay;
  var P = typeof module !== "undefined" && module.exports ? require("./devin_live.js") : root.DevinLive;
  var CHATS_KEY = "sc_chats", PREFS_KEY = "sc_chat_prefs";
  var TERMINAL = ["finished", "suspended", "limit", "error"];
  var FAIL_LIMIT = 3;

  // window.EarnHooks is the small seam the stress engine (client/hr.js) plugs into:
  //   getDifficulty()  -> 1 (easiest) .. 3 (normal), default 3
  //   onAdapt(cb)      -> cb({ level, previous }) whenever the engine changes the difficulty
  //   setDifficulty(level, info) -> the engine reports a new level (this notifies every onAdapt listener)
  // installEarnHooks makes a working object when none exists; lateHooks always talks to whatever window.EarnHooks is at
  // the time, so a real engine that loads after this page still takes over.
  function installEarnHooks(host) {
    if (host.EarnHooks) return host.EarnHooks;
    var level = 3, cbs = [];
    host.EarnHooks = {
      getDifficulty: function () { return level; },
      onAdapt: function (cb) { cbs.push(cb); },
      setDifficulty: function (l, info) { var p = level; level = l; cbs.slice().forEach(function (cb) { cb(Object.assign({}, info, { level: l, previous: p })); }); },
    };
    return host.EarnHooks;
  }

  function lateHooks(host) {
    var cbs = [], bound = null;
    function bind() {
      var r = host && host.EarnHooks;
      if (!r || r === bound) return;
      bound = r;
      if (typeof r.onAdapt === "function") r.onAdapt(function (ev) { cbs.slice().forEach(function (cb) { cb(ev); }); });
    }
    if (host && host.addEventListener) host.addEventListener("load", bind);
    bind();
    return {
      bind: bind,
      getDifficulty: function () {
        bind();
        var r = host && host.EarnHooks, d = r && typeof r.getDifficulty === "function" ? Number(r.getDifficulty()) : 3;
        return d >= 1 && d <= 3 ? d : 3;
      },
      onAdapt: function (cb) { cbs.push(cb); bind(); },
      // the shell tells the engine how each checkpoint went, so repeated misses can lower the difficulty as well as a racing heart
      reportAttempt: function (passed) { bind(); var r = host && host.EarnHooks; if (r && typeof r.reportAttempt === "function") r.reportAttempt(passed === true); },
    };
  }

  function createController(o) {
    var storage = o.storage, now = o.now || Date.now, schedule = o.schedule, cancel = o.cancel, onChange = o.onChange || function () {};
    var hooks = o.hooks, live = o.liveBridge, newId = o.newId;
    var liveMs = o.liveMs || 3000, replayMs = o.replayMs || 700;
    var loadCourse = o.loadCourse || function (x) { return B.loadCourse({ fetch: o.fetch, storage: storage, learner: x.learner }); };
    var replayFactory = o.replayFactory || function (views, state) { return R.createReplay({ views: views, now: now, state: state }); };

    var chats = [], activeId = null, offline = false, replayStates = {};
    var timers = {}, engines = {}, inflight = {}, fails = {};

    try { chats = K.parseChats(storage.getItem(CHATS_KEY)); } catch (e) { chats = []; }
    try {
      var prefs = JSON.parse(storage.getItem(PREFS_KEY)) || {};
      offline = prefs.offline === true; activeId = prefs.active || null; replayStates = prefs.replay || {};
    } catch (e) { /* first run */ }
    if (!chats.some(function (c) { return c.id === activeId; })) activeId = null;

    function persist() {
      try {
        storage.setItem(CHATS_KEY, K.serializeChats(chats));
        storage.setItem(PREFS_KEY, JSON.stringify({ offline: offline, active: activeId, replay: replayStates }));
      } catch (e) { /* storage full or blocked: keep going in memory */ }
    }
    function get(id) { return chats.filter(function (c) { return c.id === id; })[0] || null; }
    function active() { return activeId ? get(activeId) : null; }
    function changed() { persist(); onChange(); }

    function dispatch(id, ev) {
      var chat = get(id);
      if (!chat) return Promise.resolve();
      var r = K.reduce(chat, Object.assign({ now: now() }, ev));
      chats = chats.map(function (c) { return c.id === id ? r.chat : c; });
      changed();
      return Promise.all(r.effects.map(function (eff) { return run(id, eff); }));
    }

    function notice(id, text) { return dispatch(id, { type: "notice", cls: "warn", text: text }); }

    // ---- effects ----
    function run(id, eff) {
      var chat = get(id);
      if (!chat) return Promise.resolve();
      if (eff.type === "start_replay") return startReplay(id);
      var isLive = chat.source === "live" && chat.session && chat.session.live;
      if (eff.type === "stop") {
        cancelTimer(id);
        if (!isLive) { return engineFor(chat).then(function (e) { e.stop(); saveReplay(id, e); }, function () {}); }
        return Promise.resolve().then(function () { return live.stop(chat.session.id); }).catch(function (err) {
          return notice(id, "Could not reach Devin to stop the session (" + err.message + "). Stop it from the session link.");
        });
      }
      if (eff.type !== "send") return Promise.resolve();
      var delay = eff.reason === "continue" ? P.levelProfile(chat.difficulty).paceMs : 0;
      var head = eff.reason === "continue" ? dispatch(id, { type: "continue_sent", n: eff.n, delayMs: delay }) : Promise.resolve();
      var meta = { reason: eff.reason, n: eff.n, slow: delay > 0 };
      function deliver() {
        var cur = get(id);
        if (!cur || cur.stopped) return Promise.resolve();
        var sending = isLive ? Promise.resolve().then(function () { return live.message(cur.session.id, eff.text, meta); })
                             : engineFor(cur).then(function (e) { e.message(eff.text, meta); saveReplay(id, e); });
        return sending.catch(function (err) {
          if (eff.reason === "continue") return dispatch(id, { type: "continue_failed", n: eff.n });
          return notice(id, "Devin did not receive the message (" + (err && err.message) + ").");
        });
      }
      // Level 1 slows Devin down: the continue is held back, so the next stage is not built while the learner is under stress.
      return head.then(function () {
        if (delay <= 0) return deliver();
        timers[id + ":pace"] = schedule(function () { delete timers[id + ":pace"]; deliver(); }, delay);
      });
    }

    // ---- replay ----
    function saveReplay(id, engine) { replayStates[id] = engine.state(); persist(); }
    function engineFor(chat) {
      if (engines[chat.id]) return Promise.resolve(engines[chat.id]);
      return loadCourse({ learner: K.learnerId(chat) }).then(function (views) {
        engines[chat.id] = replayFactory(views, replayStates[chat.id] || null);
        return engines[chat.id];
      });
    }
    function startReplay(id) {
      var chat = get(id);
      return loadCourse({ learner: K.learnerId(chat) }).then(function (views) {
        var engine = replayFactory(views, null), sess = engine.start(chat.prompt);
        engines[id] = engine;
        return dispatch(id, { type: "session", id: sess.id, url: null, live: false, acuLimit: sess.acuLimit }).then(function () {
          saveReplay(id, engine);
          return dispatch(id, { type: "snapshot", snap: engine.poll() });
        }).then(function () { saveReplay(id, engine); schedulePoll(id); });
      }, function () {
        return notice(id, "The built-in replay needs the course once: connect to the server, then try again.");
      });
    }

    // ---- live ----
    function startLive(id) {
      var chat = get(id);
      return Promise.resolve().then(function () { return live.start({ prompt: chat.prompt, difficulty: chat.difficulty, maxAcu: 3 }); }).then(function (s) {
        return dispatch(id, { type: "session", id: s.id, url: s.url, live: true, acuLimit: s.acuLimit }).then(function () { schedulePoll(id); });
      }, function (err) { return fallback(id, K.fallbackReason(err)); });
    }

    function fallback(id, info) {
      cancelTimer(id);
      delete engines[id]; delete replayStates[id]; fails[id] = 0;
      return dispatch(id, { type: "fallback", reason: info.reason, text: info.text || K.REASON_TEXT[info.reason] });
    }

    // ---- polling ----
    function cancelTimer(id) {
      [id, id + ":pace"].forEach(function (k) { if (timers[k] !== undefined) { cancel(timers[k]); delete timers[k]; } });
    }
    function schedulePoll(id) {
      cancelTimer(id);
      var c = get(id);
      if (!c || !c.session || c.stopped || TERMINAL.indexOf(c.session.phase) >= 0) return;
      timers[id] = schedule(function () { delete timers[id]; return pollNow(id); }, c.source === "live" ? liveMs : replayMs);
    }

    function pollNow(id) {
      var chat = get(id);
      if (typeof hooks.bind === "function") hooks.bind();      // adopt a stress engine that loaded after this page
      if (!chat || !chat.session || chat.stopped) return Promise.resolve();
      if (inflight[id]) return inflight[id];
      var isLive = chat.source === "live";
      var p = Promise.resolve().then(function () {
        return isLive ? live.poll(chat.session.id) : engineFor(chat).then(function (e) { var s = e.poll(); saveReplay(id, e); return s; });
      }).then(function (snap) {
        fails[id] = 0;
        return dispatch(id, { type: "snapshot", snap: snap });
      }, function (err) {
        fails[id] = (fails[id] || 0) + 1;
        var info = K.fallbackReason(err);
        if (!isLive) return notice(id, "The replay stopped: " + err.message);
        if (info.reason === "quota" || info.reason === "no_bridge" || info.reason === "no_token" || fails[id] >= FAIL_LIMIT) return fallback(id, info);
      }).then(function () { delete inflight[id]; schedulePoll(id); }, function () { delete inflight[id]; });
      inflight[id] = p;
      return p;
    }

    // ---- public ----
    function submit(text, opts) {
      opts = opts || {};
      text = String(text || "").trim();
      if (!text) return Promise.resolve();
      var cur = active();
      if (cur && !opts.forceNew && !opts.demo && cur.session && !cur.stopped && TERMINAL.indexOf(cur.session.phase) < 0) {
        return dispatch(cur.id, { type: "user", text: text });
      }
      var replay = opts.demo === true || offline;
      var id = newId();
      var chat = K.createChat({ id: id, prompt: text, now: now(), source: replay ? "replay" : "live", course: replay ? "builtin" : null,
                                replayWhy: opts.demo ? "demo" : "offline_toggle", difficulty: hooks.getDifficulty() });
      chats = [chat].concat(chats); activeId = id; changed();
      return replay ? startReplay(id) : startLive(id);
    }

    function grade(id, n, kind, body) {
      var chat = get(id), st = chat && K.stageOf(chat, n);
      if (!chat || !st || !K.canWork(chat, n)) return Promise.reject(new Error("locked: earn the earlier stage first"));
      return B.grade({ fetch: o.fetch, url: K.gradeUrl(chat, st, kind), body: body, learner: K.learnerId(chat) }).then(function (res) {
        return dispatch(id, { type: "checkpoint", n: n, kind: kind, passed: res.passed === true, progress: res.progress, code: res.code, result: res.result }).then(function () {
          if (kind !== "explain" && typeof hooks.reportAttempt === "function") hooks.reportAttempt(res.passed === true);
          return res;
        });
      });
    }

    function reveal(id, n) {
      var chat = get(id), st = chat && K.stageOf(chat, n);
      if (!st || !K.isEarned(st)) return Promise.reject(new Error("locked"));
      var fetched = chat.course === "builtin"
        ? loadCourse({ learner: K.learnerId(chat) }).then(function (views) { return views.filter(function (v) { return v.id === st.id; })[0]; })
        : live.stage(chat.session.id, n);
      return fetched.then(function (v) { return v ? dispatch(id, { type: "stage_view", view: Object.assign({ n: n }, v) }) : undefined; });
    }

    function stop(id) { return dispatch(id, { type: "stop" }); }
    function fallbackNow(id, reason) { return fallback(id, { reason: reason || "api_error", text: K.REASON_TEXT[reason || "api_error"] }); }

    function setOffline(on) {
      offline = !!on; persist();
      var cur = active();
      if (offline && cur && cur.source === "live" && cur.session && !cur.stopped && TERMINAL.indexOf(cur.session.phase) < 0) {
        return fallback(cur.id, { reason: "offline_toggle", text: K.REASON_TEXT.offline_toggle });
      }
      onChange();
      return Promise.resolve();
    }

    function resume() {
      chats.forEach(function (c) { if (c.session && !c.stopped && TERMINAL.indexOf(c.session.phase) < 0) schedulePoll(c.id); });
    }

    var logAdaptation = o.logAdaptation || function (payload) {
      if (!o.fetch) return;
      try {
        Promise.resolve(o.fetch("/api/learn/adaptations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).catch(function () {});
      } catch (e) { /* the dashboard is best effort */ }
    };

    hooks.onAdapt(function (ev) {
      var cur = active();
      if (!cur) return;
      var event = { level: ev.level, previous: ev.prevLevel !== undefined ? ev.prevLevel : ev.previous, reason: ev.reason };
      dispatch(cur.id, Object.assign({ type: "adapt" }, event));
      logAdaptation(P.adaptPayload(event, cur));
    });

    return {
      chats: function () { return chats.slice(); }, active: active, get: get, isOffline: function () { return offline; },
      submit: submit, pollNow: pollNow, grade: grade, reveal: reveal, stop: stop, fallbackNow: fallbackNow, setOffline: setOffline, resume: resume,
      select: function (id) { activeId = get(id) ? id : null; changed(); },
      newChat: function () { activeId = null; changed(); },
      remove: function (id) {
        cancelTimer(id); delete engines[id]; delete replayStates[id];
        chats = chats.filter(function (c) { return c.id !== id; });
        if (activeId === id) activeId = null;
        changed();
      },
    };
  }

  var api = { createController: createController, lateHooks: lateHooks, installEarnHooks: installEarnHooks, TERMINAL: TERMINAL };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.ChatCtl = api;
})(typeof self !== "undefined" ? self : this);
