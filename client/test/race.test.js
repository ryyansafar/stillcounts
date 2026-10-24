const test = require("node:test");
const assert = require("node:assert");
const C = require("../crdt.js");
const R = require("../race.js");

const op = (id, replica, lamport, kind, item, value) => ({ id, replica, lamport, kind, item, value });
const OPS = [
  op("r:a:1", "vol-asha", 1, "create", "rice", "Rice (kg)"),
  op("r:a:2", "vol-asha", 2, "adjust", "rice", 10),
  op("r:b:1", "vol-biju", 1, "adjust", "rice", -3),
  op("r:b:2", "vol-biju", 3, "delete", "rice", true),
  op("r:c:1", "vol-chitra", 4, "delete", "rice", false),
  op("r:c:2", "vol-chitra", 1, "create", "water", "Water"),
  op("r:c:3", "vol-chitra", 2, "adjust", "water", 6),
  op("r:a:3", "vol-asha", 5, "delete", "water", true),
];

function shuffle(arr, seed) {
  const a = arr.slice();
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
  for (let j = a.length - 1; j > 0; j--) { const k = Math.floor(rnd() * (j + 1)); [a[j], a[k]] = [a[k], a[j]]; }
  return a;
}

test("answer key comes from the real merge and ignores order and duplicates", () => {
  const key = R.answerKey(OPS);
  assert.deepStrictEqual(key, { rice: 7, water: "removed" });
  for (let s = 1; s < 50; s++) {
    assert.deepStrictEqual(R.answerKey(shuffle(OPS.concat(OPS.slice(0, s % 4)), s)), key);
  }
  const m = C.merge(OPS);
  assert.strictEqual(m.rice.qty, key.rice);
});

test("options always include the right answer, are unique and stable for a phone", () => {
  const seq = shuffle(OPS.concat([OPS[1]]), 9);
  for (const item of ["rice", "water"]) {
    const o = R.options(seq, item, "phone-1");
    const vals = o.map((x) => x.value);
    assert.ok(vals.some((v) => v === R.answerKey(seq)[item]));
    assert.strictEqual(new Set(vals.map(String)).size, vals.length);
    assert.ok(vals.length >= 3 && vals.length <= 4);
    assert.deepStrictEqual(R.options(seq, item, "phone-1"), o);
  }
});

test("a duplicate is the trick when the double-counted total is picked", () => {
  const seq = [OPS[0], OPS[1], OPS[2], OPS[1], OPS[3], OPS[4]];
  const t = R.trickFor(seq, "rice", 17);
  assert.strictEqual(t.op.id, "r:a:2");
  assert.strictEqual(t.index, 3);
  assert.match(t.why, /duplicate/i);
});

test("a late-arriving but older delete is the trick when arrival order is trusted", () => {
  const seq = [OPS[0], OPS[1], OPS[2], OPS[4], OPS[3]];
  assert.strictEqual(R.answerKey(seq).rice, 7);
  const t = R.trickFor(seq, "rice", "removed");
  assert.strictEqual(t.op.id, "r:b:2");
  assert.match(t.why, /arriv|lamport|time/i);
});

test("every wrong option names an op that tricked the player", () => {
  for (let s = 1; s < 40; s++) {
    const seq = shuffle(OPS.concat([OPS[s % OPS.length], OPS[(s * 3) % OPS.length]]), s);
    const key = R.answerKey(seq);
    for (const item of Object.keys(key)) {
      for (const o of R.options(seq, item, "p" + s)) {
        if (o.value === key[item]) { assert.strictEqual(R.trickFor(seq, item, o.value), null); continue; }
        const t = R.trickFor(seq, item, o.value);
        assert.ok(t && t.op && seq[t.index].id === t.op.id, "trick op for " + item + " " + o.value);
      }
    }
  }
});

test("grade marks each item and reports the first trick", () => {
  const seq = [OPS[0], OPS[1], OPS[2], OPS[1], OPS[3], OPS[4], OPS[5], OPS[6], OPS[7]];
  const g = R.grade(seq, { rice: 17, water: "removed" });
  assert.strictEqual(g.correct, false);
  assert.strictEqual(g.items.water.ok, true);
  assert.strictEqual(g.items.rice.ok, false);
  assert.strictEqual(g.first.op.id, "r:a:2");
  assert.strictEqual(R.grade(seq, R.answerKey(seq)).correct, true);
});

test("describe reads like a volunteer's action", () => {
  assert.match(R.describe(OPS[1], { rice: "Rice (kg)" }), /Asha.*\+10.*Rice/);
  assert.match(R.describe(OPS[3], {}), /Biju.*removed/);
  assert.match(R.describe(OPS[4], {}), /Chitra.*restored/);
});
