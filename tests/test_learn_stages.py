import json
from pathlib import Path

from oracle.mutation import MUTANTS, referee_failure
from stillcounts.crdt import Op, merge, stats

from learn import build_stages
from learn.grader import STAGES, load_stages, public_stages

SOURCE = (Path(__file__).resolve().parent.parent / "stillcounts" / "crdt.py").read_text()
MUTANT_BY_NAME = {m["name"]: m for m in MUTANTS}
FIELDS = {"qty", "name", "note", "alive", "oversold"}


def _answer(case):
    ops = [Op(**o) for o in case["ops"]]
    item, field = case["item"], case["field"]
    if field == "oversold":
        return item in stats(ops)["oversold"]
    return merge(ops)[item][field]


def test_stages_json_is_in_sync_with_the_builder():
    assert json.loads(STAGES.read_text()) == build_stages.build()


def test_four_stages_in_the_planned_order():
    stages = load_stages()
    assert [s["order"] for s in stages] == [1, 2, 3, 4]
    assert [s["id"] for s in stages] == ["duplicate-ids", "signed-adjust-sum", "lamport-lww", "oversell-flag"]
    for s in stages:
        assert {"title", "summary", "predict", "bug", "write", "explain", "code"} <= s.keys()


def test_predict_answers_match_the_real_merge():
    for s in load_stages():
        cases = s["predict"]["cases"]
        assert s["predict"]["prompt"]
        assert len(cases) >= 3, s["id"]
        assert len({c["id"] for c in cases}) == len(cases)
        for case in cases:
            assert case["field"] in FIELDS and case["item"] in case["question"]
            assert case["expected"] == _answer(case), (s["id"], case["id"])


def test_every_bug_is_a_real_mutant_that_the_referee_kills():
    from oracle.mutation import _load
    for s in load_stages():
        bug = s["bug"]
        m = MUTANT_BY_NAME[bug["mutant"]]
        mutated = SOURCE.replace(m["old"], m["new"], 1)
        assert mutated != SOURCE
        assert referee_failure(_load(mutated)) is not None
        assert m["new"].splitlines()[0].strip() in bug["snippet"]
        assert bug["line"].strip() in bug["snippet"]
        assert bug["answer"] in {o["id"] for o in bug["options"]}
        assert len(bug["options"]) >= 3
        cx = bug["counterexample"]
        assert cx["expected"] != cx["got"]


def test_bug_mutants_match_the_stage_topic():
    assert [s["bug"]["mutant"] for s in load_stages()] == [
        "merge-skips-dedupe", "quantity-counts-magnitudes", "lww-picks-earliest", "oversold-includes-zero"]


def test_write_tasks_have_a_stub_with_a_docstring():
    import ast
    for s in load_stages():
        w = s["write"]
        tree = ast.parse(w["stub"])
        assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
        assert tree.body[0].name == w["function"]
        assert ast.get_docstring(tree.body[0])
        if w["hook"]:
            assert w["hook"]["old"] in SOURCE


def test_explain_rubrics_have_key_points():
    for s in load_stages():
        e = s["explain"]
        assert e["prompt"]
        assert len(e["rubric"]) >= 3
        assert 1 <= e["min_points"] <= len(e["rubric"])
        for p in e["rubric"]:
            assert p["point"] and p["keywords"] and all(p["keywords"])


def test_locked_code_is_the_real_code():
    for s in load_stages():
        for chunk in s["code"].split("\n\n\n"):
            assert chunk in SOURCE


def test_public_stages_hide_answers():
    text = json.dumps(public_stages())
    for s in load_stages():
        assert s["bug"]["mutant"] not in text
        assert s["code"] not in text
        for p in s["explain"]["rubric"]:
            assert p["point"] not in text
    for s in public_stages():
        assert "answer" not in s["bug"] and "counterexample" not in s["bug"]
        assert all("expected" not in c for c in s["predict"]["cases"])
        assert all(c["ops"] and c["question"] for c in s["predict"]["cases"])
        assert "rubric" not in s["explain"] and "code" not in s
        assert "hook" not in s["write"]


def test_stage_ids_are_url_safe_strings():
    import re
    for s in load_stages():
        assert isinstance(s["id"], str) and re.fullmatch(r"[a-z0-9-]+", s["id"])
