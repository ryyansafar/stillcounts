// The stop-until-earned state machine, as pure functions. Nothing here touches the DOM, the network or a clock.
const test = require("node:test");
const assert = require("node:assert");
const K = require("../chat_core.js");

const PLAN = [
  { n: 1, title: "Skeleton", summary: "s1" },
  { n: 2, title: "Add items", summary: "s2" },
  { n: 3, title: "Totals", summary: "s3" },
];
const view = (n, extra) => Object.assign({ n, id: "st" + n, title: PLAN[n - 1].title, status: "built", files: ["app.py"], concepts: ["loops"],
  bug_hint: "off by one", predict: { prompt: "p" }, bug: { prompt: "b" }, write: { prompt: "w" }, explain: { prompt: "e" },
  progress: { predict: false, bug: false, write: false, explain: false }, unlocked: false, code: null }, extra || {});
const snap = (detail, stages, extra) => Object.assign({ status: detail === "finished" ? "exit" : "running", status_detail: detail, acu_consumed: 0.5,
  acu_limit: 3, url: "https://app.devin.ai/sessions/abc", plan: PLAN, messages: [], pull_requests: [], stages }, extra || {});
const fresh = () => K.reduce(K.createChat({ id: "c1", prompt: "build a todo cli", now: 1, source: "live" }),
  { type: "session", id: "abc", url: "https://app.devin.ai/sessions/abc", live: true, acuLimit: 3, now: 2 }).chat;
const step = (chat, ev) => K.reduce(chat, ev);
const pass = (chat, n, kinds, code) => kinds.reduce((c, k, i) => step(c, { type: "checkpoint", n, kind: k, passed: true,
  progress: { predict: true, bug: kinds.length > 1, write: kinds.length > 2, explain: false }, code: i === kinds.length - 1 ? code : null, now: 9 }).chat, chat);

test("devin phases are normalised from status and status_detail", () => {
  assert.strictEqual(K.devinPhase("running", "working"), "working");
  assert.strictEqual(K.devinPhase("running", "waiting_for_user"), "waiting");
  assert.strictEqual(K.devinPhase("exit", "finished"), "finished");
  assert.strictEqual(K.devinPhase("suspended", "user_request"), "suspended");
  assert.strictEqual(K.devinPhase("suspended", "usage_limit_exceeded"), "limit");
  assert.strictEqual(K.devinPhase("running", "out_of_credits"), "limit");
  assert.strictEqual(K.devinPhase("error", "error"), "error");
  assert.strictEqual(K.devinPhase("new", null), "starting");
  assert.strictEqual(K.devinPhase("claimed", null), "starting");
});

test("a plan creates the stages and a plan message, and Devin builds stage 1 first", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("working", []), now: 3 }).chat;
  assert.strictEqual(c.plan.length, 3);
  assert.strictEqual(c.stages.length, 3);
  assert.ok(c.thread.some((t) => t.kind === "plan"));
  assert.strictEqual(K.stageBuild(c, 1), "building");
  assert.strictEqual(K.stageBuild(c, 2), "queued");
  assert.strictEqual(K.session(c).acu, 0.5);
  assert.strictEqual(K.session(c).acuLimit, 3);
});

test("a built stage shows Devin's summary and a tutor checkpoint, and the code stays null", () => {
  const r = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1, { code: "SECRET = 1" })]), now: 3 });
  const c = r.chat;
  assert.strictEqual(K.stageBuild(c, 1), "built");
  assert.deepStrictEqual(c.thread.filter((t) => t.n === 1).map((t) => t.kind).sort(), ["checkpoint", "stage"]);
  assert.strictEqual(c.stages[0].code, null, "code of an unearned stage is never kept");
  assert.ok(!JSON.stringify(c).includes("SECRET"), "locked code must not appear anywhere in the saved chat");
});

test("no lookahead: Devin may build stage n only once stage n-1 is earned", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("working", []), now: 3 }).chat;
  assert.strictEqual(K.LOOKAHEAD, 0);
  assert.strictEqual(K.devinMayBuild(c, 1), true);
  assert.strictEqual(K.devinMayBuild(c, 2), false);
  assert.strictEqual(K.devinMayBuild(c, 2, 1), true, "an explicit larger lookahead is honoured");
});

