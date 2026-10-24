// Contract check run by tests/test_learn_ui_contract.py: feeds learn.js the stage list that the real server returned
// and checks that the browser's own merge agrees with the real grader on every predict case.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const L = require("../learn.js");

// The fixture is the stage list the real server sends plus the real merge's answers. It is committed so a plain
// `node --test client/test/` runs these checks; tests/test_learn_ui_contract.py fails if it is out of date.
const path = require("node:path");
const fixture = JSON.parse(fs.readFileSync(process.env.LEARN_FIXTURE || path.join(__dirname, "fixtures", "learn_contract.json"), "utf8"));

test("the real stage list renders as four checkpoints per stage with the options and cases the UI needs", () => {
  const stages = L.normalizeStages(fixture.api);
  assert.strictEqual(stages.length, 4);
  stages.forEach((s) => {
    assert.deepStrictEqual(s.order, ["predict", "bug", "write", "explain"]);
    assert.strictEqual(s.earned, false);
    assert.strictEqual(s.code, null);
    assert.ok(s.checkpoints.predict.cases.length >= 3);
    assert.ok(s.checkpoints.bug.snippet && s.checkpoints.bug.options.length >= 2);
    assert.ok(s.checkpoints.bug.options.every((o) => o.id && o.text));
    assert.ok(s.checkpoints.write.stub.includes("def "));
    assert.ok(s.checkpoints.explain.prompt);
  });
});

test("the browser's merge gives the same answer as the real grader for every predict case", () => {
  const stages = L.normalizeStages(fixture.api);
  let n = 0;
  stages.forEach((s) => s.checkpoints.predict.cases.forEach((c) => {
    const want = fixture.expected[s.id][c.id];
    assert.deepStrictEqual(L.predictAnswer(c), want, s.id + "/" + c.id);
    assert.strictEqual(L.gradePredict(c, String(typeof want === "boolean" ? (want ? "yes" : "no") : want)), true, s.id + "/" + c.id);
    n += 1;
  }));
  assert.ok(n >= 12);
});
