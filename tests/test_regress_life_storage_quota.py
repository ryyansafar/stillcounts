"""Bug: when localStorage is full, new ops are shown on screen but silently not persisted.

`save()` swallows QuotaExceededError, so a phone that is offline with a large backlog keeps accepting taps that live
only in memory; a reload (or the OS killing the tab) loses all of them, and the volunteer is never told."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "life_storage_quota"
FAILING_SEEDS = [1, 3, 4]   # first failing seeds of `python -m chaos.run --scenario life_storage_quota`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a", quota=3000), sim.device("ph-b")
    sim.net.cut.add(a.name)
    for i in range(60):
        sim.tap(a, "adjust", "rice", i + 1)
    a.reload()
    sim.net.cut.discard(a.name)
    sim.run(3000)
    r = sim.finish()
    assert r["ok"], r["error"]
