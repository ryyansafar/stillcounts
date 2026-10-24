# Mutation testing of the merge

Two different checks, and only one of them is independent.

## 1. The referee's 14 hand-written mutants (a demo set)

`oracle/mutation.py` holds 14 deliberately broken copies of `stillcounts/crdt.py` that we wrote ourselves (for example "last writer wins picks the earliest"). The referee catches all 14. Because we chose the list, **14 of 14 is a demo, not a score.** It is what the "Break the referee on purpose" button on the board's Proof tab runs.

## 2. A real mutation run with mutmut

`mutmut` generates its own mutants of `stillcounts/crdt.py` (changed operators, constants, conditions, messages) and runs the tests against each. Nobody on this project chose them.

```bash
.venv/bin/pip install mutmut==3.8.0
.venv/bin/mutmut run          # config in setup.cfg; about a minute
.venv/bin/mutmut results
```

Result of the committed run (`data/mutmut_report.json`): **163 of 166 mutants killed, 3 survived.**

The first run killed 142 and left 24 alive. All 21 extra kills came from new tests in `tests/test_crdt_validation.py` (the validation rules and their messages, the exact canonical text, and the clock's identity). `stillcounts/crdt.py` was not changed.

The 3 survivors are equivalent mutants: changes that do not alter behaviour, so no test could kill them.

| Mutant | Why it is equivalent |
|---|---|
| `dedupe`: `>` became `>=` | It only differs when two ops have the same total order key; that key includes every field, so they are the same op. |
| `merge`: default delete flag `False` became `None` | `not None` and `not False` are both `True`. |
| `canonical`: `ensure_ascii=False` became `None` | `json.dumps` treats `None` exactly like `False` here. |

CI runs the same command in its own job and fails if more than 3 mutants survive.

What this does not cover: only `stillcounts/crdt.py` is mutated. The server, the phone client and the sync protocol are covered by the chaos harness (see the README), not by mutation testing. `client/crdt.js` is checked against the Python merge by a differential fuzz test, not mutated.