test("Devin is not sent a continue while the built stage is unearned: it builds one stage, stops and waits", () => {
  let r = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 });
  assert.deepStrictEqual(r.effects, []);
  assert.strictEqual(K.gateState(r.chat).kind, "waiting_for_learner");
});

const earn1 = (c) => step(c, { type: "checkpoint", n: 1, kind: "write", passed: true, progress: { predict: true, bug: true, write: true, explain: false }, code: "a = 1", now: 2 }).chat;

test("continue is sent once, not on every poll", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat;
  let r = step(earn1(c), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 4 });
  assert.deepStrictEqual(r.effects.map((e) => e.n), [2]);
  r = step(r.chat, { type: "continue_sent", n: 2, now: 5 });
  r = step(r.chat, { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 6 });
  assert.deepStrictEqual(r.effects, []);
  r = step(r.chat, { type: "continue_failed", n: 2, now: 7 });
  r = step(r.chat, { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 8 });
  assert.strictEqual(r.effects.length, 1, "a failed send is retried");
});

test("earning stage 1 unlocks its code with a diff and only then lets Devin go on to stage 2", () => {
  const c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat;
  const r = step(c, { type: "checkpoint", n: 1, kind: "write", passed: true, progress: { predict: true, bug: true, write: true, explain: false }, code: "a = 1", now: 8 });
  assert.strictEqual(K.isEarned(r.chat.stages[0]), true);
  assert.strictEqual(r.chat.stages[0].code, "a = 1");
  assert.ok(r.chat.thread.find((t) => t.kind === "unlock" && t.n === 1));
  assert.deepStrictEqual(r.effects.map((e) => e.n), [2], "stage 1 is earned, so stage 2 may start now");
});

test("a failed checkpoint changes nothing and nothing proceeds", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat;
  c = step(c, { type: "continue_sent", n: 2, now: 3 }).chat;
  const r = step(c, { type: "checkpoint", n: 1, kind: "predict", passed: false, progress: { predict: false, bug: false, write: false, explain: false }, code: null, now: 4 });
  assert.strictEqual(K.isEarned(r.chat.stages[0]), false);
  assert.strictEqual(r.chat.stages[0].code, null);
  assert.deepStrictEqual(r.effects, []);
  assert.ok(!r.chat.thread.some((t) => t.kind === "unlock"));
});

test("explain is practice only: the code unlocks after predict, find the bug and write", () => {
  const c = pass(step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat, 1, ["predict", "bug", "write"], "x = 1");
  assert.strictEqual(K.isEarned(c.stages[0]), true);
});

test("a stage cannot be worked on before the earlier one is earned or before Devin built it", () => {
  const c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1), view(2)]), now: 3 }).chat;
  assert.strictEqual(K.canWork(c, 1), true);
  assert.strictEqual(K.canWork(c, 2), false);
  assert.strictEqual(K.canWork(c, 3), false, "not built yet");
});

test("gate state tells the learner who is waiting for whom", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("working", []), now: 3 }).chat;
  assert.strictEqual(K.gateState(c).kind, "waiting_for_devin");
  c = step(c, { type: "snapshot", snap: snap("working", [view(1)]), now: 4 }).chat;
  assert.strictEqual(K.gateState(c).kind, "pipelining", "Devin builds stage 2 while the learner works stage 1");
  c = step(c, { type: "snapshot", snap: snap("working", [view(1)]), now: 5 }).chat;
  c = pass(c, 1, ["predict", "bug", "write"], "x");
  assert.strictEqual(K.gateState(c).kind, "waiting_for_devin", "the learner is faster: honest waiting state");
  c = step(c, { type: "snapshot", snap: snap("finished", [view(1, { progress: { predict: true, bug: true, write: true, explain: false }, unlocked: true, code: "x" }), view(2), view(3)]), now: 6 }).chat;
  assert.strictEqual(K.gateState(c).kind, "finished");
});

test("gate state: limit, stopped and complete", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("usage_limit_exceeded", [], { status: "suspended" }), now: 3 }).chat;
  assert.strictEqual(K.gateState(c).kind, "limit");
  c = step(fresh(), { type: "stop", now: 3 }).chat;
  assert.strictEqual(K.gateState(c).kind, "stopped");
});

