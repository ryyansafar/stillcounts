# Contributing to Earn the Code

This repository (`stillcounts`) holds Earn the Code and its first course, an offline-first ledger. Course content lives in `learn/`; the code being taught lives in `stillcounts/` and `oracle/`.

1. Write a failing test first, then the fix. Keep coverage at or above 80%.
2. Never edit `oracle/`, `stillcounts/crdt.py` or `tests/test_oracle_*.py` in the same change as the code they judge.
3. CI enforces 90% coverage (`--cov-fail-under=90`). Run `.venv/bin/python -m pytest -q` and `node --test client/test/` before pushing.
4. If you change the Learn stages, regenerate the contract fixture: `UPDATE_FIXTURE=1 .venv/bin/python -m pytest tests/test_learn_ui_contract.py`. If you change `data/devin_evidence.json`, run `python -m ops.devin_evidence`.
5. Learn rules (tests pin them): the server is the grader; a wrong predict case is named, never answered, and re-rolled from a per-learner seed (`learn/cases.py`, templates in `learn/build_stages.py`); find-the-bug needs the changed line and the cause and locks for 30 s after a miss; explain never unlocks a stage. Copy must stay honest: the merge and `client/crdt.js` are public, what is gated is the guided path, the reveal and the unlock record. Sandbox memory claims are per platform: RLIMIT_AS is Linux only, the 256 MB allocation guard is everywhere.
6. Operations are immutable; state is always derived by merging the set of operations.
7. The sync protocol exists twice: `client/app.js` and its Python mirror in `chaos/sim.py`. Change both in one commit; `tests/test_client_mirror_parity.py` replays scripted traces through each and fails if they differ.
8. `tests/test_chaos_open_set.py` replays the attack harness. If you fix an open scenario, remove it from `OPEN` there, drop its strict xfail in `tests/test_regress_*.py`, and re-run `.venv/bin/python -m chaos.run --seeds 0:500 --summary data/chaos_summary.json` (about 20 minutes).
9. The merge is mutation-tested: `.venv/bin/pip install mutmut==3.8.0 && .venv/bin/mutmut run`. No more than the 3 documented equivalent mutants may survive (`docs/mutation.md`).
9. Course content is graded by plain code, never by a model. A new stage needs predict cases whose answers come from running the real merge, a find-the-bug item built from a real mutant, a write task judged by the referee, and an explain rubric. Do not add a checkpoint that a model grades.
10. Anything not yet on main (weakest-function targeting, fading help, streaks, `make_course` inside the chat) must be labelled as not built in the docs until it is. If a Devin CLI session did later work, label it "Devin CLI, local".
11. Branches named `devin/*` may not touch `oracle/`, `stillcounts/crdt.py`, `tests/test_oracle_*.py` or `.github/CODEOWNERS`; CI fails them. CODEOWNERS alone is only advisory unless branch protection requires code-owner review, which the repo owner has to switch on.

## Chat shell

The root page is the chat (`client/chat.html`). `client/chat_core.js` is the stop-until-earned state machine and must stay pure (no DOM, no network, no clock): change it test first in `client/test/chat_core.test.js`. Rules the tests pin:

- Devin builds one stage, stops, and is told to continue only after the stage is earned (`LOOKAHEAD` is 0). The bridge refuses an unearned continue with 403.
- The replay (`chat_replay.js`) labels everything it makes as simulated and obeys the same gate as a real session.
- The browser never decides an unlock. Learner identity is the server's HttpOnly cookie (`sc_learner`); the page sends no identity, only an attempt name (`X-Attempt`) so a new chat starts from zero. Live sessions can be graded only by the browser that started them.
- Never show a locked stage's code, the expected answer or the right option in the browser; never keep them in `localStorage`.
- Heart rate stays on the device. The stress policy (`client/stress.js`) lowers difficulty and never auto-passes anything.

The live bridge is `server/devin_bridge.py` and `server/live_course.py`, with the browser side in `client/devin_live.js` and `client/chat_bridge.js`. The token stays on the server. Test the bridge with a fake Devin (`tests/test_devin_bridge.py`), not a real one.

Before you push: `.venv/bin/python -m pytest -q`, `node --test client/test/`, `.venv/bin/python -m oracle.oracle --seed 0xA3 --ops 240 --perms 200` and `.venv/bin/python -m oracle.mutation`. `scripts/run.sh test` runs the first two. Do not commit generated or live data (`data/ops.jsonl*`, `data/chaos_results.json`). Commit messages are conventional (`feat:`, `fix:`, `docs:`) and commits are authored by you alone.

## Socials & Support
- Portfolio: https://ryyansafar.site
- GitHub: https://github.com/ryyansafar
- Buy Me a Coffee: https://buymeacoffee.com/ryyansafar
- PayPal: https://www.paypal.com/paypalme/ryyansafar
- Razorpay: https://razorpay.me/@ryyansafar
