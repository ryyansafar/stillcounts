"""The attack harness is the headline proof, so CI replays it instead of trusting a stored number.

OPEN is the list of scenarios the README documents as still failing. A scenario outside it that fails on any of the
first seeds is a regression in the product or in the harness, and fails the build. When a documented scenario is fixed,
remove it from OPEN and from its strict xfail in tests/test_regress_*.py in the same change."""
from chaos.scenarios import SCENARIOS, run_one

OPEN = {"life_storage_quota", "net_restore_old_backup"}
SEEDS = range(0, 6)


def test_only_the_documented_scenarios_fail_on_the_first_seeds():
    failed = {name for name in SCENARIOS for seed in SEEDS if not run_one(seed, name)["ok"]}
    assert failed <= OPEN, f"unexpected failures: {sorted(failed - OPEN)}"