test("stopping the session sends the stop effect and the chat never asks Devin to continue afterwards", () => {
  const r = step(fresh(), { type: "stop", now: 3 });
  assert.deepStrictEqual(r.effects.map((e) => e.type), ["stop"]);
  const r2 = step(r.chat, { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 4 });
  assert.deepStrictEqual(r2.effects, []);
});

test("log messages from the session stream are appended once", () => {
  const msgs = [{ id: "m1", source: "devin", text: "reading repo" }, { id: "m2", source: "devin", text: "writing app.py" }];
  let c = step(fresh(), { type: "snapshot", snap: snap("working", [], { messages: msgs }), now: 3 }).chat;
  c = step(c, { type: "snapshot", snap: snap("working", [], { messages: msgs.concat([{ id: "m3", source: "devin", text: "tests" }]) }), now: 4 }).chat;
  assert.deepStrictEqual(c.thread.filter((t) => t.kind === "log").map((t) => t.text), ["reading repo", "writing app.py", "tests"]);
});

test("pull requests become cards that update in place with CI and review state", () => {
  const pr = { url: "https://github.com/o/r/pull/7", title: "stage 1", state: "open", ci: "pending", stage: 1, review: { state: "none", comments: 0 } };
  let c = step(fresh(), { type: "snapshot", snap: snap("working", [], { pull_requests: [pr] }), now: 3 }).chat;
  c = step(c, { type: "snapshot", snap: snap("working", [], { pull_requests: [Object.assign({}, pr, { ci: "passing", review: { state: "commented", comments: 2 } })] }), now: 4 }).chat;
  assert.strictEqual(c.thread.filter((t) => t.kind === "pr").length, 1);
  assert.strictEqual(c.prs[pr.url].ci, "passing");
  assert.strictEqual(c.prs[pr.url].review.comments, 2);
});

test("a stressed learner makes the bridge message Devin; a recovered learner relaxes it", () => {
  let r = step(fresh(), { type: "adapt", level: 1, previous: 2, now: 3 });
  assert.strictEqual(r.chat.difficulty, 1);
  assert.deepStrictEqual(r.effects.map((e) => [e.type, e.reason]), [["send", "adapt"]]);
  assert.match(r.effects[0].text, /stressed/);
  assert.match(r.effects[0].text, /smaller/);
  assert.match(r.effects[0].text, /comments/);
  assert.ok(r.chat.thread.some((t) => t.kind === "bridge" && /stress/i.test(t.text)));
  r = step(r.chat, { type: "adapt", level: 2, previous: 1, now: 4 });
  assert.match(r.effects[0].text, /normal/i);
  r = step(r.chat, { type: "adapt", level: 2, previous: 2, now: 5 });
  assert.deepStrictEqual(r.effects, [], "no change, no message");
});

