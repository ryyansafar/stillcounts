const test = require("node:test");
const assert = require("node:assert");
const B = require("../chat_bridge.js");

function fakeFetch(routes) {
  const calls = [];
  const f = async (url, init) => {
    calls.push({ url, init: init || {}, body: init && init.body ? JSON.parse(init.body) : undefined });
    const key = (init && init.method || "GET") + " " + url.split("?")[0];
    const r = routes[key];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    if (r instanceof Error) throw r;
    return { ok: r.status === undefined || r.status < 400, status: r.status || 200, json: async () => r.body };
  };
  f.calls = calls;
  return f;
}

test("the prompt sent to Devin asks for 3 to 4 small stages and a stop after each", () => {
  const p = B.devinPrompt("a tip calculator", 2);
  assert.match(p, /a tip calculator/);
  assert.match(p, /150 lines/);
  assert.match(p, /3 to 4 stages/);
  assert.match(p, /waiting_for_user|wait/i);
  assert.match(p, /continue/i);
  assert.match(B.devinPrompt("x", 1), /smaller|small/i);
});

test("the structured output schema asks for summary, files, concepts and a bug to hunt per stage", () => {
  const s = B.STRUCTURED_OUTPUT_SCHEMA;
  const stage = s.properties.stages.items.properties;
  ["n", "title", "summary", "files", "concepts", "bug_hint"].forEach((k) => assert.ok(stage[k], k));
});

test("start posts the prompt, the schema and an ACU cap, and the browser never sees a token", async () => {
  const f = fakeFetch({ "POST /api/devin/sessions": { body: { session_id: "s1", url: "https://app.devin.ai/sessions/s1", acu_limit: 3 } } });
  const b = B.createLiveBridge({ fetch: f });
  const s = await b.start({ prompt: "tip calculator", difficulty: 2, maxAcu: 3 });
  assert.deepStrictEqual([s.id, s.url, s.acuLimit, s.live], ["s1", "https://app.devin.ai/sessions/s1", 3, true]);
  const c = f.calls[0];
  assert.strictEqual(c.body.user_prompt, "tip calculator");
  assert.strictEqual(c.body.max_acu_limit, 3);
  assert.ok(c.body.structured_output_schema);
  assert.ok(!JSON.stringify(c).toLowerCase().includes("authorization"));
});

test("poll, message and stop hit the bridge routes", async () => {
  const f = fakeFetch({ "GET /api/devin/sessions/s1": { body: { status: "running" } }, "POST /api/devin/sessions/s1/message": { body: {} },
                        "POST /api/devin/sessions/s1/stop": { body: {} } });
  const b = B.createLiveBridge({ fetch: f });
  assert.strictEqual((await b.poll("s1")).status, "running");
  await b.message("s1", "continue", { reason: "continue", n: 2 });
  assert.deepStrictEqual(f.calls[1].body, { text: "continue", reason: "continue", n: 2 });
  await b.stop("s1");
  assert.strictEqual(f.calls[2].url, "/api/devin/sessions/s1/stop");
});

test("bridge errors carry status and code so the controller can name the reason", async () => {
  const b = B.createLiveBridge({ fetch: fakeFetch({ "POST /api/devin/sessions": { status: 503, body: { code: "no_token", detail: "no token" } } }) });
  await assert.rejects(() => b.start({ prompt: "x" }), (e) => e.status === 503 && e.code === "no_token");
  const down = B.createLiveBridge({ fetch: fakeFetch({ "GET /api/devin/sessions/s1": new Error("fetch failed") }) });
  await assert.rejects(() => down.poll("s1"), (e) => !e.status);
  const missing = B.createLiveBridge({ fetch: fakeFetch({}) });
  await assert.rejects(() => missing.start({ prompt: "x" }), (e) => e.status === 404);
});

test("a hung bridge times out instead of freezing the thread", async () => {
  const never = () => new Promise(() => {});
  const b = B.createLiveBridge({ fetch: never, timeoutMs: 20 });
  await assert.rejects(() => b.poll("s1"), /timeout/);
});

test("the course loader asks the existing Learn API and falls back to the saved copy offline", async () => {
  const store = (() => { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = v; } }; })();
  const f = fakeFetch({ "GET /api/learn/stages": { body: { stages: [{ id: "a", title: "A" }, { id: "b", title: "B" }] } } });
  const views = await B.loadCourse({ fetch: f, storage: store, learner: "chat-c1" });
  assert.deepStrictEqual(views.map((v) => [v.n, v.id]), [[1, "a"], [2, "b"]]);
  assert.strictEqual(f.calls[0].init.headers["X-Learner-Id"], undefined, "the page sends no learner id, the server cookie identifies it");
  const offline = await B.loadCourse({ fetch: fakeFetch({ "GET /api/learn/stages": new Error("down") }), storage: store, learner: "chat-c1" });
  assert.strictEqual(offline.length, 2);
  await assert.rejects(() => B.loadCourse({ fetch: fakeFetch({ "GET /api/learn/stages": new Error("down") }), storage: { getItem: () => null, setItem() {} }, learner: "x" }));
});

test("grade posts to the given url without a client-chosen learner id", async () => {
  const f = fakeFetch({ "POST /api/learn/a/bug": { body: { passed: true, progress: { bug: true } } } });
  const r = await B.grade({ fetch: f, url: "/api/learn/a/bug", body: { answer: "b" }, learner: "chat-c1" });
  assert.strictEqual(r.passed, true);
  assert.strictEqual(f.calls[0].init.headers["X-Learner-Id"], undefined, "the page sends no learner id, the server cookie identifies it");
});
