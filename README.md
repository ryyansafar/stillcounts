# Still Counts

**A relief-camp ledger that keeps counting with the internet pulled.** Volunteers keep counting blankets, rice and medicines on their phones with no signal. The counts queue on the phone and merge correctly when the connection returns, with nothing lost and nothing counted twice.

Built for Devin Hacks Kochi (24 Oct 2026). Track: *Works anyway* (unreliable internet).

## How the merge works
The ledger is a set of operations, each with a unique id. The state is a pure function of that set, so any delivery order, any duplicates and any split-and-merge give byte-identical results.
- Quantities are sums of signed integer adjustments.
- Names, notes and deletions are last-writer-wins by (Lamport time, replica id), never by the phone's wall clock.
- Stock that goes negative after a merge (two phones issued the last blankets) is flagged for a human, not hidden.

## The referee
`oracle/` replays the same operations in hundreds of random orders, with duplicates, and fails if any result differs. It also catches deliberately broken merges. The agents that attack the product may run the oracle but are never allowed to edit it (`.github/CODEOWNERS`).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
node --test client/test/
.venv/bin/python -m oracle.oracle --seed 0xA3 --ops 240 --perms 200
.venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

## Earn the Code (Learn mode)
`learn/stages.json` holds four stages (duplicate ids, signed adjust sum, Lamport last-writer-wins, oversell flag). Each has predict cases whose answers come from the real merge, a find-the-bug item built from a real mutant in `oracle/mutation.py`, a small function to write and an explain rubric. `learn/grader.py` grades all four checkpoints with plain code: written functions run in a subprocess with a timeout, plugged into the real ledger code and judged by the referee. Edit the content in `learn/build_stages.py`, then regenerate:

```bash
.venv/bin/python -m learn.build_stages
```

Open `/static/learn.html` (the Learn link on the phone app and the board). Predict is graded in the browser with the same merge so it works with no signal; find the bug, write and explain go through `/api/learn`, which keeps the real merge code locked until predict, find the bug and write are earned.

## Referee Race
Open `/race` on a phone (the Race link on the phone app, or the QR code on the board's Race tab). Every phone gets the same operations in its own shuffled order with duplicates and races to predict the merged stock. The answer key is the real merge, so the round shows that order does not matter. The host starts rounds from the board.

## Chaos simulation
`chaos/` is a deterministic simulation of the 24 scout scenarios (dropped, duplicated and reordered requests, clock jumps, crashes, restores, two tabs on one phone). Run it with `.venv/bin/python -m chaos.run --seeds 0:500`; the full per-run output goes to `data/chaos_results.json` (git-ignored) and `data/chaos_summary.json` is a small committed summary of a real 500-seed run.

It found real product bugs: 8 of the 24 scenarios fail on some seeds (3506 of 12500 runs in the committed summary). Each one is pinned by `tests/test_regress_*.py`, marked as an expected failure until the product is fixed, so the suite stays green without hiding them. These are open bugs, not fixed ones.

## Devin
Devin sessions propose hostile network scenarios (partitions, reordering, duplicates, clock skew), build a chaos harness, run it against the oracle and open pull requests for anything they break. The sessions and pull requests are listed in `docs/devin.md`. A human presses merge.

## Licence
MIT.

## Socials & Support
- Portfolio: https://ryyansafar.site
- GitHub: https://github.com/ryyansafar
- Buy Me a Coffee: https://buymeacoffee.com/ryyansafar
- PayPal: https://www.paypal.com/paypalme/ryyansafar
- Razorpay: https://razorpay.me/@ryyansafar
