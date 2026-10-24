"""Runs 50 seeds of every chaos scenario (chaos/scenarios.py) through the deterministic simulation.

Scenarios that currently expose a product bug are listed in KNOWN_BUGS: for those this file only checks that the
harness itself runs, reaches quiescence and is reproducible; the failing seeds are pinned in tests/test_regress_*.py.
Every other scenario must pass the referee on all 50 seeds.
"""
import pytest

from chaos.scenarios import SCENARIOS, run_one

SEEDS = range(50)
KNOWN_BUGS = {   # scenario -> regression test that pins it
    "net_retry_same_id_new_content": "tests/test_regress_net_retry_same_id_new_content.py",
    "net_corrupt_line_shifts_cursor": "tests/test_regress_net_corrupt_line_shifts_cursor.py",
    "net_restore_old_backup": "tests/test_regress_net_restore_old_backup.py",
    "ids_enormous_lamport": "tests/test_regress_ids_enormous_lamport.py",
    "life_two_tabs_one_phone": "tests/test_regress_life_two_tabs_one_phone.py",
    "life_cursor_after_log_shrinks": "tests/test_regress_life_cursor_after_log_shrinks.py",
    "life_storage_quota": "tests/test_regress_life_storage_quota.py",
    "life_clock_jump_lamport": "tests/test_regress_life_clock_jump_lamport.py",
}


def test_all_24_scout_scenarios_are_registered():
    import json
    from pathlib import Path
    scouted = json.loads((Path(__file__).parent.parent / "docs" / "scout_scenarios.json").read_text())
    groups = {s for _, s, _ in SCENARIOS.values()} - {"CONTROL"}
    assert len([n for n, (_, s, _) in SCENARIOS.items() if s != "CONTROL"]) == 24
    assert groups == {"SCOUT-NETWORK", "SCOUT-IDS-CLOCKS", "SCOUT-APP-LIFECYCLE"}
    assert scouted


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_50_seeds(name):
    failures = []
    for seed in SEEDS:
        r = run_one(seed, name)
        assert set(r) >= {"seed", "scenario", "ok", "ops", "conflicts", "lost", "label"}
        assert r["ops"] > 0
        assert "did not reach quiescence" not in (r["error"] or ""), (seed, r["error"])
        if not r["ok"]:
            failures.append((seed, r["error"]))
    if name not in KNOWN_BUGS:
        assert not failures, f"{name}: {len(failures)} failing seeds, first {failures[0]}"


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_is_reproducible_from_its_seed(name):
    assert run_one(7, name) == run_one(7, name)


def test_regression_files_exist():
    from pathlib import Path
    root = Path(__file__).parent.parent
    for path in KNOWN_BUGS.values():
        assert (root / path).exists(), path
