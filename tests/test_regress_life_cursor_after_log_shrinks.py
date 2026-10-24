"""Bug: when the server log shrinks (lines dropped at restart), ops the server lost are not re-sent.

The server only full-resyncs when the cursor is past the end of the log; it does not know which ops it lost, and the
phones never re-upload ops they marked `synced`. Ops whose lines were dropped survive only on some phones."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "life_cursor_after_log_shrinks"
FAILING_SEEDS = [0, 1, 2]   # first failing seeds of `python -m chaos.run --scenario life_cursor_after_log_shrinks`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a, b = sim.device("ph-a"), sim.device("ph-b")
    for v in range(1, 4):
        sim.tap(a, "adjust", "rice", v)
    sim.run(3000)
    sim.server.crash()
    sim.server.corrupt_line(1)
    sim.server.start()
    sim.run(6000)
    r = sim.finish()
    assert r["ok"], r["error"]
