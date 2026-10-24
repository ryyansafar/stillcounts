// Runs client/app.js against a tiny fake DOM so phone-app regressions found in browser QA stay fixed.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CRDT = fs.readFileSync(path.join(__dirname, "..", "crdt.js"), "utf8");
const APP = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");

class El {
  constructor(doc, tag) {
    this.doc = doc; this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ""; this._text = ""; this.value = ""; this.selectionStart = 0; this.selectionEnd = 0;
  }
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(v) { this.children = []; this._text = String(v); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  replaceChildren(...cs) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children = []; cs.forEach((c) => this.appendChild(c));
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type) { (this.listeners[type] || []).forEach((fn) => fn({ type, target: this })); }
  click() { this.dispatch("click"); }
  focus() { this.doc.activeElement = this; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === this.doc.body; }
}

function makeDocument() {
  const doc = { activeElement: null };
  doc.body = new El(doc, "body");
  doc.createElement = (tag) => new El(doc, tag);
  const byId = {};
  ["status", "who", "list", "me", "newitem", "addbtn", "foot"].forEach((id) => { byId[id] = doc.body.appendChild(new El(doc, "div")); });
  doc.getElementById = (id) => byId[id] || null;
  return doc;
}

function makeStorage() {
  const data = {}, windows = [];
  return {
    windows,
    api(owner) {
      return {
        getItem: (k) => (k in data ? data[k] : null),
        setItem: (k, v) => {
          data[k] = String(v);
          windows.filter((w) => w !== owner.win).forEach((w) => (w.listeners.storage || []).forEach((fn) => fn({ key: k })));
        },
      };
    },
  };
}

// Boots one phone tab. `net.online` toggles whether /api/sync succeeds.
function boot(storage, net) {
  const doc = makeDocument(), ticks = [], owner = {};
  const win = { listeners: {}, addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); } };
  owner.win = win; storage.windows.push(win);
  const ctx = {
    window: win, document: doc, navigator: {}, localStorage: storage.api(owner), console,
    setInterval: (fn) => { ticks.push(fn); return ticks.length; },
    fetch: () => (net.online ? Promise.resolve({ ok: true, json: () => Promise.resolve(net.reply()) }) : Promise.reject(new Error("offline"))),
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(CRDT, ctx);
  win.StillCounts = ctx.StillCounts;
  vm.runInContext(APP, ctx);
  return { doc, win, tick: () => ticks.forEach((fn) => fn()), close: () => storage.windows.splice(storage.windows.indexOf(win), 1) };
}

const settle = () => new Promise((r) => setImmediate(r));
function all(el, pred, out = []) { if (pred(el)) out.push(el); el.children.forEach((c) => all(c, pred, out)); return out; }
const cardNames = (doc) => all(doc.getElementById("list"), (e) => e.className === "name").map((e) => e.textContent);
const button = (doc, label) => all(doc.body, (e) => e.getAttribute("aria-label") === label)[0];
const noteFor = (doc, id) => all(doc.body, (e) => e.getAttribute("data-note") === id)[0];

test("a note being typed survives the background sync re-render", async () => {
  const tab = boot(makeStorage(), { online: false });
  await settle();
  const note = noteFor(tab.doc, "blankets");
  note.focus(); note.value = "half typ"; note.setSelectionRange(8, 8);
  tab.tick(); await settle(); await settle();
  const active = tab.doc.activeElement;
  assert.strictEqual(active.getAttribute("data-note"), "blankets");
  assert.strictEqual(active.value, "half typ");
  assert.strictEqual(active.selectionStart, 8);
});

test("tap buttons are not torn down by a sync tick when nothing changed", async () => {
  const tab = boot(makeStorage(), { online: false });
  await settle();
  const plus = button(tab.doc, "Blankets +1");
  tab.tick(); await settle(); await settle();
  assert.ok(plus.isConnected(), "the +1 button a finger is on was replaced mid-tap");
});

test("distinct non-Latin item names stay distinct items", async () => {
  const tab = boot(makeStorage(), { online: false });
  await settle();
  const input = tab.doc.getElementById("newitem"), add = tab.doc.getElementById("addbtn");
  input.value = "കമ്പിളി"; add.click();
  input.value = "അരി"; add.click();
  const names = cardNames(tab.doc);
  assert.ok(names.includes("കമ്പിളി"), names.join(","));
  assert.ok(names.includes("അരി"), names.join(","));
});

test("two offline tabs of the same phone never erase each other's queued taps", async () => {
  const storage = makeStorage(), net = { online: false };
  const a = boot(storage, net), b = boot(storage, net);
  await settle();
  for (let i = 0; i < 3; i++) button(a.doc, "Blankets +1").click();
  for (let i = 0; i < 2; i++) button(b.doc, "Blankets +1").click();
  await settle();
  a.close();
  const reloaded = boot(storage, net);
  await settle();
  const qty = all(reloaded.doc.getElementById("list"), (e) => e.className.startsWith("qty"))[0].textContent;
  assert.strictEqual(qty, "5");
  assert.match(reloaded.doc.getElementById("status").textContent, /5 queued/);
});

test("names typed in different Unicode forms are one item, and symbol-only names still get distinct ids", async () => {
  const tab = boot(makeStorage(), { online: false });
  await settle();
  const input = tab.doc.getElementById("newitem"), add = tab.doc.getElementById("addbtn");
  input.value = "café"; add.click();            // precomposed
  input.value = "café"; add.click();           // e + combining accent: the same word
  input.value = "!!!"; add.click();
  input.value = "???"; add.click();
  const names = cardNames(tab.doc);
  assert.strictEqual(names.filter((n) => n.normalize("NFC") === "café").length, 1, names.join(","));
  assert.ok(names.includes("!!!") && names.includes("???"), names.join(","));
});

test("when the base says its log changed (reset), the phone offers back every op the base no longer has", async () => {
  const storage = makeStorage();
  const net = { online: true, reply: () => ({ ops: [], rejected: [], cursor: 0, epoch: "e1", reset: false, max_lamport: 0 }) };
  const tab = boot(storage, net);
  await settle();
  for (let i = 0; i < 3; i++) button(tab.doc, "Blankets +1").click();
  await settle();
  tab.tick(); await settle();
  // The base lost everything and says so. Its new log is empty, so all three ops must go out again.
  net.reply = () => ({ ops: [], rejected: [], cursor: 0, epoch: "e2", reset: true, max_lamport: 0 });
  tab.tick(); await settle(); await settle();
  assert.match(tab.doc.getElementById("status").textContent, /3 queued|syncing · 3/);
});

test("an op whose Lamport time the base refuses is re-stamped and sent again, not dropped", async () => {
  const storage = makeStorage();
  const net = { online: false, reply: () => ({}) };
  const tab = boot(storage, net);
  await settle();
  button(tab.doc, "Blankets +1").click();
  await settle();
  net.online = true;
  const id = JSON.parse(storage.api({ win: null }).getItem("sc_state")).ops[0].id;
  net.reply = () => ({ ops: [], rejected: [{ id, reason: "lamport skew too large (more than 100000 ahead of the log)" }],
                       cursor: 0, epoch: "e", reset: false, max_lamport: 40 });
  tab.tick(); await settle(); await settle();
  const rec = JSON.parse(storage.api({ win: null }).getItem("sc_state"));
  assert.strictEqual(rec.ops[0].lamport, 41);
  assert.ok(!rec.synced.includes(id), "the re-stamped op must stay queued");
  assert.strictEqual(rec.lamport, 41);
});
