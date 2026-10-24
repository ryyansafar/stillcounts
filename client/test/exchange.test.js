const test = require("node:test");
const assert = require("node:assert");
const X = require("../exchange.js");
const C = require("../crdt.js");

const op = (id, lamport, extra) => Object.assign({ id, replica: id.split(":")[0], lamport, kind: "adjust", item: "rice", value: 5 }, extra);

test("a bundle round-trips and two phones that swap bundles agree, whatever the order", () => {
  const a = [op("ph-a:1", 1), op("ph-a:2", 2, { value: -3 })], b = [op("ph-b:1", 1, { value: 7 }), op("ph-b:2", 3, { kind: "note", value: "damp" })];
  const fromB = X.decode(X.encode(b)), fromA = X.decode(X.encode(a));
  assert.strictEqual(fromA.skipped + fromB.skipped, 0);
  const onA = C.merge(a.concat(fromB.ops)), onB = C.merge(fromA.ops.concat(b, fromA.ops));   // B also gets A's bundle twice
  assert.strictEqual(JSON.stringify(onA), JSON.stringify(onB));
  assert.strictEqual(onA.rice.qty, 9);
});

test("text that is not a bundle is refused with a reason", () => {
  ["", "hello", "[]", "{\"ops\": []}", "{\"stillcounts\": 1}"].forEach((t) => assert.ok(X.decode(t).error, t));
});

test("invalid operations are skipped one by one, the valid ones are kept", () => {
  const bad = [op("x:1", 0), op("x:2", 1, { value: 0 }), op("x:3", 1, { kind: "explode" }), op("x:4", 1, { value: "5" }),
    op("x:5", 1, { extra: 1 }), op("x:6", 1, { value: 2e9 }), op("x:7", 1.5), null, "str", op("x:8", 1, { kind: "create", value: "" }),
    op("x:9", 1, { value: 1 })];
  const out = X.decode(JSON.stringify({ stillcounts: 1, ops: bad }));
  assert.deepStrictEqual(out.ops.map((o) => o.id), ["x:9"]);
  assert.strictEqual(out.skipped, bad.length - 1);
});

test("an oversized bundle is refused", () => {
  const ops = Array.from({ length: 5001 }, (_, i) => op("p:" + i, 1));
  assert.match(X.decode(X.encode(ops)).error, /more than 5000/);
});
