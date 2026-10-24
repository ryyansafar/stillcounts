"""Fixed (found by the chaos harness): an op id reused with different content used to diverge forever.

A legacy client (or a reinstall that restores the old counter) sends `ph-a:<id>` again with a different value after
the first copy already reached the server. The server answers `rejected: id reused with different content`, but the
phone kept its own copy in its log and never adopted the server's, so the phone and the server held different ops
under one id and their canonical() states differed. Fix: the phone gives its op a fresh id and sends it again, and
pulls the base's op under the old id (client/app.js reidentify(), mirrored in chaos/sim.py)."""
import pytest

from chaos.scenarios import run_one
from chaos.sim import Simulation

SCENARIO = "net_retry_same_id_new_content"
FAILING_SEEDS = [0, 1, 2]   # seeds that failed before the fix, from `python -m chaos.run --scenario net_retry_same_id_new_content`


@pytest.mark.parametrize("seed", FAILING_SEEDS)
def test_failing_seed_replays_clean(seed):
    r = run_one(seed, SCENARIO)
    assert r["ok"], f"seed {seed}: {r['error']} ({r['label']})"


def test_minimal_reproduction():
    sim = Simulation(0, SCENARIO)
    a = sim.device("ph-a")
    first = sim.tap(a, "adjust", "rice", 5)
    sim.run(3000)
    sim.net.drop_next_response.add(a.name)
    replica = a.tab.replica
    a.wipe(keep_replica=replica)
    a.open_tab()
    sim.tap(a, "adjust", "rice", 9, id=first["id"])
    sim.run(6000)
    r = sim.finish()
    assert r["ok"], r["error"]
