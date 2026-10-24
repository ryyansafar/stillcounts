"""Builds learn/stages.json. Every expected answer comes from the real merge and every bug from a real mutant.

Run `python -m learn.build_stages` after editing the stage content below.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from learn import guided
from oracle.mutation import MUTANTS, _load
from stillcounts.crdt import Op, merge, stats

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = ROOT / "stillcounts" / "crdt.py"
OUT = Path(__file__).resolve().parent / "stages.json"


def op(id: str, lamport: int, kind: str, item: str, value) -> dict:
    return {"id": id, "replica": id.split(":")[0], "lamport": lamport, "kind": kind, "item": item, "value": value}


def kw(*alternatives: str) -> list[str]:
    return list(alternatives)


STAGES = [
    {
        "id": "duplicate-ids",
        "order": 1,
        "title": "Counted once, even when sent twice",
        "summary": "A phone's outbox retries until the server says yes, so the same operation can arrive "
                   "more than once. Every operation has a unique id, and the merge keeps one copy per id.",
        "predict": [
            {"id": "p1", "prompt": "Phone A received 10 blankets. The signal dropped and the outbox sent it again. "
                                   "Phone B received 5. What is the blanket count?",
             "ops": [op("ph-a:1", 1, "adjust", "blankets", 10), op("ph-a:1", 1, "adjust", "blankets", 10),
                     op("ph-b:1", 1, "adjust", "blankets", 5)],
             "ask": {"item": "blankets", "field": "qty"}},
            {"id": "p2", "prompt": "20 bags of rice arrive, then one issue of 4 bags is retried three times. "
                                   "How much rice is left?",
             "ops": [op("ph-a:1", 1, "adjust", "rice", 20), op("ph-a:2", 2, "adjust", "rice", -4),
                     op("ph-a:2", 2, "adjust", "rice", -4), op("ph-a:2", 2, "adjust", "rice", -4)],
             "ask": {"item": "rice", "field": "qty"}},
            {"id": "p3", "prompt": "Two copies of id ph-a:1 disagree: one says +4 at Lamport 1, the other +7 at "
                                   "Lamport 2. The merge keeps the copy with the larger (lamport, replica, kind, "
                                   "item, value). What is the water count?",
             "ops": [op("ph-a:1", 1, "adjust", "water", 4), op("ph-a:1", 2, "adjust", "water", 7)],
             "ask": {"item": "water", "field": "qty"}},
        ],
        "bug": {
            "mutant": "merge-skips-dedupe",
            "prompt": "This merge was changed by one line. Phones now report too many blankets after a bad "
                      "signal day. What is wrong?",
            "options": [
                {"id": "a", "text": "The quantity sum ignores negative adjustments."},
                {"id": "b", "text": "merge groups the raw operations without calling dedupe(), so a retried "
                                    "operation is counted again."},
                {"id": "c", "text": "Items are sorted by name, so the delivery order changes the result."},
                {"id": "d", "text": "Last-writer-wins picks the earliest write."},
            ],
            "answer": "b",
            "explanation": "Without dedupe() every retried copy of an operation is added again. The real merge "
                           "loops over dedupe(ops), which keeps exactly one operation per id.",
        },
        "write": {
            "function": "dedupe",
            "prompt": "Write dedupe(ops): keep one operation per id.",
            "stub": '''def dedupe(ops):
    """Return each operation exactly once, sorted by id.

    ops is an iterable of Op(id, replica, lamport, kind, item, value). The same id can
    arrive many times. Keep one op per id. If two ops share an id but differ in content,
    keep the one with the larger _total(op), so every phone picks the same copy.
    Call _validate(op) on every op first: it raises ValueError for a malformed op.
    """
    raise NotImplementedError
''',
            "hook": None,
        },
        "explain": {
            "prompt": "In your own words: why can the same operation arrive more than once, and why is it "
                      "never counted twice?",
            "min_points": 3,
            "rubric": [
                {"id": "unique-id", "point": "Every operation carries a unique id.",
                 "keywords": kw("id", "ids", "identifier*", "unique")},
                {"id": "duplicates", "point": "Retries on a flaky network deliver the same operation more than once.",
                 "keywords": kw("retr*", "resen*", "re sen*", "twice", "again", "more than once", "duplicat*",
                                "repeat*", "copies", "copy")},
                {"id": "keep-one", "point": "The merge keeps one operation per id before counting.",
                 "keywords": kw("once", "one per", "one copy", "dedup*", "set", "ignor*", "drop*", "discard*",
                                "skip*")},
                {"id": "order-free", "point": "So the result is the same however many times and in whatever "
                                              "order operations arrive.",
                 "keywords": kw("order", "idempot*", "same result", "same state", "same answer", "identical",
                                "regardless", "converge*")},
            ],
        },
        "code": ["_total", "dedupe"],
    },
    {
        "id": "signed-adjust-sum",
        "order": 2,
        "title": "Quantity is a sum of signed changes",
        "summary": "Phones never send 'the count is 30'. They send changes: +50 received, -12 issued. "
                   "The quantity is the sum of every signed adjustment, so offline changes from many phones add up.",
        "predict": [
            {"id": "p1", "prompt": "50 tarpaulins arrive, then 12 and 8 are handed out. How many are left?",
             "ops": [op("ph-a:1", 1, "adjust", "tarpaulin", 50), op("ph-a:2", 2, "adjust", "tarpaulin", -12),
                     op("ph-a:3", 3, "adjust", "tarpaulin", -8)],
             "ask": {"item": "tarpaulin", "field": "qty"}},
            {"id": "p2", "prompt": "Two phones were offline all morning. A: +10, -2. B: -3, +6. What is the "
                                   "paracetamol count after they sync?",
             "ops": [op("ph-a:1", 1, "adjust", "paracetamol", 10), op("ph-b:1", 1, "adjust", "paracetamol", -3),
                     op("ph-a:2", 2, "adjust", "paracetamol", -2), op("ph-b:2", 2, "adjust", "paracetamol", 6)],
             "ask": {"item": "paracetamol", "field": "qty"}},
            {"id": "p3", "prompt": "Baby food: +5 received, then two phones issue 3 and 4. A note is also added. "
                                   "What is the count?",
             "ops": [op("ph-a:1", 1, "adjust", "baby-food", 5), op("ph-a:2", 2, "note", "baby-food", "stage 2 jars"),
                     op("ph-a:3", 3, "adjust", "baby-food", -3), op("ph-b:1", 2, "adjust", "baby-food", -4)],
             "ask": {"item": "baby-food", "field": "qty"}},
        ],
        "bug": {
            "mutant": "quantity-counts-magnitudes",
            "prompt": "One line of the merge was changed. Now the stock only ever goes up. What is wrong?",
            "options": [
                {"id": "a", "text": "abs() turns every issue (a negative adjustment) into a receipt."},
                {"id": "b", "text": "Adjustments from other replicas are skipped."},
                {"id": "c", "text": "The sum should be taken over unique items, not operations."},
                {"id": "d", "text": "Notes are added into the quantity."},
            ],
            "answer": "a",
            "explanation": "abs(o.value) throws the sign away, so -12 issued counts as +12 received. "
                           "The real merge sums o.value with its sign.",
        },
        "write": {
            "function": "quantity",
            "prompt": "Write quantity(ops): the quantity of one item.",
            "stub": '''def quantity(ops):
    """Return the quantity of one item as an int.

    ops is every operation of one item, of any kind ("adjust", "create", "note",
    "delete"). Only "adjust" operations change the quantity: their value is a signed,
    non-zero int (+ received, - issued). Return the sum of those values.
    """
    raise NotImplementedError
''',
            "hook": {"old": 'sum(o.value for o in group if o.kind == "adjust")', "new": "quantity(group)"},
        },
        "explain": {
            "prompt": "In your own words: why does the ledger add up signed changes instead of storing the "
                      "current count?",
            "min_points": 3,
            "rubric": [
                {"id": "signed-change", "point": "Each operation is a signed change, not an absolute count.",
                 "keywords": kw("signed", "sign", "change*", "delta*", "difference*", "increment*", "decrement*",
                                "plus", "minus", "negative", "positive")},
                {"id": "commutative", "point": "Addition gives the same total in any order.",
                 "keywords": kw("commutativ*", "associativ*", "any order", "order does not matter",
                                "order doesn t matter", "regardless of order", "order independent",
                                "whatever order", "same total")},
                {"id": "no-overwrite", "point": "Storing a total would let one phone overwrite another's changes.",
                 "keywords": kw("overwrit*", "lost", "lose", "loses", "losing", "clobber*", "replac*",
                                "instead of one")},
                {"id": "concurrent", "point": "Changes made on several phones while offline all count.",
                 "keywords": kw("both", "concurrent*", "offline", "two phones", "each phone", "every phone",
                                "all phones", "many phones", "several phones")},
            ],
        },
        "code": ["merge"],
    },
    {
        "id": "lamport-lww",
        "order": 3,
        "title": "Last writer wins, by Lamport time",
        "summary": "Names, notes and deletions keep one value. The write with the highest (Lamport time, "
                   "replica id) wins. The phone's wall clock is never used: it can be hours wrong.",
        "predict": [
            {"id": "p1", "prompt": "Phone A names the item 'Blankets' at Lamport 1. Phone B renames it "
                                   "'Wool blankets' at Lamport 3. What is the name?",
             "ops": [op("ph-a:1", 1, "create", "blankets", "Blankets"),
                     op("ph-b:1", 3, "create", "blankets", "Wool blankets")],
             "ask": {"item": "blankets", "field": "name"}},
            {"id": "p2", "prompt": "ph-a and ph-b both rename rice at Lamport 2. A tie on time is broken by "
                                   "replica id. What is the name?",
             "ops": [op("ph-b:1", 2, "create", "rice", "Rice (25 kg)"), op("ph-a:1", 2, "create", "rice", "Rice")],
             "ask": {"item": "rice", "field": "name"}},
            {"id": "p3", "prompt": "ph-c wrote its note at Lamport 5. ph-a's note at Lamport 2 arrives last, "
                                   "and ph-a's wall clock said it was later. What is the note?",
             "ops": [op("ph-c:1", 5, "note", "water", "1 L bottles"), op("ph-a:1", 2, "note", "water", "Use first")],
             "ask": {"item": "water", "field": "note"}},
            {"id": "p4", "prompt": "Tarpaulin is deleted at Lamport 4, then restored at Lamport 6. Is it alive?",
             "ops": [op("ph-a:1", 1, "adjust", "tarpaulin", 3), op("ph-a:2", 4, "delete", "tarpaulin", True),
                     op("ph-b:1", 6, "delete", "tarpaulin", False)],
             "ask": {"item": "tarpaulin", "field": "alive"}},
        ],
        "bug": {
            "mutant": "lww-picks-earliest",
            "prompt": "One line was changed. Renames no longer stick. What is wrong?",
            "options": [
                {"id": "a", "text": "Replica ids should never be compared."},
                {"id": "b", "text": "The default should be an empty string."},
                {"id": "c", "text": "min() keeps the oldest write, so a later write never takes effect."},
                {"id": "d", "text": "Writes should be ordered by the phone's wall clock."},
            ],
            "answer": "c",
            "explanation": "Last-writer-wins must keep the write with the highest (lamport, replica). "
                           "min() keeps the first one ever written.",
        },
        "write": {
            "function": "_lww",
            "prompt": "Write _lww(ops, default): the last-writer-wins value.",
            "stub": '''def _lww(ops, default):
    """Return the value of the winning write, or default if ops is empty.

    ops is a list of writes to one register (all names, all notes or all deletes of one
    item). The winner has the highest _order(op), which is (op.lamport, op.replica):
    the highest Lamport time, and on a tie the highest replica id.
    """
    raise NotImplementedError
''',
            "hook": None,
        },
        "explain": {
            "prompt": "In your own words: how does the ledger decide which rename wins, and why not use the "
                      "time on the phone?",
            "min_points": 3,
            "rubric": [
                {"id": "lamport", "point": "Writes are ordered by a Lamport (logical) clock.",
                 "keywords": kw("lamport", "logical", "counter")},
                {"id": "wall-clock", "point": "Phone wall clocks can be wrong or skewed.",
                 "keywords": kw("wall clock*", "skew*", "wrong time", "phone clock*", "phone s clock*",
                                "phone time", "system time", "time zone*", "drift*", "clock is wrong",
                                "clocks are wrong")},
                {"id": "highest-wins", "point": "The highest Lamport time wins.",
                 "keywords": kw("highest", "higher", "larger", "largest", "bigger", "biggest", "greatest",
                                "max*", "latest")},
                {"id": "tie-break", "point": "Ties are broken by replica id, so every phone agrees.",
                 "keywords": kw("tie*", "replica*", "same lamport", "same time", "phone id", "device id")},
            ],
        },
        "code": ["_order", "_lww"],
    },
    {
        "id": "oversell-flag",
        "order": 4,
        "title": "Oversold stock is flagged, not hidden",
        "summary": "Two offline phones can both hand out the last blankets. Both handouts really happened, "
                   "so the merge keeps both and the count goes negative. A live item below zero is flagged "
                   "for a human.",
        "predict": [
            {"id": "p1", "prompt": "2 blankets left. Phones A and B, both offline, each hand out 2. "
                                   "Is blankets flagged as oversold?",
             "ops": [op("ph-a:1", 1, "adjust", "blankets", 2), op("ph-a:2", 2, "adjust", "blankets", -2),
                     op("ph-b:1", 2, "adjust", "blankets", -2)],
             "ask": {"item": "blankets", "field": "oversold"}},
            {"id": "p2", "prompt": "4 bags of rice arrive and exactly 4 are handed out. Is rice flagged?",
             "ops": [op("ph-a:1", 1, "adjust", "rice", 4), op("ph-b:1", 1, "adjust", "rice", -3),
                     op("ph-a:2", 2, "adjust", "rice", -1)],
             "ask": {"item": "rice", "field": "oversold"}},
            {"id": "p3", "prompt": "Water went to -5, then the item was deleted. Is water flagged?",
             "ops": [op("ph-a:1", 1, "adjust", "water", -5), op("ph-b:1", 3, "delete", "water", True)],
             "ask": {"item": "water", "field": "oversold"}},
        ],
        "bug": {
            "mutant": "oversold-includes-zero",
            "prompt": "One line of the statistics was changed. Coordinators now get alerts for shelves that are "
                      "simply empty. What is wrong?",
            "options": [
                {"id": "a", "text": "Deleted items are flagged."},
                {"id": "b", "text": "Conflicts are counted for the winning replica."},
                {"id": "c", "text": "Only the first oversold item is reported."},
                {"id": "d", "text": "qty <= 0 flags stock of exactly zero; it should be qty < 0."},
            ],
            "answer": "d",
            "explanation": "Zero means everything was handed out, which is fine. Only a live item below zero "
                           "was oversold.",
        },
        "write": {
            "function": "oversold_items",
            "prompt": "Write oversold_items(state): which items need a human.",
            "stub": '''def oversold_items(state):
    """Return the list of oversold item keys, in the order they appear in state.

    state is the merged ledger: {item: {"name": str, "qty": int, "note": str,
    "alive": bool}}. An item is oversold when its quantity is below zero and it has
    not been deleted (alive is True).
    """
    raise NotImplementedError
''',
            "hook": {"old": '[i for i, s in state.items() if s["qty"] < 0 and s["alive"]]',
                     "new": "oversold_items(state)"},
        },
        "explain": {
            "prompt": "In your own words: why can stock go below zero, and what does the ledger do about it?",
            "min_points": 3,
            "rubric": [
                {"id": "offline-both", "point": "Two phones handed out the same last stock while offline.",
                 "keywords": kw("offline", "both", "two phones", "concurrent*", "same time", "no signal",
                                "without signal", "no internet")},
                {"id": "negative", "point": "Merging both handouts makes the quantity negative.",
                 "keywords": kw("negative", "below zero", "less than zero", "under zero", "minus")},
                {"id": "really-happened", "point": "Both handouts really happened, so neither can be rejected.",
                 "keywords": kw("really", "actually", "happened", "real", "physical*", "cannot undo",
                                "can t undo", "cannot reject", "can t reject")},
                {"id": "flag-human", "point": "It is flagged for a human, not hidden or clamped.",
                 "keywords": kw("flag*", "human", "coordinator*", "review*", "alert*", "warn*", "not hidden",
                                "not hide", "visible", "manual*")},
                {"id": "zero-deleted", "point": "Exactly zero, or a deleted item, is not flagged.",
                 "keywords": kw("zero", "deleted", "delete", "removed", "tombstone*", "alive")},
            ],
        },
        "code": ["stats"],
    },
]


PREDICT_PROMPT = "Predict the merged result. The real merge then runs on the same operations and checks you."
QUESTIONS = {"qty": "What is the quantity of {item}?", "name": "What is the name of {item}?",
             "note": "What is the note on {item}?", "alive": "Is {item} still alive (not deleted)? yes or no",
             "oversold": "Is {item} flagged as oversold? yes or no"}


def answer(case: dict):
    """The real merge's answer to a predict case."""
    ops = [Op(**o) for o in case["ops"]]
    item, field = case["item"], case["field"]
    if field == "oversold":
        return item in stats(ops)["oversold"]
    return merge(ops)[item][field]


