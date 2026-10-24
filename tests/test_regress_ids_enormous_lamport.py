"""Bug: a phone whose stored Lamport clock is corrupted to a huge value loses every new op.

The server refuses ops whose lamport is above 2^53-1 or more than MAX_SKEW ahead of its log. The phone keeps its
clock, keeps the refused ops in its own log and only counts them as `rejected`, so every op the volunteer enters
from then on is shown on that phone but never reaches the server or the other phones."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

pytestmark = pytest.mark.xfail(strict=True, reason="known product bug found by the chaos harness; remove this mark when the product is fixed")

SCENARIO = "ids_enormous_lamport"
FAILING_SEEDS = [0, 1, 2]   # first failing seeds of `python -m chaos.run --scenario ids_enormous_lamport`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a"), sim.device("ph-b")
    sim.run(100)
    a.tab.clock = 10 ** 7
    sim.tap(a, "adjust", "rice", 5)
    sim.run(3000)
    r = sim.finish()
    assert r["ok"], r["error"]
