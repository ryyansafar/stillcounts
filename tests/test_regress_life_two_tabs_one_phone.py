"""Bug: two tabs on one phone overwrite each other's `sc_state`.

Both tabs share localStorage. Each keeps its own in-memory op list and persists the whole list, so while offline
the last tab to write wins and the other tab's ops disappear from storage; once that tab is closed they are gone."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "life_two_tabs_one_phone"
FAILING_SEEDS = [0, 2, 3]   # first failing seeds of `python -m chaos.run --scenario life_two_tabs_one_phone`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a"), sim.device("ph-b")
    sim.net.cut.add(a.name)
    t1 = a.tab
    sim.on_device(a, lambda: t1.add_op("adjust", "rice", 1), t1)
    t2 = sim.on_device(a, a.open_tab)
    sim.on_device(a, lambda: t1.add_op("adjust", "water", 2), t1)
    sim.on_device(a, lambda: t2.add_op("adjust", "blankets", 3), t2)
    t1.alive = False
    a.tabs.remove(t1)
    sim.net.cut.discard(a.name)
    sim.run(3000)
    r = sim.finish()
    assert r["ok"], r["error"]
