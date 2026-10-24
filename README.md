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
python3 -m venv .venv && .venv/bin/pip install pytest hypothesis fastapi uvicorn requests httpx jsonschema
.venv/bin/python -m pytest -q
.venv/bin/python -m oracle.oracle --seed 0xA3 --ops 240 --perms 200
.venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

## Earn the Code (Learn mode)
`learn/stages.json` holds four stages (duplicate ids, signed adjust sum, Lamport last-writer-wins, oversell flag). Each has predict cases whose answers come from the real merge, a find-the-bug item built from a real mutant in `oracle/mutation.py`, a small function to write and an explain rubric. `learn/grader.py` grades all four checkpoints with plain code: written functions run in a subprocess with a timeout, plugged into the real ledger code and judged by the referee. Edit the content in `learn/build_stages.py`, then regenerate:

```bash
.venv/bin/python -m learn.build_stages
```

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
