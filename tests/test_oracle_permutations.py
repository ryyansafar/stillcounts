"""The oracle is the referee. Agents may run it but never edit it (see CODEOWNERS)."""
import pytest

from oracle.oracle import OracleFailure, check_delivery, generate_ops, permutation_check
from stillcounts.crdt import Op, merge


def test_generated_ops_are_deterministic_for_a_seed():
    assert generate_ops(seed=0xA3, n=60, replicas=3) == generate_ops(seed=0xA3, n=60, replicas=3)
    assert generate_ops(seed=1, n=60, replicas=3) != generate_ops(seed=2, n=60, replicas=3)


def test_generated_ops_have_unique_ids_and_requested_size():
    ops = generate_ops(seed=7, n=100, replicas=4)
    assert len(ops) == 100 and len({o.id for o in ops}) == 100


def test_permutation_check_passes_for_the_real_merge():
    ops = generate_ops(seed=0xA3, n=240, replicas=3)
    result = permutation_check(ops, perms=200, seed=1)
    assert result["ok"] and result["permutations"] == 200 and result["ops"] == 240


def test_permutation_check_is_seeded_and_repeatable():
    ops = generate_ops(seed=5, n=80, replicas=3)
    assert permutation_check(ops, perms=50, seed=9) == permutation_check(ops, perms=50, seed=9)


def arrival_order_merge(ops):
    """A deliberately WRONG merge: last writer by ARRIVAL order wins, ignoring lamport time."""
    state = {}
    for o in ops:
        s = state.setdefault(o.item, {"name": o.item, "qty": 0, "note": "", "alive": True})
        if o.kind == "adjust":
            s["qty"] += o.value
        elif o.kind == "create":
            s["name"] = o.value
        elif o.kind == "note":
            s["note"] = o.value
        elif o.kind == "delete":
            s["alive"] = not o.value
    return state


def test_the_oracle_catches_a_last_write_wins_by_arrival_mutation():
    ops = generate_ops(seed=0xA3, n=240, replicas=3)
    with pytest.raises(OracleFailure) as exc:
        permutation_check(ops, perms=50, seed=1, merge_fn=arrival_order_merge)
    assert "differs" in str(exc.value)


def test_oracle_catches_a_merge_that_drops_duplicates_wrongly():
    def double_counting(ops):
        state = merge(ops)
        for o in ops:                       # counts every delivery, so duplicates inflate quantities
            if o.kind == "adjust":
                state[o.item]["qty"] += o.value
        return state

    ops = generate_ops(seed=3, n=60, replicas=3)
    with pytest.raises(OracleFailure):
        permutation_check(ops, perms=30, seed=2, merge_fn=double_counting)


def test_check_delivery_passes_when_every_replica_receives_everything():
    ops = generate_ops(seed=11, n=50, replicas=3)
    deliveries = {"a": list(reversed(ops)), "b": ops + ops[:5], "c": ops[25:] + ops[:25]}
    assert check_delivery(ops, deliveries)["lost"] == 0


def test_check_delivery_reports_lost_ops():
    ops = generate_ops(seed=11, n=50, replicas=3)
    with pytest.raises(OracleFailure) as exc:
        check_delivery(ops, {"a": ops, "b": ops[:-3]})
    assert "lost 3" in str(exc.value)
