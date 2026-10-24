"""Fixed (was found by the chaos harness): a Lamport jump (phone clock in 2099, or an older client that seeded Lamport from Date.now()) makes the
server refuse all further ops of that phone as `lamport skew too large`; the phone never repairs its clock, so the ops
stay on that phone only."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "life_clock_jump_lamport"
FAILING_SEEDS = [0, 1, 2]   # seeds that failed before the fix, from `python -m chaos.run --scenario life_clock_jump_lamport`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a"), sim.device("ph-b")
    sim.run(100)
    a.tab.clock = 4_102_444_800_000
    sim.tap(a, "note", "rice", "Hold")
    sim.run(3000)
    r = sim.finish()
    assert r["ok"], r["error"]
