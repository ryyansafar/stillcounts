import itertools
import json
import random

import pytest
from hypothesis import given, settings, strategies as st

from stillcounts.crdt import Clock, Op, canonical, merge, stats

ITEMS = ["blankets", "rice", "paracetamol"]
REPLICAS = ["ph-a", "ph-b", "ph-c"]


def op(i, replica, lamport, kind, item, value):
    return Op(id=f"{replica}:{i}", replica=replica, lamport=lamport, kind=kind, item=item, value=value)


def test_adjust_ops_sum_into_quantity():
    ops = [op(1, "a", 1, "adjust", "rice", 40), op(2, "b", 1, "adjust", "rice", -12)]
    assert merge(ops)["rice"]["qty"] == 28


def test_duplicate_delivery_is_idempotent():
    o = op(1, "a", 1, "adjust", "rice", 5)
    assert merge([o, o, o])["rice"]["qty"] == 5


def test_name_is_last_writer_wins_with_replica_tiebreak():
    ops = [op(1, "a", 3, "create", "x", "Old"), op(1, "b", 3, "create", "x", "New")]
    assert merge(ops)["x"]["name"] == "New"          # same lamport: higher replica id wins


def test_later_lamport_beats_earlier_regardless_of_arrival_order():
    early = op(1, "z", 1, "note", "x", "first")
    late = op(1, "a", 2, "note", "x", "second")
    assert merge([early, late])["x"]["note"] == "second"
    assert merge([late, early])["x"]["note"] == "second"


def test_delete_is_a_tombstone_and_can_be_overwritten_by_a_later_restore():
    ops = [op(1, "a", 1, "create", "x", "Tarps"), op(2, "a", 2, "delete", "x", True)]
    assert merge(ops)["x"]["alive"] is False
    assert merge(ops + [op(3, "b", 3, "delete", "x", False)])["x"]["alive"] is True


def test_item_without_a_create_op_still_exists_named_by_its_id():
    assert merge([op(1, "a", 1, "adjust", "water", 3)])["water"]["name"] == "water"


def test_conflicts_count_the_losing_writes_from_other_replicas():
    ops = [op(1, "a", 1, "note", "x", "A"), op(1, "b", 2, "note", "x", "B"), op(1, "c", 3, "note", "x", "C")]
    s = stats(ops)
    assert s["conflicts"] == 2 and s["ops"] == 3


def test_negative_stock_is_reported_as_an_oversell_not_hidden():
    ops = [op(1, "a", 1, "adjust", "blankets", 10), op(1, "b", 1, "adjust", "blankets", -7), op(2, "c", 2, "adjust", "blankets", -6)]
    s = stats(ops)
    assert merge(ops)["blankets"]["qty"] == -3
    assert s["oversold"] == ["blankets"]


def test_adjust_of_zero_or_non_integer_is_rejected():
    with pytest.raises(ValueError):
        merge([op(1, "a", 1, "adjust", "x", 0)])
    with pytest.raises(ValueError):
        merge([op(1, "a", 1, "adjust", "x", 1.5)])


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        merge([op(1, "a", 1, "explode", "x", 1)])


def test_clock_ticks_and_observes_remote_time():
    c = Clock("a")
    assert c.tick() == 1 and c.tick() == 2
    c.observe(10)
    assert c.tick() == 11


def test_canonical_state_is_byte_identical_json():
    ops = [op(1, "a", 1, "adjust", "rice", 2), op(2, "a", 2, "create", "rice", "Rice")]
    assert canonical(merge(ops)) == canonical(merge(list(reversed(ops))))
    assert json.loads(canonical(merge(ops)))["rice"]["qty"] == 2


op_strategy = st.builds(
    lambda replica, n, lamport, kind, item, amount, text: Op(
        id=f"{replica}:{n}", replica=replica, lamport=lamport, kind=kind, item=item,
        value={"adjust": amount, "create": text, "note": text, "delete": amount > 0}[kind]),
    st.sampled_from(REPLICAS), st.integers(0, 40), st.integers(1, 30),
    st.sampled_from(["adjust", "create", "note", "delete"]), st.sampled_from(ITEMS),
    st.integers(-20, 20).filter(lambda x: x != 0), st.text(alphabet="abcxyz", min_size=1, max_size=4),
)


@settings(max_examples=150, deadline=None)
@given(st.lists(op_strategy, min_size=1, max_size=25), st.randoms(use_true_random=False))
def test_any_order_and_any_duplication_converges(ops, rnd):
    # ops with the same id must be identical, so keep the first of each id
    uniq = {o.id: o for o in ops}.values()
    base = canonical(merge(uniq))
    for _ in range(20):
        shuffled = list(uniq) + rnd.sample(list(uniq), k=min(3, len(list(uniq))))   # add duplicates
        rnd.shuffle(shuffled)
        assert canonical(merge(shuffled)) == base


@settings(max_examples=100, deadline=None)
@given(st.lists(op_strategy, min_size=2, max_size=20), st.integers(1, 19))
def test_merging_in_two_halves_equals_merging_all(ops, cut):
    uniq = list({o.id: o for o in ops}.values())
    cut = min(cut, len(uniq) - 1) or 1
    a, b = uniq[:cut], uniq[cut:]
    assert canonical(merge(a + b)) == canonical(merge(b + a))
