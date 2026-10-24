from oracle.mutation import MUTANTS, run


def test_every_mutant_pattern_exists_in_the_source():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "stillcounts" / "crdt.py").read_text()
    for m in MUTANTS:
        assert m["old"] in src, m["name"]


def test_the_referee_kills_every_mutant():
    result = run()
    assert result["total"] >= 12
    assert result["survivors"] == [], f"surviving mutants: {result['survivors']}"
    assert result["killed"] == result["total"]
