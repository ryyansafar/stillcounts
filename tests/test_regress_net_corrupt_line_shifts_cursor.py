"""Bug: a log line lost on server restart is never re-uploaded.

When `LogStore` drops an unreadable line at restart (disk fault, partial write of an older line), that op vanishes
from the server. Every phone already marked it `synced`, the epoch did not change, so no phone sends it again and
later-syncing phones never receive it: the op exists only on the phones that had it."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "net_corrupt_line_shifts_cursor"
FAILING_SEEDS = [0, 1, 2]   # first failing seeds of `python -m chaos.run --scenario net_corrupt_line_shifts_cursor`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a"), sim.device("ph-b")
    sim.tap(a, "adjust", "rice", 5)
    sim.tap(a, "adjust", "water", 2)
    sim.run(3000)
    sim.server.crash()
    sim.server.corrupt_line(0)
    sim.server.start()
    sim.run(6000)
    r = sim.finish()
    assert r["ok"], r["error"]
