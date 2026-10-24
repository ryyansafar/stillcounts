// The chat shell: sidebar with past chats, a thread of bubbles, a composer. Devin builds, the tutor gates, and the code of a
// stage stays locked until the learner earns it. All decisions live in chat_core.js, chat_view.js and chat_ctl.js; this file only
// turns view blocks into elements and wires the buttons.
(function (root) {
  "use strict";
  var isNode = typeof module !== "undefined" && module.exports;
  var K = isNode ? require("./chat_core.js") : root.ChatCore;
  var V = isNode ? require("./chat_view.js") : root.ChatView;
  var Ctl = isNode ? require("./chat_ctl.js") : root.ChatCtl;
  var B = isNode ? require("./chat_bridge.js") : root.ChatBridge;
  var P = isNode ? require("./devin_live.js") : root.DevinLive;
  var HINTS = {
    predict: "Apply the operations one at a time, in order, and keep a running value in your head.",
    bug: "Compare each line with what the stage summary says it should do. Bugs hide at boundaries and in signs.",
    write: "Start with the empty case, then the plain case. The steps above follow the docstring.",
    explain: "Say what the code does, then what would go wrong without the key line.",
  };
  var LABELS = { predict: "Predict", bug: "Find the bug", write: "Write", explain: "Explain (practice)" };
  var IDEAS = ["a tip calculator", "a todo list for the terminal", "a dice roller with history", "a unit converter"];
  var LINKS = [["Portfolio", "https://ryyansafar.site"], ["GitHub", "https://github.com/ryyansafar"], ["Buy me a coffee", "https://buymeacoffee.com/ryyansafar"],
               ["PayPal", "https://www.paypal.com/paypalme/ryyansafar"], ["Razorpay", "https://razorpay.me/@ryyansafar"]];

  function mountChat(o) {
    var doc = o.doc, ctl = o.ctl, host = o.host || {};
    var ui = { stick: true, results: {}, busy: {}, drafts: {}, sideOpen: false, cache: {}, composer: "" };

    function h(tag, attrs, kids) {
      var e = doc.createElement(tag);
      Object.keys(attrs || {}).forEach(function (k) {
        var v = attrs[k];
        if (v === undefined || v === null || v === false) return;
        if (k === "text") e.textContent = v;
        else if (k === "className" || k === "type" || k === "value" || k === "placeholder" || k === "disabled" || k === "checked" || k === "href") e[k] = v;
        else e.setAttribute(k, v);
      });
      (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? doc.createTextNode(c) : c); });
      return e;
    }
    function on(el, type, fn) { el.addEventListener(type, fn); return el; }
    function chip(text, cls) { return h("span", { className: "chip " + (cls || ""), text: text }); }
    function $(id) { return doc.getElementById(id); }

    // ---------- bubbles ----------
    function devin(kids, cls) { return h("div", { className: "msg devin " + (cls || "") }, [h("span", { className: "avatar", "aria-hidden": "true", text: "D" }), h("div", { className: "bubble" }, kids)]); }
    function tutor(kids, cls) { return h("div", { className: "msg tutor " + (cls || "") }, [h("span", { className: "avatar", "aria-hidden": "true", text: "T" }), h("div", { className: "bubble" }, kids)]); }

    function codePanel(b) {
      if (b.locked) {
        return h("div", { className: "codepanel locked", "data-code-state": "locked", role: "group", "aria-label": "Code locked" }, [
          h("span", { className: "padlock", "aria-hidden": "true" }),
          h("div", {}, [h("strong", { text: "Code locked" }), h("div", { className: "sub", text: "Devin wrote it and stopped. Earn this stage to read it." })]),
          h("div", { className: "redact", "aria-hidden": "true" }, [h("i"), h("i"), h("i"), h("i")])]);
      }
      if (b.needCode) {
        var btn = on(h("button", { type: "button", className: "primary", "data-action": "reveal", text: "Reveal the code" }), "click", function () {
          ctl.reveal(ctl.active().id, b.n).catch(function () { btn.textContent = "Needs a signal. Try again"; });
        });
        return h("div", { className: "codepanel open", "data-code-state": "needcode" }, [h("strong", { text: "Stage earned." }), btn]);
      }
      var lines = (b.diff || []).map(function (d) {
        return h("div", { className: "ln d-" + d.t }, [h("span", { className: "gutter", text: d.t === "add" ? "+" : d.t === "del" ? "-" : " " }), h("span", { className: "src", text: d.text })]);
      });
      return h("div", { className: "codepanel open", "data-code-state": "open" }, [
        h("div", { className: "legend", text: "Unlocked. Green lines are new in this stage." }), h("pre", { className: "diff", "data-code": b.n }, lines)]);
    }

    function stageBubble(b) {
      return devin([h("div", { className: "eyebrow", text: "Stage " + b.n + " built" }), h("h3", { text: b.title }), b.summary ? h("p", { text: b.summary }) : null,
        h("div", { className: "chips" }, b.files.map(function (f) { return chip(f, "file"); }).concat(b.concepts.map(function (c) { return chip(c, "concept"); }))),
        codePanel(b)], "stage");
    }

    function planBubble(b) {
      var label = { queued: "queued", building: "Devin is building", built: "built, waiting for you" };
      return devin([h("div", { className: "eyebrow", text: "Plan" }), h("h3", { text: b.stages.length + " stages" }),
        h("ol", { className: "plan" }, b.stages.map(function (s) {
          return h("li", { "data-stage": s.n }, [h("span", { className: "t", text: s.title }), chip(s.earned ? "earned" : label[s.state], s.earned ? "good" : s.state === "building" ? "warn" : "")]);
        })), h("p", { className: "sub", text: "Devin builds one stage, stops, and only goes on once you earn the stage before it." })]);
    }

    function logBubble(b) {
      var live = ctl.active() && ctl.active().source === "live";
      return devin([h("div", { className: "eyebrow", text: live ? "Build log, streamed from the Devin session" : "Build log, SIMULATED" }),
                    h("pre", { className: "log", text: b.lines.join("\n") })]);
    }

    function prBubble(b) {
      var pr = b.pr, link = V.safeLink(pr.url), ci = V.ciLabel(pr);
      var title = link ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer", text: pr.title || pr.url }) : h("span", { text: pr.title || "Pull request" });
      return devin([h("div", { className: "eyebrow", text: "Pull request" + (pr.stage ? " for stage " + pr.stage : "") + (pr.simulated ? " (simulated)" : "") }),
        h("div", { className: "prtitle" }, [title]), h("div", { className: "chips" }, [chip(pr.state, ""), chip(ci.text, ci.cls), chip(V.reviewLabel(pr), "")])], "pr");
    }

    // ---------- checkpoint card ----------
    function normalized(view) {
      var L = root.Learn || (isNode ? require("./learn.js") : null);
      if (!view || !L) return {};
      return L.normalizeStages([view])[0].checkpoints;
    }

    function answerKind(c) {
      var L = root.Learn;
      if (!L || !Array.isArray(c.ops)) return "text";
      try { var a = L.predictAnswer(c); return typeof a === "number" ? "number" : typeof a === "boolean" ? "bool" : "text"; } catch (e) { return "text"; }
    }
    function guessValue(kind, raw) {
      var g = String(raw).trim();
      if (kind === "number") return /^[-−]?\d+$/.test(g) ? parseInt(g.replace("−", "-"), 10) : null;
      if (kind === "bool") return /^(true|yes|y)$/i.test(g) ? true : /^(false|no|n)$/i.test(g) ? false : null;
      return g === "" ? null : g;
    }
    function draft(b, kind, field, init) { var k = b.n + "/" + kind + "/" + field; return k in ui.drafts ? ui.drafts[k] : (init || ""); }
    function field(b, kind, name, el) { on(el, "input", function () { ui.drafts[b.n + "/" + kind + "/" + name] = el.value; }); return el; }

    function describeOp(op) {
      var what = op.kind === "adjust" ? (op.value > 0 ? "+" : "") + op.value + " " + op.item : op.kind + " " + op.item + " " + JSON.stringify(op.value);
      return "phone " + op.replica + ", time " + op.lamport + ": " + what;
    }

    function submitKind(b, kind, body, cp) {
      var chat = ctl.active(); if (!chat) return;
      ui.busy[b.n] = true; ui.results[b.n + "/" + kind] = { cls: "", text: "Checking…" }; render();
      ctl.grade(chat.id, b.n, kind, body).then(function (res) { ui.results[b.n + "/" + kind] = V.outcome(kind, res, cp); }, function (err) {
        var text = err.status === 403 ? "Code is graded on the base laptop only. Ask the host to run it there."
          : /locked/.test(err.message) ? "Locked. Earn the earlier stage first."
          : err.status ? "The server could not grade this (HTTP " + err.status + "). Try again later." : "Needs a signal: grading runs on the server. Your answer is kept here.";
        ui.results[b.n + "/" + kind] = { cls: "warn", text: text };
      }).then(function () { ui.busy[b.n] = false; render(); });
    }

    function kindBody(b, kind, cp) {
      var inputs = {}, kids = [], result = b.results[kind], prof = P.levelProfile(b.level);
      if (kind === "predict") {
        kids.push(h("p", { text: cp.prompt || "Predict what the code gives for each case." }));
        var caseEls = [], total = (cp.cases || []).length;
        (cp.cases || []).forEach(function (c, ci) {
          var ak = answerKind(c), input = field(b, kind, c.id, h("input", { type: ak === "number" ? "number" : "text", className: "field", "data-input": "predict:" + c.id,
            "aria-label": "Your prediction for " + c.id, placeholder: ak === "number" ? "a whole number" : ak === "bool" ? "yes or no" : "your answer", value: draft(b, kind, c.id) }));
          inputs[c.id] = { el: input, kind: ak };
          var caseEl = h("div", { className: "case", "data-case": c.id }, [prof.oneCaseAtATime ? h("div", { className: "eyebrow", text: "Case " + (ci + 1) + " of " + total }) : null,
            h("p", { text: c.prompt || "" }), c.question ? h("p", { text: c.question }) : null,
            Array.isArray(c.ops) ? h("ol", { className: "ops" }, c.ops.map(function (op) { return h("li", { text: describeOp(op) }); })) : null, input]);
          caseEls.push(caseEl); kids.push(caseEl);
        });
        // smaller steps: one case at a time, the next appears once the one before has an answer
        function syncCases() {
          if (!prof.oneCaseAtATime) return;
          (cp.cases || []).forEach(function (c, i) { caseEls[i].hidden = i > 0 && !inputs[cp.cases[i - 1].id].el.value.trim(); });
        }
        (cp.cases || []).forEach(function (c) { on(inputs[c.id].el, "input", syncCases); });
        syncCases();
        kids.push(on(h("button", { type: "button", className: "wide", "data-action": "predict", text: "Check my prediction" }), "click", function () {
          var guesses = {}, bad = null;
          (cp.cases || []).forEach(function (c) { var g = guessValue(inputs[c.id].kind, inputs[c.id].el.value); if (g === null) bad = bad || c; else guesses[c.id] = g; });
          if (bad) { ui.results[b.n + "/predict"] = { cls: "warn", text: "Answer every case (" + bad.id + " is empty or not the right kind of answer)." }; render(); return; }
          submitKind(b, "predict", { answer: guesses }, cp);
        }));
      } else if (kind === "bug") {
        kids.push(h("p", { text: cp.prompt || "One line of this code is wrong. What is it?" }), h("pre", { className: "code", text: cp.snippet || cp.mutant_source || "" }));
        var opts = cp.options || cp.choices;
        if (Array.isArray(opts) && opts.length) {
          kids.push(h("div", { className: "choices" }, opts.map(function (c) {
            var id = typeof c === "string" ? c : c.id;
            return on(h("button", { type: "button", "data-choice": id, text: typeof c === "string" ? c : (c.text || c.id) }), "click", function () { submitKind(b, "bug", { answer: id }, cp); });
          })));
        } else {
          var bi = field(b, kind, "text", h("input", { type: "text", className: "field", "data-input": "bug", "aria-label": "What is wrong", placeholder: "What is wrong?", value: draft(b, kind, "text") }));
          kids.push(h("div", { className: "row" }, [bi, on(h("button", { type: "button", "data-action": "bug", text: "Check" }), "click", function () {
            if (bi.value.trim()) submitKind(b, "bug", { answer: bi.value.trim() }, cp); })]));
        }
      } else if (kind === "write") {
        var steps = prof.steps ? P.stepsFromStub(cp.stub || cp.starter || "") : [];
        kids.push(h("p", { text: cp.prompt || "Complete the function. Plain code runs it and grades it." }));
        if (steps.length) kids.push(h("p", { className: "sub", text: "Smaller steps, taken from the task itself:" }), h("ol", { className: "steps", "data-steps": "write" }, steps.map(function (t) { return h("li", { text: t }); })));
        if (prof.writeMode === "guided" && cp.guided && Array.isArray(cp.guided.choices) && cp.guided.choices.length) {
          var gkey = b.n + "/write/pick", picked = ui.drafts[gkey] || "";
          kids.push(h("p", { className: "sub", text: "Guided (difficulty 1): " + (cp.guided.prompt || "pick the body that fits.") }));
          kids.push(h("div", { className: "choices guided", "data-guided": "write" }, cp.guided.choices.map(function (g) {
            var radio = h("input", { type: "radio", name: "g" + b.n, value: g.id, "aria-label": "Option " + g.id });
            if (picked === g.id) radio.checked = true;
            on(radio, "change", function () { ui.drafts[gkey] = g.id; });
            return h("label", { className: "gchoice", "data-gchoice": g.id }, [radio, h("pre", { className: "code", text: g.body })]);
          })));
          kids.push(on(h("button", { type: "button", className: "wide", "data-action": "write-guided", text: "Run the chosen one" }), "click", function () {
            var g = cp.guided.choices.filter(function (c) { return c.id === (ui.drafts[gkey] || ""); })[0];
            if (!g) { ui.results[b.n + "/write"] = { cls: "warn", text: "Pick one of the options first." }; render(); return; }
            submitKind(b, "write", { source: P.assembleChoice(cp.stub || cp.starter || "", g.body) }, cp);
          }));
        } else {
          var init = cp.stub || cp.starter || "";
          if (prof.writeMode === "guided") { init = P.guidedSkeleton(init, steps.length ? steps : P.stepsFromStub(init)); kids.push(h("p", { className: "sub", text: "Guided fill-in (difficulty 1): write the code under each step." })); }
          var ta = field(b, kind, "src", h("textarea", { className: "field codein", rows: "8", spellcheck: "false", autocapitalize: "off", wrap: "off", "data-input": "write",
            "aria-label": "Your code", value: draft(b, kind, "src", init) }));
          kids.push(ta, on(h("button", { type: "button", className: "wide", "data-action": "write", text: "Run it" }), "click", function () {
            if (ta.value.trim()) submitKind(b, "write", { source: ta.value }, cp); }));
        }
      } else {
        var ex = field(b, kind, "text", h("textarea", { className: "field", rows: "4", "data-input": "explain", "aria-label": "Your explanation", value: draft(b, kind, "text") }));
        kids.push(h("p", { text: cp.prompt || "Explain in your own words why it works." }));
        if (prof.starters) kids.push(h("div", { className: "starters", "data-starters": "explain" }, P.STARTERS.map(function (t) {
          return on(h("button", { type: "button", text: t }), "click", function () { ex.value = (ex.value ? ex.value + " " : "") + t; ui.drafts[b.n + "/explain/text"] = ex.value; });
        })));
        kids.push(ex,
          on(h("button", { type: "button", className: "wide", "data-action": "explain", text: "Submit and see the points" }), "click", function () {
            if (ex.value.trim()) submitKind(b, "explain", { text: ex.value }, cp); }));
      }
      if (prof.hint) kids.push(h("p", { className: "hint", "data-hint": kind, text: "Hint: " + (cp.hint || HINTS[kind]) }));
      if (result) {
        kids.push(h("p", { className: "result " + result.cls, "data-result": kind, role: "status", text: result.text }));
        if (result.points && result.points.length) kids.push(h("ul", { className: "rubric" }, result.points.map(function (t) { return h("li", { text: t }); })));
      }
      return kids;
    }

    function checkpointBubble(b) {
      var cps = normalized(b.view), kids = [];
      var earned = b.passed.predict && b.passed.bug && b.passed.write;
      kids.push(h("div", { className: "eyebrow", text: "Tutor checkpoint · stage " + b.n }),
        h("p", { className: "sub", text: earned ? "Earned. Explain is optional practice." : b.canWork ? "Pass predict, find the bug and write to unlock this stage's code. Devin stays stopped until you do."
                                                     : "Locked until the stage before it is earned." }));
      V.KINDS.forEach(function (kind) {
        var cp = cps[kind]; if (!cp) return;
        var done = b.passed[kind], isCur = b.current === kind && b.canWork;
        var order = V.KINDS.indexOf(kind), prevDone = order === 0 || V.KINDS.slice(0, order).every(function (k) { return k === "explain" || b.passed[k] || !cps[k]; });
        var open = isCur || b.results[kind];
        var sum = h("summary", {}, [h("span", { className: "tick " + (done ? "done" : ""), "aria-hidden": "true", text: done ? "✓" : String(order + 1) }), h("span", { text: LABELS[kind] })]);
        var det = h("details", { className: "kind" + (done ? " done" : "") + (!b.canWork || !prevDone ? " locked" : ""), "data-checkpoint": kind }, [sum]);
        if (open) det.setAttribute("open", "");
        if (b.canWork && (prevDone || done)) kindBody(b, kind, cp).forEach(function (n) { det.appendChild(n); });
        else det.appendChild(h("p", { className: "sub", text: "Locked. Earn the previous checkpoint first." }));
        kids.push(det);
      });
      var prof = P.levelProfile(b.level);
      if (prof.level < 3 && !earned) {
        kids.splice(1, 0, h("div", { className: "levelnote", "data-level": prof.level }, [chip("Difficulty " + prof.level + ": " + prof.label, prof.level === 1 ? "bad" : "warn"),
          prof.breathing ? h("span", { className: "breath", text: "Breathe in for 4, out for 6. Nothing here is timed and nothing is skipped." }) : null]));
      }
      return tutor(kids, earned ? "earned" : "");
    }

    function buildBlock(b) {
      if (b.type === "user") return h("div", { className: "msg user" }, [h("div", { className: "bubble", text: b.text })]);
      if (b.type === "plan") return planBubble(b);
      if (b.type === "log") return logBubble(b);
      if (b.type === "stage") return stageBubble(b);
      if (b.type === "checkpoint") return checkpointBubble(b);
      if (b.type === "pr") return prBubble(b);
      if (b.type === "unlock") return tutor([h("strong", { text: b.text })], "earned slim");
      if (b.type === "notice") return h("div", { className: "sysnote " + b.cls, role: "status", text: b.text });
      return h("div", { className: "sysnote bridge", text: b.text });
    }

    // ---------- reconcile the thread so unchanged bubbles (and half-typed answers) are left alone ----------
    function renderThread(chat) {
      var box = $("thread"), list = chat ? V.blocks(chat, ui) : [], next = {}, i;
      list.forEach(function (b) {
        var hit = ui.cache[b.key];
        next[b.key] = hit && hit.sig === b.sig ? hit : { sig: b.sig, node: buildBlock(b) };
      });
      ui.cache = next;
      for (i = 0; i < list.length; i++) {
        var node = next[list[i].key].node;
        if (box.children[i] !== node) box.insertBefore(node, box.children[i] || null);
      }
      while (box.children.length > list.length) box.removeChild(box.children[box.children.length - 1]);
      if (ui.stick) box.scrollTop = box.scrollHeight;
    }

    // ---------- chrome ----------
    function renderSide() {
      var listEl = $("chatlist"), active = ctl.active();
      listEl.replaceChildren.apply(listEl, ctl.chats().map(function (c) {
        var r = V.sidebarRow(c);
        var row = h("div", { className: "chatrow" + (active && active.id === c.id ? " current" : "") }, [
          on(h("button", { type: "button", className: "pick", "data-chat": c.id, "aria-current": active && active.id === c.id ? "true" : null }, [
            h("span", { className: "t", text: r.title }), h("span", { className: "m" }, [chip(r.badge, r.badge === "LIVE" ? "live" : "sim"), h("span", { text: r.progress })])]), "click",
            function () { ui.sideOpen = false; ui.stick = true; ctl.select(c.id); }),
          on(h("button", { type: "button", className: "del", "aria-label": "Delete chat " + r.title, text: "×" }), "click", function () { ctl.remove(c.id); })]);
        return row;
      }));
      if (!ctl.chats().length) listEl.appendChild(h("p", { className: "sub empty", text: "No chats yet. Past chats are kept on this device." }));
      $("offline").checked = ctl.isOffline();
      $("app").className = "shell" + (ui.sideOpen ? " side-open" : "");
    }

    function renderTop(chat) {
      var top = $("topbar"), kids = [on(h("button", { type: "button", className: "burger", "aria-label": "Chats", text: "☰" }), "click", function () { ui.sideOpen = !ui.sideOpen; renderSide(); })];
      if (!chat) { kids.push(h("div", { className: "ttl", text: "New chat" })); top.replaceChildren.apply(top, kids); $("banner").className = "banner hidden"; return; }
      var b = V.badge(chat), a = V.acu(chat), s = chat.session, link = s && V.safeLink(s.url), gate = K.gateState(chat);
      kids.push(h("div", { className: "ttl", text: chat.title }), h("span", { className: "badge " + b.cls, "data-source": chat.source, title: b.why, text: b.text }));
      if (a) kids.push(h("div", { className: "acu", "data-acu": "1", title: "Compute units used by this session against its cap" }, [h("span", { text: a.text }),
        h("span", { className: "bar" }, [h("span", { className: "fill", style: "width:" + a.pct + "%" })])]));
      if (link) kids.push(h("a", { className: "navlink", href: link, target: "_blank", rel: "noopener noreferrer", text: "Open in Devin" }));
      if (s && !chat.stopped && ["working", "waiting", "starting"].indexOf(s.phase) >= 0) {
        kids.push(on(h("button", { type: "button", className: "stop", "data-action": "stop", text: "Stop" }), "click", function () { ctl.stop(chat.id); }));
      }
      top.replaceChildren.apply(top, kids);
      var ban = $("banner");
      if (chat.source === "replay") { ban.className = "banner sim"; ban.textContent = b.why; }
      else if (gate.kind === "limit" || gate.kind === "error") {
        ban.className = "banner warn"; ban.textContent = gate.text + " ";
        ban.appendChild(on(h("button", { type: "button", "data-action": "fallback", text: "Switch to a labelled replay" }), "click", function () { ctl.fallbackNow(chat.id, gate.kind === "limit" ? "quota" : "api_error"); }));
      } else ban.className = "banner hidden";
    }

    function renderLive(chat) {
      var bar = $("livebar"), v = P.statusView(chat);
      if (!v) { bar.className = "livebar hidden"; bar.replaceChildren(); return; }
      bar.className = "livebar " + (v.live ? "live" : "sim");
      var kids = [h("span", { className: "chip " + v.cls, "data-live": "phase", text: v.text }), h("span", { className: "chip", "data-live": "detail", title: "status_detail from the Devin session", text: v.detail || "no status yet" })];
      if (v.acu) kids.push(h("span", { className: "chip", "data-live": "acu", text: (v.live ? "ACU " : "Simulated ACU ") + v.acu.used + " of " + v.acu.cap }));
      kids.push(h("span", { className: "chip", "data-live": "prs", text: v.prs.total ? v.prs.total + " PR" + (v.prs.total === 1 ? "" : "s") + " · CI " + v.prs.passing + " passing, " + v.prs.failing + " failing, " + v.prs.pending + " running" + (v.prs.reviewComments ? " · Devin Review " + v.prs.reviewComments : "") : "no pull requests yet" }));
      if (v.slow) kids.push(h("span", { className: "chip warn", "data-live": "pace", text: "Slow pace: Devin is held back at difficulty 1" }));
      if (v.link) kids.push(h("a", { className: "navlink", href: v.link, target: "_blank", rel: "noopener noreferrer", text: "Session link" }));
      var line = h("div", { className: "livemsg", text: v.last ? "Devin: " + v.last : v.gate.text });
      bar.replaceChildren.apply(bar, [h("div", { className: "liverow" }, kids), line]);
    }

    function renderGate(chat) {
      var g = $("gate");
      if (!chat) { g.className = "gate hidden"; return; }
      var s = K.gateState(chat);
      g.className = "gate " + s.kind; g.textContent = s.text;
      g.setAttribute("data-gate", s.kind);
    }

    function renderWelcome(chat) {
      var w = $("welcome");
      w.className = chat ? "welcome hidden" : "welcome";
      if (chat || w.children.length) return;
      w.appendChild(h("img", { className: "mark", src: "/static/icons/logo.svg", alt: "", width: "56", height: "56" }));
      w.appendChild(h("h1", { text: "Ask Devin to build something small." }));
      w.appendChild(h("p", { className: "lede", text: "Devin builds it stage by stage and stops after each one. You earn every stage with a prediction, a bug hunt and a bit of code, and only then do you see the code and does Devin go on." }));
      var chips = h("div", { className: "ideas" }, IDEAS.map(function (t) {
        return on(h("button", { type: "button", className: "idea", text: "Build " + t }), "click", function () { send("Build " + t); });
      }));
      w.appendChild(chips);
      w.appendChild(on(h("button", { type: "button", className: "demo", "data-action": "demo", text: "Try the built-in demo course (simulated Devin, instant)" }), "click", function () { send("Show me the built-in demo course", { demo: true }); }));
      w.appendChild(h("p", { className: "sub", text: "A live build needs this server to hold a Devin token. If it does not, or the usage limit is hit, you get a replay that says SIMULATED everywhere." }));
    }

    function renderComposer(chat) {
      var box = $("prompt"), running = chat && chat.session && !chat.stopped && V && ["working", "waiting", "starting"].indexOf(chat.session.phase) >= 0;
      box.placeholder = running ? "Message Devin…" : chat ? "This build is over. Type to start a new chat." : "Ask Devin to build something small…";
    }

    function render() {
      var chat = ctl.active();
      renderSide(); renderTop(chat); renderLive(chat); renderWelcome(chat); renderThread(chat); renderGate(chat); renderComposer(chat);
    }

    function send(text, opts) {
      var box = $("prompt");
      text = String(text === undefined ? box.value : text).trim();
      if (!text) return;
      box.value = "";
      ui.sideOpen = false; ui.stick = true;
      return ctl.submit(text, opts);
    }

    // ---------- wiring ----------
    on($("thread"), "scroll", function () { var t = $("thread"); ui.stick = t.scrollHeight - t.scrollTop - t.clientHeight < 120; });
    on($("send"), "click", function () { send(); });
    on($("prompt"), "keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    on($("newchat"), "click", function () { ui.sideOpen = false; ctl.newChat(); var p = $("prompt"); if (p.focus) p.focus(); });
    on($("offline"), "change", function () { ctl.setOffline($("offline").checked); });
    on($("scrim"), "click", function () { ui.sideOpen = false; renderSide(); });
    var links = $("socials");
    LINKS.forEach(function (l) { links.appendChild(h("a", { href: l[1], target: "_blank", rel: "noopener noreferrer", text: l[0] })); });
    return { render: render, ui: ui, send: send };
  }

  var api = { mountChat: mountChat };
  if (isNode) { module.exports = api; return; }
  root.Chat = api;
  var d = root.document;
  if (!d || !d.getElementById("app") || !d.getElementById("thread")) return;

  var store;
  try { store = root.localStorage; store.getItem("sc_chats"); } catch (e) {
    var mem = {}; store = { getItem: function (k) { return k in mem ? mem[k] : null; }, setItem: function (k, v) { mem[k] = String(v); } };
  }
  Ctl.installEarnHooks(root);
  var view;
  var ctl = Ctl.createController({
    storage: store, fetch: root.fetch.bind(root), now: Date.now, schedule: function (fn, ms) { return root.setTimeout(fn, ms); }, cancel: function (h) { root.clearTimeout(h); },
    hooks: Ctl.lateHooks(root), liveBridge: P.create({ fetch: root.fetch.bind(root) }),
    newId: function () { return "c" + Date.now().toString(36) + Math.floor(Math.random() * 1296).toString(36); },
    onChange: function () { if (view) view.render(); },
  });
  view = mountChat({ doc: d, ctl: ctl, host: root });
  view.render();
  ctl.resume();
  root.chatController = ctl;
  if ("serviceWorker" in root.navigator) root.navigator.serviceWorker.register("/sw.js").catch(function () {});
})(typeof self !== "undefined" ? self : this);
