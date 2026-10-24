# Contributing

1. Write a failing test first, then the fix. Keep coverage at or above 80%.
2. Never edit `oracle/`, `stillcounts/crdt.py` or `tests/test_oracle_*.py` in the same change as the code they judge.
3. CI enforces 90% coverage (`--cov-fail-under=90`). Run `.venv/bin/python -m pytest -q` and `node --test client/test/` before pushing.
4. If you change the Learn stages, regenerate the contract fixture: `UPDATE_FIXTURE=1 .venv/bin/python -m pytest tests/test_learn_ui_contract.py`. If you change `data/devin_evidence.json`, run `python -m ops.devin_evidence`.
5. Operations are immutable; state is always derived by merging the set of operations.
6. The sync protocol exists twice: `client/app.js` and its Python mirror in `chaos/sim.py`. Change both in one commit; `tests/test_client_mirror_parity.py` replays scripted traces through each and fails if they differ.
7. `tests/test_chaos_open_set.py` replays the attack harness. If you fix an open scenario, remove it from `OPEN` there, drop its strict xfail in `tests/test_regress_*.py`, and re-run `.venv/bin/python -m chaos.run --seeds 0:500 --summary data/chaos_summary.json` (about 20 minutes).
8. The merge is mutation-tested: `.venv/bin/pip install mutmut==3.8.0 && .venv/bin/mutmut run`. No more than the 3 documented equivalent mutants may survive (`docs/mutation.md`).
9. Branches named `devin/*` may not touch `oracle/`, `stillcounts/crdt.py`, `tests/test_oracle_*.py` or `.github/CODEOWNERS`; CI fails them. CODEOWNERS alone is only advisory unless branch protection requires code-owner review, which the repo owner has to switch on.

## Socials & Support
- Portfolio: https://ryyansafar.site
- GitHub: https://github.com/ryyansafar
- Buy Me a Coffee: https://buymeacoffee.com/ryyansafar
- PayPal: https://www.paypal.com/paypalme/ryyansafar
- Razorpay: https://razorpay.me/@ryyansafar
