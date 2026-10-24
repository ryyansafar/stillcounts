const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../crdt.js");

const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "tests", "vectors.json"), "utf8"));

for (const v of vectors) {
  test("matches the Python reference: " + v.name, () => {
    assert.deepStrictEqual(C.merge(v.ops), v.state);
    assert.deepStrictEqual(C.stats(v.ops), v.stats);
  });
}

test("any order and duplication converges", () => {
  const v = vectors.find((x) => x.name === "seed-168-n240" || x.name.startsWith("seed-163"));
  const ops = v.ops;
  const ref = JSON.stringify(C.merge(ops));
  let seed = 12345;
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
  for (let i = 0; i < 100; i++) {
    const copy = ops.concat(ops.slice(0, Math.floor(rnd() * 10)));
    for (let j = copy.length - 1; j > 0; j--) { const k = Math.floor(rnd() * (j + 1)); [copy[j], copy[k]] = [copy[k], copy[j]]; }
    assert.strictEqual(JSON.stringify(C.merge(copy)), ref);
  }
});

test("invalid ops are rejected", () => {
  assert.throws(() => C.merge([{ id: "a:1", replica: "a", lamport: 1, kind: "adjust", item: "x", value: 0 }]));
  assert.throws(() => C.merge([{ id: "a:1", replica: "a", lamport: 1, kind: "explode", item: "x", value: 1 }]));
});

test("clock ticks and observes", () => {
  const c = new C.Clock("a");
  assert.strictEqual(c.tick(), 1);
  c.observe(10);
  assert.strictEqual(c.tick(), 11);
});
