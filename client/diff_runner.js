// Reads a JSON list of operation sets from stdin and prints the merged state and stats for each, using the JavaScript merge.
const C = require("./crdt.js");
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const sets = JSON.parse(input);
  process.stdout.write(JSON.stringify(sets.map((ops) => {
    try { return { state: C.merge(ops), stats: C.stats(ops) }; } catch (e) { return { error: String(e.message) }; }
  })));
});
