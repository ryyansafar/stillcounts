// The browser side of the Devin bridge. The browser only ever talks to this server's /api/devin routes: the Devin token
// lives on the server (ops/devin_client.py style) and is never sent here.
//
// Bridge contract (what the server must provide; the browser codes against it and names the failure when it is missing):
//   POST /api/devin/sessions               { prompt, user_prompt, max_acu_limit, structured_output_schema, difficulty }
//                                          -> { session_id, url, acu_limit }      503 {code:"no_token"}, 402/429 {code:"quota"}
//   GET  /api/devin/sessions/{id}          -> { status, status_detail, acu_consumed, acu_limit, url, plan:[{n,id,title,summary}],
//                                               messages:[{id,source,text}], pull_requests:[{url,title,state,ci,stage,review}],
//                                               stages:[ Learn-API-shaped stage + {n,status,files,concepts,bug_hint} ] }
//                                          stage.code is present only for a stage the learner earned (the bridge withholds the rest).
//   POST /api/devin/sessions/{id}/message  { text, reason: "continue"|"adapt"|"user", n }
//   POST /api/devin/sessions/{id}/stop     {}                                     (suspends the session)
//   POST /api/devin/sessions/{id}/stages/{n}/{predict|bug|write|explain}   same bodies and replies as /api/learn/{stage}/{kind}
//   GET  /api/devin/sessions/{id}/stages/{n}                                one stage view (code once earned)
(function (root) {
  "use strict";
  var COURSE_KEY = "sc_chat_course";

  var STRUCTURED_OUTPUT_SCHEMA = {
    type: "object",
    properties: {
      stages_done: { type: "integer", description: "How many stages are finished and committed." },
      stages: {
        type: "array",
        items: {
          type: "object",
          properties: {
            n: { type: "integer" },
            title: { type: "string" },
            summary: { type: "string", description: "What this stage added, in two sentences." },
            files: { type: "array", items: { type: "string" }, description: "Files touched in this stage." },
            concepts: { type: "array", items: { type: "string" }, description: "Key concepts a learner should understand." },
            bug_hint: { type: "string", description: "One realistic bug a learner could hunt in this stage's code." },
          },
          required: ["n", "title", "summary", "files", "concepts", "bug_hint"],
        },
      },
    },
    required: ["stages_done", "stages"],
  };

  function devinPrompt(userPrompt, difficulty) {
    var text = String(userPrompt || "").trim() + "\n\n" +
      "RULES FOR THIS SESSION. Keep the whole app under about 150 lines, built in 3 to 4 stages, each stage small enough to read in a minute. " +
      "Work on ONE stage, commit it, update the structured output, then STOP and wait (go to waiting_for_user). " +
      "Do not start the next stage until a message says 'continue'. A learner has to earn each stage before the next one is built, " +
      "so never skip ahead and never build more than the stage you were asked for.";
    return difficulty !== undefined && difficulty <= 1 ? text + " The learner is stressed: keep every stage smaller and add comments." : text;
  }

  function bridgeError(status, body, fallback) {
    var e = new Error((body && (body.detail || body.error)) || fallback || ("HTTP " + status));
    e.status = status; e.code = body && body.code;
    return e;
  }

  function call(fetchFn, timeoutMs, method, url, body, learner) {
    var headers = {};
    if (learner) headers["X-Learner-Id"] = learner;
    var init = { method: method, headers: headers };
    if (body !== undefined) { init.headers = Object.assign({ "Content-Type": "application/json" }, headers); init.body = JSON.stringify(body); }
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(new Error("timeout")); }, timeoutMs);
      Promise.resolve().then(function () { return fetchFn(url, init); }).then(function (r) {
        return Promise.resolve(r.json ? r.json().catch(function () { return {}; }) : {}).then(function (data) {
          clearTimeout(timer);
          if (!r.ok) reject(bridgeError(r.status, data));
          else resolve(data);
        });
      }, function (err) { clearTimeout(timer); reject(err); });
    });
  }

  function createLiveBridge(o) {
    var f = o.fetch, ms = o.timeoutMs || 8000, base = o.base || "/api/devin/sessions";
    return {
      start: function (req) {
        return call(f, ms, "POST", base, {
          prompt: devinPrompt(req.prompt, req.difficulty), user_prompt: req.prompt, max_acu_limit: req.maxAcu || 3,
          structured_output_schema: STRUCTURED_OUTPUT_SCHEMA, difficulty: req.difficulty,
        }).then(function (d) { return { id: d.session_id, url: d.url || null, acuLimit: d.acu_limit || req.maxAcu || 3, live: true }; });
      },
      poll: function (id) { return call(f, ms, "GET", base + "/" + encodeURIComponent(id)); },
      message: function (id, text, meta) {
        return call(f, ms, "POST", base + "/" + encodeURIComponent(id) + "/message", Object.assign({ text: text }, meta || {}));
      },
      stop: function (id) { return call(f, ms, "POST", base + "/" + encodeURIComponent(id) + "/stop", {}); },
      stage: function (id, n) { return call(f, ms, "GET", base + "/" + encodeURIComponent(id) + "/stages/" + n); },
    };
  }

  function grade(o) { return call(o.fetch, o.timeoutMs || 8000, "POST", o.url, o.body, o.learner); }

  // Built-in demo course: the stage list of the existing Learn API, kept in storage for offline use.
  function loadCourse(o) {
    function fromStorage(err) {
      var saved = null;
      try { saved = JSON.parse(o.storage.getItem(COURSE_KEY)); } catch (e) { saved = null; }
      if (Array.isArray(saved) && saved.length) return saved;
      throw err;
    }
    return call(o.fetch, o.timeoutMs || 8000, "GET", "/api/learn/stages", undefined, o.learner).then(function (data) {
      var list = Array.isArray(data) ? data : (data && data.stages) || [];
      var views = list.map(function (s, i) { return Object.assign({}, s, { n: i + 1 }); });
      var locked = views.map(function (v) { return v.unlocked ? v : Object.assign({}, v, { code: null }); });
      try { o.storage.setItem(COURSE_KEY, JSON.stringify(locked)); } catch (e) { /* storage full */ }
      if (!views.length) throw new Error("the Learn API returned no stages");
      return views;
    }, fromStorage);
  }

  var api = { STRUCTURED_OUTPUT_SCHEMA: STRUCTURED_OUTPUT_SCHEMA, devinPrompt: devinPrompt, createLiveBridge: createLiveBridge,
              grade: grade, loadCourse: loadCourse };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.ChatBridge = api;
})(typeof self !== "undefined" ? self : this);
