// Learn mode, "Earn the Code": the real merge code stays locked until the learner earns a stage.
// Plain code grades every checkpoint: predict is checked in the browser by the same merge the app uses (crdt.js),
// so it works with no signal; find-the-bug and write are graded by the server's referee; explain shows which rubric points
// were covered. Every request carries an X-Learner-Id header (a random id kept on this phone).
//
// API (server/learn_api.py), stage = { id, order, title, summary, unlocked, progress, code (null while locked),
//   predict: { prompt, cases: [{ id, prompt, question, ops, item, field }] },
//   bug: { prompt, snippet, options: [{ id, text }] }, write: { function, prompt, stub }, explain: { prompt, min_points } }
//   GET  /api/learn/stages                      -> { stages: [stage] }
//   POST /api/learn/{stage}/predict { answer: { caseId: guess } } -> { passed, progress, unlocked, code }
//   POST /api/learn/{stage}/bug     { answer: optionId }
//   POST /api/learn/{stage}/write   { source }                    -> result.error / result.counterexample on failure
//   POST /api/learn/{stage}/explain { text }          -> result.covered / result.missed
// Every response carries `progress`, `unlocked` and, once unlocked, `code`.
(function (root) {
  "use strict";
  var C = typeof module !== "undefined" && module.exports ? require("./crdt.js") : root.StillCounts;
  var KINDS = ["predict", "bug", "write", "explain"];
  var LABELS = { predict: "Predict", bug: "Find the bug", write: "Write", explain: "Explain" };
  var KEY = "sc_learn";
  var TIMEOUT_MS = 4000;

  function payloadOf(raw) {
    var out = {};
    Object.keys(raw || {}).forEach(function (k) { if (k !== "kind" && k !== "type") out[k] = raw[k]; });
    return out;
  }

  function normalizeStages(data) {
    var list = Array.isArray(data) ? data : (data && Array.isArray(data.stages) ? data.stages : []);
    return list.map(function (s) {
      var id = String(s.id || s.stage || s.slug || s.name), cps = {};
      if (Array.isArray(s.checkpoints)) {
        s.checkpoints.forEach(function (c) { cps[c.kind || c.type] = payloadOf(c); });
      } else if (s.checkpoints && typeof s.checkpoints === "object") {
        Object.keys(s.checkpoints).forEach(function (k) { cps[k] = payloadOf(s.checkpoints[k]); });
      } else {
        KINDS.forEach(function (k) { if (s[k] && typeof s[k] === "object") cps[k] = payloadOf(s[k]); });
      }
      if (s.progress && typeof s.progress === "object") {
        Object.keys(cps).forEach(function (k) { if (s.progress[k] === true) cps[k].passed = true; });
      }
      return { id: id, title: s.title || s.name || id, summary: s.summary || s.description || "",
               earned: !!(s.earned || s.unlocked), locked: s.locked, code: s.code || null, checkpoints: cps,
               order: KINDS.filter(function (k) { return cps[k]; }) };
    });
  }

  // one predict case: { id, ops, item, field }
  function predictAnswer(c) {
    var ops = c.ops || [], field = c.field || "qty";
    if (field === "oversold") return C.stats(ops).oversold.indexOf(c.item) >= 0;
    var state = C.merge(ops), s = state[c.item] || { name: c.item, qty: 0, note: "", alive: true };
    return s[field];
  }

  function wordOf(g) {
    if (/^(true|yes|y)$/i.test(g)) return true;
    if (/^(false|no|n)$/i.test(g)) return false;
    return null;
  }

  // true / false, or null when the guess is not an answer at all
  function gradePredict(c, guess) {
    var answer = predictAnswer(c), g = String(guess === undefined || guess === null ? "" : guess).trim();
    if (typeof answer === "number") {
      if (!/^[-−]?\d+$/.test(g)) return null;
      return parseInt(g.replace("−", "-"), 10) === answer;
    }
    if (typeof answer === "boolean") { var w = wordOf(g); return w === null ? null : w === answer; }
    return g === "" ? null : g === String(answer);
  }

  // what is sent to the server for one guess
  function guessValue(c, guess) {
    var g = String(guess).trim(), answer = predictAnswer(c);
    if (typeof answer === "number") return parseInt(g.replace("−", "-"), 10);
    if (typeof answer === "boolean") return wordOf(g);
    return g;
  }

  function hintFor(c) {
    var a = predictAnswer(c);
    return typeof a === "number" ? "a whole number" : typeof a === "boolean" ? "yes or no" : "the text";
  }

  function newLearnerId() {
    var id = "l-";
    for (var i = 0; i < 12; i++) id += Math.floor(Math.random() * 36).toString(36);
    return id;
  }

  function key(stage, kind) { return stage.id + "/" + kind; }

  // Explain is practice: the server unlocks the real code after predict, find the bug and write.
  function required(stage) { return stage.order.filter(function (k) { return k !== "explain"; }); }

  function isDone(stage, kind, passed) {
    return !!passed[key(stage, kind)] || (!!stage.earned && kind !== "explain");
  }

  function isEarned(stage, passed) {
    if (stage.earned) return true;
    var need = required(stage);
    return need.length > 0 && need.every(function (k) { return passed[key(stage, k)]; });
  }

  function isStageLocked(stages, i, passed) {
    if (stages[i].locked === false || isEarned(stages[i], passed)) return false;
    return i > 0 && !isEarned(stages[i - 1], passed);
  }

  function isCheckpointLocked(stage, kind, passed) {
    var i = stage.order.indexOf(kind);
    if (i < 0) return true;
    if (i === 0 || isEarned(stage, passed)) return false;
    return !passed[key(stage, stage.order[i - 1])];
  }

  function progress(stages, passed) {
    var done = 0, total = 0;
    stages.forEach(function (s) {
      var earned = isEarned(s, passed);
      s.order.forEach(function (k) { total += 1; if ((earned && k !== "explain") || passed[key(s, k)]) done += 1; });
    });
    return { done: done, total: total, pct: total ? Math.round(100 * done / total) : 0 };
  }

  function passOf(body, fallback) {
    var keys = ["passed", "correct", "ok", "earned"];
    for (var i = 0; i < keys.length; i++) if (typeof body[keys[i]] === "boolean") return body[keys[i]];
    return fallback;
  }

  function describeOp(op, seen) {
    var what = op.kind === "adjust" ? (op.value > 0 ? "+" : "") + op.value + " " + op.item
      : op.kind + " " + op.item + " " + JSON.stringify(op.value);
    var line = "phone " + op.replica + " · time " + op.lamport + " · " + what;
    if (seen[op.id]) line += "  (same id " + op.id + " again)";
    seen[op.id] = true;
    return line;
  }

  function mount(container, opts) {
    var doc = opts.doc, fetchFn = opts.fetch, storage = opts.storage;
    var saved = {};
    try { saved = JSON.parse(storage.getItem(KEY)) || {}; } catch (e) { saved = {}; }
    var st = { stages: normalizeStages(saved.stages || []), raw: saved.stages || null, passed: saved.passed || {},
               queue: saved.queue || [], codes: saved.codes || {}, drafts: saved.drafts || {}, selected: saved.selected || null,
               online: null, loaded: false, learner: saved.learner || newLearnerId() };
    var pending = [], view = {};

    function persist() {
      try {
        storage.setItem(KEY, JSON.stringify({ learner: st.learner, stages: st.raw, passed: st.passed, queue: st.queue, codes: st.codes,
                                              drafts: st.drafts, selected: st.selected }));
      } catch (e) { /* storage full: keep going in memory */ }
    }
    function track(p) { pending.push(p); return p; }
    function idle() {
      var now = pending.splice(0);
      return Promise.all(now).then(function () { return pending.length ? idle() : undefined; });
    }

    function h(tag, attrs, kids) {
      var e = doc.createElement(tag);
      Object.keys(attrs || {}).forEach(function (k) {
        var v = attrs[k];
        if (v === undefined || v === null || v === false) return;
        if (k === "text") e.textContent = v;
        else if (k === "className" || k === "type" || k === "value" || k === "placeholder" || k === "disabled") e[k] = v;
        else e.setAttribute(k, v);
      });
      (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? doc.createTextNode(c) : c); });
      return e;
    }

    function request(url, body) {
      var headers = { "X-Learner-Id": st.learner };
      var init = body === undefined ? { method: "GET", headers: headers }
        : { method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, headers), body: JSON.stringify(body) };
      return new Promise(function (resolve, reject) {
        var timer = setTimeout(function () { reject(new Error("timeout")); }, TIMEOUT_MS);
        Promise.resolve().then(function () { return fetchFn(url, init); }).then(function (r) {
          clearTimeout(timer); st.online = true;
          if (!r.ok) { var err = new Error("HTTP " + r.status); err.status = r.status; throw err; }
          return r.json();
        }).then(resolve, function (err) {
          clearTimeout(timer);
          if (!err.status) st.online = false;
          reject(err);
        });
      });
    }
    function post(stage, kind, body) { return request("/api/learn/" + encodeURIComponent(stage.id) + "/" + kind, body); }

    function stageById(id) { return st.stages.filter(function (s) { return s.id === id; })[0]; }

    function absorb(stage, kind, body, fallback) {
      var ok = passOf(body, fallback);
      if (ok) st.passed[key(stage, kind)] = true;
      if (body.progress && typeof body.progress === "object") {
        Object.keys(body.progress).forEach(function (k) { if (body.progress[k] === true) st.passed[key(stage, k)] = true; });
      }
      if (body.code) st.codes[stage.id] = body.code;
      persist();
      return ok;
    }

    function load() {
      return request("/api/learn/stages").then(function (data) {
        st.raw = data; st.stages = normalizeStages(data); st.loaded = true;
        st.stages.forEach(function (s) {
          s.order.forEach(function (k) { if (s.checkpoints[k].passed) st.passed[key(s, k)] = true; });
          if (s.code) st.codes[s.id] = s.code;
        });
        persist();
      }, function () { /* offline: keep the cached lessons */ }).then(render);
    }

    function flush() {
      if (!st.queue.length) return Promise.resolve();
      var item = st.queue[0], stage = stageById(item.stage);
      return post(stage || { id: item.stage }, item.kind, item.body).then(function (body) {
        st.queue.shift();
        if (stage) absorb(stage, item.kind, body, false); else persist();
        return flush();
      }, function (err) {
        if (err.status) { st.queue.shift(); persist(); return flush(); }   // the server answered: do not retry forever
      }).then(refresh);
    }

    function setResult(kind, cls, text) {
      var r = view.results[kind]; if (!r) return;
      r.className = "result " + cls; r.textContent = text;
    }

    function offlineText(err) {
      if (err && err.status === 403) return "Code is graded on the base laptop only. Ask the host to run it there, or to allow phones on this network.";
      return err && err.status ? "The server could not grade this (HTTP " + err.status + "). Try again later."
        : "Needs a signal: the referee runs on the server. Your answer is kept on this phone.";
    }

    // ---- actions ----
    function doPredict(stage) {
      var cases = stage.checkpoints.predict.cases || [], guesses = {}, local = {}, unanswered = null;
      cases.forEach(function (c) {
        var raw = view.inputs["predict:" + c.id].value, g = gradePredict(c, raw);
        if (g === null) { unanswered = unanswered || c; return; }
        local[c.id] = g; guesses[c.id] = guessValue(c, raw);
      });
      if (unanswered) { setResult("predict", "warn", "Answer every case (" + hintFor(unanswered) + " for " + unanswered.id + ")."); return Promise.resolve(); }
      var allRight = cases.every(function (c) { return local[c.id]; }), body = { answer: guesses };
      function show(ok) {
        var wrong = cases.filter(function (c) { return !local[c.id]; });
        setResult("predict", ok ? "good" : "bad", ok ? "Right. The real merge agrees on every case."
          : "Not quite. " + wrong.map(function (c) { return c.id + ": the real merge gives " + predictAnswer(c); }).join("; ") + ".");
        refresh();
      }
      return post(stage, "predict", body).then(function (res) { show(absorb(stage, "predict", res, allRight)); }, function (err) {
        if (!err.status && allRight) { st.queue.push({ stage: stage.id, kind: "predict", body: body }); }
        if (allRight) st.passed[key(stage, "predict")] = true;
        persist(); show(allRight);
      });
    }

    function doBug(stage, answer) {
      answer = String(answer || "").trim();
      if (!answer) { setResult("bug", "warn", "Say what is wrong first."); return Promise.resolve(); }
      return post(stage, "bug", { answer: answer }).then(function (res) {
        var ok = absorb(stage, "bug", res, false);
        setResult("bug", ok ? "good" : "bad", ok ? "Found it." + (res.explanation ? " " + res.explanation : "") : "Not that one. Look again.");
        refresh();
      }, function (err) { setResult("bug", "warn", offlineText(err)); refresh(); });
    }

    function failureText(r) {
      if (r.error) return r.error;
      var c = r.counterexample;
      if (c) return "on " + (c.ops || []).length + " operations the real merge gives " + JSON.stringify(c.expected) + " and yours gives " + JSON.stringify(c.got) + ".";
      return r.failure || r.message || r.detail || "the referee found a difference.";
    }

    function doWrite(stage) {
      var code = view.inputs.write.value;
      st.drafts[key(stage, "write")] = code; persist();
      if (!code.trim()) { setResult("write", "warn", "Write the function first."); return Promise.resolve(); }
      setResult("write", "", "Running the referee…");
      return post(stage, "write", { source: code }).then(function (res) {
        var ok = absorb(stage, "write", res, false);
        setResult("write", ok ? "good" : "bad", ok ? "Passed the referee." : "Not yet: " + failureText(res.result || res));
        refresh();
      }, function (err) { setResult("write", "warn", offlineText(err)); refresh(); });
    }

    function doExplain(stage) {
      var text = view.inputs.explain.value;
      st.drafts[key(stage, "explain")] = text; persist();
      if (!text.trim()) { setResult("explain", "warn", "Write a sentence or two first."); return Promise.resolve(); }
      return post(stage, "explain", { text: text }).then(function (res) {
        var ok = absorb(stage, "explain", res, true), r = res.result || res, points = [];
        (r.covered || []).forEach(function (p) { points.push("Covered: " + (p.point || p)); });
        (r.missed || []).forEach(function (p) { points.push("Missed: " + (p.point || p)); });
        (r.rubric || res.rubric || []).forEach(function (p) { points.push(typeof p === "string" ? p : (p.text || p.point || JSON.stringify(p))); });
        setResult("explain", ok ? "good" : "bad", ok ? "Good. Compare your words with the rubric." : "Not yet. Compare with the rubric and try again.");
        var list = h("ul", { className: "rubric", "data-rubric": "explain" }, points.map(function (t) { return h("li", { text: t }); }));
        view.rubric.replaceChildren(h("h4", { text: "The rubric" }), list);
        refresh();
      }, function (err) { setResult("explain", "warn", offlineText(err)); refresh(); });
    }

    function doReveal(stage) {
      function show() {
        var code = st.codes[stage.id] || stage.code;
        view.codeBox.replaceChildren(code ? h("pre", { className: "code real", "data-code": stage.id, text: code })
          : h("p", { className: "result warn", text: "Connect once to download the real code." }));
      }
      if (st.codes[stage.id] || stage.code) { show(); return Promise.resolve(); }
      return load().then(show);
    }

    function guard(stage, kind) {
      if (!isCheckpointLocked(stage, kind, st.passed)) return false;
      setResult(kind, "warn", "Locked. Earn the previous checkpoint first.");
      return true;
    }

    // ---- rendering ----
    function card(stage, kind) {
      var cp = stage.checkpoints[kind], kids = [];
      view.results[kind] = h("p", { className: "result", "data-result": kind, "aria-live": "polite" });
      var draft = st.drafts[key(stage, kind)];
      if (kind === "predict") {
        kids.push(h("p", { text: cp.prompt || "Predict what the real merge gives for each case." }));
        (cp.cases || []).forEach(function (c) {
          var seen = {}, input = h("input", { type: typeof predictAnswer(c) === "number" ? "number" : "text", className: "field",
            "data-input": "predict:" + c.id, "aria-label": "Your prediction for " + c.id, placeholder: hintFor(c) });
          if (typeof predictAnswer(c) === "number") input.setAttribute("inputmode", "numeric");
          view.inputs["predict:" + c.id] = input;
          kids.push(h("div", { className: "case", "data-case": c.id }, [
            h("p", { text: c.prompt || "" }),
            h("p", { text: c.question || ("What is the " + (c.field || "quantity") + " of " + c.item + " after these phones merge?") }),
            h("ol", { className: "ops" }, (c.ops || []).map(function (op) { return h("li", { text: describeOp(op, seen) }); })),
            input]));
        });
        kids.push(h("button", { type: "button", className: "wide", "data-action": "predict", text: "Check with the real merge" }));
      } else if (kind === "bug") {
        var m = cp.mutant || {}, mcode = cp.snippet || cp.mutant_source || (typeof m === "string" ? m : (m.code || m.diff || m.snippet || ""));
        kids.push(h("p", { text: cp.prompt || "This copy of the real merge has one deliberate mistake. What is wrong?" }));
        kids.push(h("pre", { className: "code", text: mcode }));
        var options = cp.options || cp.choices;
        if (Array.isArray(options) && options.length) {
          kids.push(h("div", { className: "choices" }, options.map(function (c) {
            var id = typeof c === "string" ? c : c.id, label = typeof c === "string" ? c : (c.text || c.id);
            var b = h("button", { type: "button", "data-choice": id, text: label });
            b.addEventListener("click", function () { if (!guard(stage, "bug")) track(doBug(stage, id)); });
            return b;
          })));
        } else {
          view.inputs.bug = h("input", { type: "text", className: "field", "data-input": "bug", "aria-label": "What is wrong", placeholder: "What is wrong?" });
          kids.push(h("div", { className: "row" }, [view.inputs.bug, h("button", { type: "button", "data-action": "bug", text: "Check" })]));
        }
      } else if (kind === "write") {
        kids.push(h("p", { text: cp.prompt || "Complete the function. The referee grades it." }));
        view.inputs.write = h("textarea", { className: "field codein", rows: "8", spellcheck: "false", autocapitalize: "off",
                                            "data-input": "write", "aria-label": "Your code", wrap: "off", value: draft !== undefined ? draft : (cp.stub || cp.starter || "") });
        kids.push(view.inputs.write);
        kids.push(h("button", { type: "button", className: "wide", "data-action": "write", text: "Run the referee" }));
      } else {
        kids.push(h("p", { text: cp.prompt || "Explain in your own words why this merge is correct." }));
        view.inputs.explain = h("textarea", { className: "field", rows: "4", "data-input": "explain", "aria-label": "Your explanation",
                                              value: draft || "" });
        kids.push(view.inputs.explain);
        kids.push(h("button", { type: "button", className: "wide", "data-action": "explain", text: "Submit and see the rubric" }));
        view.rubric = h("div", { className: "rubricbox" });
      }
      kids.push(view.results[kind]);
      if (view.rubric && kind === "explain") kids.push(view.rubric);
      var el = h("section", { className: "card cp", "data-checkpoint": kind }, [
        h("h3", {}, [h("span", { className: "lock", "data-lock": kind }), LABELS[kind]])].concat(kids));
      var actions = {
        predict: function () { return doPredict(stage); }, bug: function () { return doBug(stage, view.inputs.bug.value); },
        write: function () { return doWrite(stage); }, explain: function () { return doExplain(stage); },
      };
      all(el, function (n) { return n.getAttribute && n.getAttribute("data-action") === kind; }).forEach(function (b) {
        b.addEventListener("click", function () { if (!guard(stage, kind)) track(actions[kind]()); });
      });
      view.cards[kind] = el;
      return el;
    }

    function all(node, pred, out) {
      out = out || [];
      if (pred(node)) out.push(node);
      (node.children ? Array.prototype.slice.call(node.children) : []).forEach(function (c) { all(c, pred, out); });
      return out;
    }

    function pickStage() {
      var s = st.selected && stageById(st.selected);
      if (s && !isStageLocked(st.stages, st.stages.indexOf(s), st.passed)) return s;
      for (var i = 0; i < st.stages.length; i++) {
        if (!isStageLocked(st.stages, i, st.passed) && !isEarned(st.stages[i], st.passed)) return st.stages[i];
      }
      return st.stages[0];
    }

    function render() {
      view = { cards: {}, results: {}, inputs: {}, items: {} };
      view.net = h("span", { className: "pill", role: "status", "data-status": "net" });
      view.fill = h("div", { className: "fill" });
      view.bar = h("div", { className: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
                            "aria-label": "Checkpoints earned" }, [view.fill]);
      view.barText = h("div", { className: "bartext" });
      var kids = [h("header", {}, [h("h1", { text: "Learn · Earn the Code" }), view.net]),
                  h("div", { className: "sub", text: "The real merge code stays locked until you earn each stage. Plain code checks your answers." }),
                  view.bar, view.barText];
      if (!st.stages.length) {
        kids.push(h("p", { className: "card empty", "data-status": "empty",
          text: st.online === false ? "No signal. Connect once to download the lessons; after that they work offline."
                                    : "Loading lessons… Connect once to download them; after that they work offline." }));
        container.replaceChildren.apply(container, kids);
        refresh();
        return;
      }
      var list = h("ol", { className: "stages" }, st.stages.map(function (s) {
        var b = h("button", { type: "button", className: "stage" }, [h("span", { className: "lock" }), h("span", { className: "title", text: s.title }),
                                                                      h("span", { className: "count" })]);
        var li = h("li", { "data-stage": s.id }, [b]);
        b.addEventListener("click", function () {
          if (isStageLocked(st.stages, st.stages.indexOf(s), st.passed)) return;
          st.selected = s.id; persist(); render();
        });
        view.items[s.id] = { li: li, button: b };
        return li;
      }));
      kids.push(list);
      var stage = pickStage();
      view.stage = stage;
      var detail = [h("h2", { text: stage.title })];
      if (stage.summary) detail.push(h("p", { className: "sub", text: stage.summary }));
      stage.order.forEach(function (k) { detail.push(card(stage, k)); });
      view.codeBox = h("div", { className: "codebox" });
      view.reveal = h("div", { className: "reveal" });
      detail.push(view.reveal, view.codeBox);
      kids.push(h("section", { className: "detail", "data-detail": stage.id }, detail));
      container.replaceChildren.apply(container, kids);
      refresh();
    }

    function setLock(el, locked, earned) {
      el.className = "lock" + (earned ? " earned" : locked ? " locked" : " open");
      el.setAttribute("aria-label", earned ? "earned" : locked ? "locked" : "open");
      el.setAttribute("role", "img");
      el.textContent = earned ? "✓" : "";
    }

    function refresh() {
      if (!view.bar) return;
      var p = progress(st.stages, st.passed);
      view.bar.setAttribute("aria-valuenow", String(p.pct));
      view.fill.setAttribute("style", "width:" + p.pct + "%");
      view.barText.textContent = p.done + " of " + p.total + " checkpoints earned";
      var q = st.queue.length;
      view.net.className = "pill " + (st.online === false ? (q ? "queued" : "bad") : st.online ? "ok" : "");
      view.net.textContent = st.online === false ? "offline" + (q ? " · " + q + " to send" : "") : st.online ? "online" : "starting";
      st.stages.forEach(function (s, i) {
        var it = view.items[s.id]; if (!it) return;
        var locked = isStageLocked(st.stages, i, st.passed), earned = isEarned(s, st.passed);
        it.li.setAttribute("data-locked", String(locked));
        it.li.className = (view.stage === s ? "current " : "") + (earned ? "earned" : locked ? "locked" : "");
        it.button.disabled = locked;
        it.button.setAttribute("aria-label", s.title + (earned ? ", earned" : locked ? ", locked" : ""));
        setLock(it.button.children[0], locked, earned);
        var n = s.order.filter(function (k) { return isDone(s, k, st.passed) || (earned && k !== "explain"); }).length;
        it.button.children[2].textContent = n + "/" + s.order.length;
      });
      var stage = view.stage; if (!stage) return;
      stage.order.forEach(function (k) {
        var el = view.cards[k]; if (!el) return;
        var locked = isCheckpointLocked(stage, k, st.passed), done = isDone(stage, k, st.passed);
        el.setAttribute("data-locked", String(locked));
        el.className = "card cp" + (locked ? " locked" : "") + (done ? " done" : "");
        all(el, function (n) { return n.getAttribute && n.getAttribute("data-lock") === k; }).forEach(function (l) { setLock(l, locked, done); });
        all(el, function (n) { return n.tagName === "BUTTON" || n.tagName === "INPUT" || n.tagName === "TEXTAREA"; })
          .forEach(function (n) { n.disabled = locked; });
      });
      if (isEarned(stage, st.passed) && !view.reveal.children.length) {
        var b = h("button", { type: "button", className: "wide unlock", "data-action": "reveal", text: "Reveal the real code" });
        b.addEventListener("click", function () { track(doReveal(stage)); });
        view.reveal.appendChild(h("p", { className: "result good", text: "Stage earned. The real code is unlocked." }));
        view.reveal.appendChild(b);
      }
    }

    render();
    var ready = track(load().then(flush));
    return { ready: ready, idle: idle, flush: function () { return track(flush().then(load)); }, reload: load, state: st };
  }

  var api = { KINDS: KINDS, normalizeStages: normalizeStages, predictAnswer: predictAnswer, gradePredict: gradePredict,
              isEarned: isEarned, isStageLocked: isStageLocked, isCheckpointLocked: isCheckpointLocked, progress: progress, mount: mount };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.Learn = api;
  var el = root.document && root.document.getElementById("learn");
  if (!el) return;
  var store;
  try { store = root.localStorage; store.getItem(KEY); } catch (e) {
    var mem = {}; store = { getItem: function (k) { return k in mem ? mem[k] : null; }, setItem: function (k, v) { mem[k] = String(v); } };
  }
  var app = mount(el, { doc: root.document, fetch: root.fetch.bind(root), storage: store });
  root.addEventListener("online", function () { app.flush(); });
  root.addEventListener("offline", function () { app.state.online = false; app.reload(); });
  if ("serviceWorker" in root.navigator) root.navigator.serviceWorker.register("/sw.js").catch(function () {});
})(typeof self !== "undefined" ? self : this);
