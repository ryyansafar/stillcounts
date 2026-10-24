"""Tests added after running mutmut against stillcounts/crdt.py (see docs/mutation.md): they pin down behaviour the
first mutation run showed nothing was checking: validation rules and their messages, the canonical text form, and the
clock's identity."""
import pytest

from stillcounts.crdt import Clock, Op, canonical, merge


def op(kind="adjust", value=1, lamport=1):
    return Op(id="a:1", replica="a", lamport=lamport, kind=kind, item="rice", value=value)


@pytest.mark.parametrize("bad,message", [
    (op(kind="explode"), "unknown op kind: 'explode'"),
    (op(lamport=0), "lamport must be a positive integer"),
    (op(lamport=-4), "lamport must be a positive integer"),
    (op(lamport=True), "lamport must be a positive integer"),
    (op(lamport="3"), "lamport must be a positive integer"),
    (op(lamport=2.0), "lamport must be a positive integer"),
    (op(value=0), "adjust needs a non-zero integer"),
    (op(value=True), "adjust needs a non-zero integer"),
    (op(value="5"), "adjust needs a non-zero integer"),
    (op(kind="delete", value="yes"), "delete needs a boolean"),
    (op(kind="delete", value=1), "delete needs a boolean"),
    (op(kind="note", value=5), "note needs a string"),
    (op(kind="create", value=None), "create needs a string"),
])
def test_each_invalid_operation_is_refused_with_its_own_message(bad, message):
    with pytest.raises(ValueError) as err:
        merge([bad])
    assert str(err.value) == message


def test_a_valid_operation_of_every_kind_is_accepted():
    merge([op(), op("create", "Rice", 2), op("note", "damp", 3), op("delete", True, 4)])


def test_canonical_is_sorted_compact_and_keeps_non_ascii_characters():
    assert canonical({"b": {"z": 1, "a": 2}, "a": "café"}) == '{"a":"café","b":{"a":2,"z":1}}'


def test_a_clock_remembers_whose_it_is():
    assert Clock("ph-a", 7).replica == "ph-a"


# Fixture note: Devin QA issue #9 (https://github.com/ryyansafar/stillcounts/issues/9) was found by the browser QA session
# with Malayalam item names that all collapsed into one item. The slugging lived in client/app.js (see the matching test in
# client/test/app.test.js); here we pin the other half, that the merge itself keeps distinct non-Latin item ids apart.
# The fixture uses two neutral Greek words, chosen arbitrarily: the test is about identity, not about any language.
def test_distinct_non_latin_item_ids_stay_distinct_items():
    ops = [Op(id="a:1", replica="a", lamport=1, kind="adjust", item="\u03c1\u03cd\u03b6\u03b9", value=2),
           Op(id="a:2", replica="a", lamport=2, kind="adjust", item="\u03b6\u03c5\u03b3\u03cc\u03c2", value=3)]
    state = merge(ops)
    assert len(state) == 2