test("continue text carries the difficulty so Devin builds a smaller stage for a stressed learner", () => {
  let c = step(fresh(), { type: "adapt", level: 1, previous: 2, now: 3 }).chat;
  c = earn1(step(c, { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat);
  const r = step(c, { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 4 });
  assert.match(r.effects[0].text, /small/i);
});

test("a fallback is announced in the thread and switches to the labelled replay, never silently", () => {
  const r = step(fresh(), { type: "fallback", reason: "quota", text: "Devin usage limit reached", now: 5 });
  assert.strictEqual(r.chat.source, "replay");
  assert.strictEqual(r.chat.course, "builtin");
  assert.strictEqual(r.chat.replayWhy, "quota");
  const note = r.chat.thread.find((t) => t.kind === "notice");
  assert.match(note.text, /replay/i);
  assert.match(note.text, /usage limit/i);
  assert.deepStrictEqual(r.effects.map((e) => e.type), ["start_replay"]);
  assert.strictEqual(K.session(r.chat), null);
});

test("fallbackReason classifies bridge failures", () => {
  const e = (status, code) => Object.assign(new Error("x"), { status, code });
  assert.strictEqual(K.fallbackReason(e(402)).reason, "quota");
  assert.strictEqual(K.fallbackReason(e(429)).reason, "quota");
  assert.strictEqual(K.fallbackReason(e(503, "no_token")).reason, "no_token");
  assert.strictEqual(K.fallbackReason(e(404)).reason, "no_bridge");
  assert.strictEqual(K.fallbackReason(e(500)).reason, "api_error");
  assert.strictEqual(K.fallbackReason(e(0)).reason, "network");
  assert.strictEqual(K.fallbackReason(new Error("timeout")).reason, "network");
  K.fallbackReason(e(500));
  ["quota", "no_token", "no_bridge", "api_error", "network", "offline_toggle"].forEach((r) => assert.ok(K.REASON_TEXT[r], r));
});

test("diffLines marks added, removed and unchanged lines", () => {
  const d = K.diffLines("a\nb\nc", "a\nB\nc\nd");
  assert.deepStrictEqual(d.map((x) => x.t + ":" + x.text), ["same:a", "del:b", "add:B", "same:c", "add:d"]);
  assert.deepStrictEqual(K.diffLines("", "x\ny").map((x) => x.t), ["add", "add"]);
  assert.deepStrictEqual(K.diffLines("x", "x").map((x) => x.t), ["same"]);
});

test("the unlock diff compares with the code of the previous earned stage", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1), view(2)]), now: 3 }).chat;
  c = pass(c, 1, ["predict", "bug", "write"], "a = 1");
  c = pass(c, 2, ["predict", "bug", "write"], "a = 1\nb = 2");
  const d = K.unlockDiff(c, 2);
  assert.deepStrictEqual(d.map((x) => x.t), ["same", "add"]);
  assert.deepStrictEqual(K.unlockDiff(c, 1).map((x) => x.t), ["add"]);
});

test("grade URLs: built-in demo uses the existing Learn API, a live session uses the Devin bridge", () => {
  const live = fresh();
  assert.strictEqual(K.gradeUrl(live, { n: 2, id: "st2" }, "write"), "/api/devin/sessions/abc/stages/2/write");
  const demo = K.createChat({ id: "c2", prompt: "x", now: 1, source: "replay", course: "builtin" });
  assert.strictEqual(K.gradeUrl(demo, { n: 1, id: "duplicate-ids" }, "bug"), "/api/learn/duplicate-ids/bug");
  assert.match(K.learnerId(demo), /^[A-Za-z0-9_-]{1,64}$/);
});

test("saved chats keep at most 30 chats and 300 thread items, drop unknown junk, and survive bad JSON", () => {
  const many = [];
  for (let i = 0; i < 40; i++) many.push(K.createChat({ id: "c" + i, prompt: "p" + i, now: i, source: "live" }));
  const text = K.serializeChats(many);
  const back = K.parseChats(text);
  assert.strictEqual(back.length, 30);
  assert.deepStrictEqual(K.parseChats("not json"), []);
  assert.deepStrictEqual(K.parseChats('{"a":1}'), []);
  assert.deepStrictEqual(K.parseChats(JSON.stringify({ chats: [{ nope: 1 }, null, 5] })), []);
  assert.deepStrictEqual(K.parseChats(null), []);
});

test("serialising never writes the code of an unearned stage", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat;
  c.stages[0].code = "LEAK";                       // even if something upstream kept it by mistake
  assert.ok(!K.serializeChats([c]).includes("LEAK"));
});

test("titles come from the prompt", () => {
  assert.strictEqual(K.titleFrom("  Build me a tip calculator  "), "Build me a tip calculator");
  assert.ok(K.titleFrom("x".repeat(200)).length <= 48);
  assert.strictEqual(K.titleFrom(""), "New chat");
});

test("reduce never mutates its input", () => {
  const c = fresh();
  const frozen = JSON.stringify(c);
  K.reduce(c, { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 });
  assert.strictEqual(JSON.stringify(c), frozen);
});

test("a snapshot never moves a built stage back and never un-earns one", () => {
  let c = step(fresh(), { type: "snapshot", snap: snap("waiting_for_user", [view(1)]), now: 3 }).chat;
  c = pass(c, 1, ["predict", "bug", "write"], "code");
  c = step(c, { type: "snapshot", snap: snap("working", [view(1, { status: "queued" })]), now: 4 }).chat;
  assert.strictEqual(K.stageBuild(c, 1), "built");
  assert.strictEqual(K.isEarned(c.stages[0]), true);
  assert.strictEqual(c.stages[0].code, "code");
});
