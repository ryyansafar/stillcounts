// OFFLINE REPLAY: a simulated Devin. It exists only for the fallback (API error, quota, offline toggle) and for the
// built-in demo course, and every line, PR and ACU number it produces is labelled "simulated". It is not a recording of
// a real session and it never pretends to be one.
//
// It obeys the same gate as the real thing: after each stage it stops (waiting_for_user) and builds the next stage only
// when it receives a message with reason "continue". The state is plain JSON so a reload can pick it up again.
(function (root) {
  "use strict";
  var ACU_LIMIT = 3, ACU_PER_LINE = 0.1;

  function linesFor(view, small) {
    var out = ["(simulated) reading the task for stage " + view.n + ": " + view.title,
               "(simulated) writing the code for this stage",
               "(simulated) running the checks"];
    if (small) out.splice(2, 0, "(simulated) smaller stage with comments, as the bridge asked");
    out.push("(simulated) opening a pull request");
    return out;
  }

  function createReplay(opts) {
    var views = opts.views, now = opts.now || Date.now, stepMs = opts.stepMs || 1200;
    var s = opts.state || { phase: "idle", built: 0, building: 0, buildStart: 0, emitted: 0, small: false, msgs: [], prs: [], seq: 0, id: null };

    function say(text, source) { s.seq += 1; s.msgs.push({ id: "r" + s.seq, source: source || "devin", text: text }); }

    function finishStage() {
      var n = s.building, v = views[n - 1];
      s.built = n;
      say("(simulated) stage " + n + " built. Stopping and waiting for the learner to earn it.");
      s.prs.push({ url: "replay:pr-" + n, title: "Stage " + n + ": " + v.title + " (simulated PR)", state: "open", ci: "simulated", stage: n,
                   review: { state: "none", comments: 0 }, simulated: true });
      s.phase = n < views.length ? "waiting" : "done";
    }

    function advance() {
      if (s.phase !== "building") return;
      var step = stepMs * (s.slow ? 2 : 1), lines = linesFor(views[s.building - 1], s.small), elapsed = now() - s.buildStart;
      var due = Math.min(lines.length, Math.floor(elapsed / step) + 1);
      while (s.emitted < due) say(lines[s.emitted++]);
      if (elapsed >= lines.length * step) finishStage();
    }

    function beginStage(n) { s.building = n; s.buildStart = now(); s.emitted = 0; s.phase = "building"; }

    function statusOf() {
      if (s.phase === "building") return ["running", "working"];
      if (s.phase === "waiting") return ["running", "waiting_for_user"];
      if (s.phase === "done") return ["exit", "finished"];
      if (s.phase === "stopped") return ["suspended", "user_request"];
      return ["new", null];
    }

    return {
      start: function (prompt) {
        s.id = "replay-" + now(); s.prompt = String(prompt || "");
        say("(simulated) replaying a build of the built-in course. This is not a live Devin session.");
        beginStage(1);
        return { id: s.id, url: null, live: false, acuLimit: ACU_LIMIT };
      },
      poll: function () {
        advance();
        var st = statusOf();
        return {
          status: st[0], status_detail: st[1], simulated: true, url: null,
          acu_consumed: Math.round(s.msgs.length * ACU_PER_LINE * 10) / 10, acu_limit: ACU_LIMIT,
          plan: views.map(function (v) { return { n: v.n, id: v.id, title: v.title, summary: v.summary }; }),
          messages: s.msgs.slice(),
          stages: views.slice(0, s.built).map(function (v) {
            var out = Object.assign({}, v, { status: "built" });
            if (!v.unlocked) out.code = null;
            return out;
          }),
          pull_requests: s.prs.slice(),
        };
      },
      message: function (text, meta) {
        var reason = meta && meta.reason;
        if (s.phase === "stopped" || s.phase === "done" || s.phase === "idle") return;
        if (meta && meta.slow !== undefined) s.slow = meta.slow === true;
        if (reason === "continue") {
          if (s.phase === "waiting") { say("(simulated) got 'continue', starting stage " + (s.built + 1) + "."); beginStage(s.built + 1); }
        } else if (reason === "adapt") {
          s.small = /stressed/i.test(text);
          say(s.small ? "(simulated) Devin: understood, the next stage will be smaller and commented." : "(simulated) Devin: understood, the next stage goes back to normal size.");
        } else {
          say("(simulated) Devin: noted. I will not start the next stage until the bridge says continue.");
        }
      },
      stop: function () { s.phase = "stopped"; say("(simulated) session suspended."); },
      state: function () { return JSON.parse(JSON.stringify(s)); },
    };
  }

  var api = { createReplay: createReplay, ACU_LIMIT: ACU_LIMIT };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.ChatReplay = api;
})(typeof self !== "undefined" ? self : this);
