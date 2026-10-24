# Overview
Attack the offline sync of a relief-camp ledger. The ledger is a set of operations merged by a pure function (`stillcounts/crdt.py`); a plain-code referee (`oracle/oracle.py`) checks that any delivery order gives identical state with nothing lost. Your job is to think of hostile conditions that could break that promise, and later to turn them into a runnable chaos harness. You never change the referee.

# Read first
`README.md`, `stillcounts/crdt.py`, `server/app.py`, `oracle/oracle.py`, `tests/`.

# Procedure for a SCOUT task (read-only)
1. Read the code and the tests. Do not edit files and do not open pull requests.
2. Propose up to 8 concrete hostile scenarios for the AREA you were given. Each must say what a replica does step by step, what the oracle should observe, and why it might break. Prefer scenarios the existing tests do not already cover.
3. Return only the structured JSON.

# Procedure for a WRITER task
1. Build `chaos/` (harness) that runs scenarios against the REAL `merge`, `server.app` and `oracle.oracle`: simulated replicas, network partitions, duplicated and reordered delivery, dropped syncs, clock skew in the ops' Lamport values, repeated retries.
2. Every scenario must be seeded and reproducible (`python -m chaos.run --seed N`). Print one JSON line per scenario with ops, conflicts and whether the oracle passed.
3. If a scenario breaks the oracle, do NOT change the oracle or `stillcounts/crdt.py` in the same pull request. Report the failing seed in your output and open a separate pull request with the minimal fix and a failing test first.
4. Run everything and report results.

# Forbidden
Editing `oracle/`, `tests/test_oracle_*.py` or `stillcounts/crdt.py` in the harness pull request; weakening any test; network calls to anything except the local test server.
