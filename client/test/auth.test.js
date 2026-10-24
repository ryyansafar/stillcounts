// client/auth.js: the join token is read from ?t=, stored, removed from the address bar, and sent on /api/ requests only.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const AUTH = fs.readFileSync(path.join(__dirname, "..", "auth.js"), "utf8");

function boot(search, stored) {
  const store = stored ? { sc_token: stored } : {}, calls = [], urls = [];
  const win = { fetch: (url, init) => { calls.push({ url, init }); return Promise.resolve({}); } };
  const ctx = {
    window: win, URLSearchParams, Object,
    location: { search, pathname: "/", hash: "" },
    history: { replaceState: (a, b, url) => urls.push(url) },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
  };
  vm.createContext(ctx);
  vm.runInContext(AUTH, ctx);
  return { win, calls, urls, store };
}

test("a token in the URL is stored, removed from the address bar and sent on /api/ calls", async () => {
  const t = boot("?t=abc123");
  assert.strictEqual(t.store.sc_token, "abc123");
  assert.deepStrictEqual(t.urls, ["/"]);
  await t.win.fetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" } });
  assert.strictEqual(t.calls[0].init.headers["X-Join-Token"], "abc123");
  assert.strictEqual(t.calls[0].init.headers["Content-Type"], "application/json");
});

test("a remembered token is used when the URL has none, and only /api/ calls carry it", async () => {
  const t = boot("", "kept");
  await t.win.fetch("/api/state");
  await t.win.fetch("/static/app.js");
  assert.strictEqual(t.calls[0].init.headers["X-Join-Token"], "kept");
  assert.strictEqual(t.calls[1].init, undefined);
});

test("with no token anywhere nothing is added", async () => {
  const t = boot("");
  await t.win.fetch("/api/sync", { method: "POST" });
  assert.deepStrictEqual(t.calls[0].init, { method: "POST" });
});