def _functions(source: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}


def _function_source(source: str, names: list[str]) -> str:
    funcs = _functions(source)
    return "\n\n\n".join(ast.get_source_segment(source, funcs[n]) for n in names)


def _enclosing_function(source: str, lineno: int) -> str:
    best = None
    for node in _functions(source).values():
        if node.lineno <= lineno <= node.end_lineno and (best is None or node.lineno > best.lineno):
            best = node
    return ast.get_source_segment(source, best)


def _outcome(mod, ops: list[dict]):
    try:
        mops = [mod.Op(**o) for o in ops]
        return {"state": mod.merge(mops), "stats": mod.stats(mops)}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _bug(stage: dict, source: str) -> dict:
    spec = dict(stage["bug"])
    m = next(m for m in MUTANTS if m["name"] == spec["mutant"])
    idx = source.find(m["old"])
    if idx < 0:
        raise ValueError(f"mutant pattern not found: {m['name']}")
    mutated = source.replace(m["old"], m["new"], 1)
    lineno = source[:idx].count("\n") + 1
    import stillcounts.crdt as real
    mutant = _load(mutated)
    counterexample = None
    for case in stage["predict"]["cases"]:
        expected, got = _outcome(real, case["ops"]), _outcome(mutant, case["ops"])
        if expected != got:
            counterexample = {"case": case["id"], "ops": case["ops"], "expected": expected, "got": got}
            break
    if counterexample is None:
        raise ValueError(f"no predict case of stage {stage['id']!r} exposes {m['name']}")
    return {
        "mutant": m["name"],
        "prompt": spec["prompt"],
        "snippet": _enclosing_function(mutated, lineno),
        "line": mutated.splitlines()[lineno - 1].strip(),
        "options": spec["options"],
        "answer": spec["answer"],
        "explanation": spec["explanation"],
        "counterexample": counterexample,
    }


def build() -> list[dict]:
    source = SOURCE_PATH.read_text()
    out = []
    for stage in STAGES:
        s = json.loads(json.dumps(stage))
        cases = []
        for case in s["predict"]:
            c = {"id": case["id"], "prompt": case["prompt"], "ops": case["ops"], **case["ask"]}
            c["question"] = QUESTIONS[c["field"]].format(item=c["item"])
            c["expected"] = answer(c)
            cases.append(c)
        s["predict"] = {"prompt": PREDICT_PROMPT, "cases": cases}
        s["bug"] = _bug(s, source)
        s["code"] = _function_source(source, stage["code"])
        s["write"]["guided"] = guided.for_stage(s["id"])
        out.append(s)
    return json.loads(json.dumps(out))


def main() -> None:
    OUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
