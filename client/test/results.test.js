const test = require("node:test");
const assert = require("node:assert");
const Res = require("../results.js");

test("results page text: summary, adaptation line, learner line", () => {
  assert.match(Res.summarize({ summary: { events: 3, lowered: 2, restored: 1 } }), /3 adaptations logged: 2 lowered the difficulty, 1 restored it/);
  assert.match(Res.eventLine({ at: 0, prev: 3, level: 1, stage: 2, kind: "stress", reason: "heart rate up vs baseline" }), /difficulty 3 to 1 \(stage 2\)\s+\[stress\]/);
  assert.match(Res.learnerLine({ learner: "chat-c1", earned: 1, stages: { "duplicate-ids": { predict: true, bug: true, write: true, explain: false }, other: { predict: false, bug: false, write: false, explain: false } } }), /predict\+bug\+write.*other: none/);
});
