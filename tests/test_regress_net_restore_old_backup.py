"""Fixed (was found by the chaos harness): after the server is restored from an older backup, ops newer than the backup are lost.

The restore brings back the old epoch file together with the old log, so phones see an unchanged epoch and keep
their (now too large) cursor; and their ops written after the backup are marked `synced`, so they are never sent
again. The server and every phone that did not hold those ops lose them for good."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "net_restore_old_backup"
FAILING_SEEDS = [0, 1, 2]   # seeds that failed before the fix, from `python -m chaos.run --scenario net_restore_old_backup`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a"), sim.device("ph-b")
    sim.tap(a, "adjust", "rice", 5)
    sim.run(3000)
    snap = sim.server.snapshot()
    sim.tap(a, "adjust", "water", 2)
    sim.run(6000)
    sim.server.crash()
    sim.server.restore(snap)
    sim.server.start()
    sim.run(9000)
    r = sim.finish()
    assert r["ok"], r["error"]
