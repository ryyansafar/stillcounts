"""The course engine: it builds a course from any repository's functions. Predict answers come from really running the
code, bug items from generated mutants that the repository's own tests catch, write tasks are graded by those tests."""
import ast
import json

import pytest

from learn import course_grader, make_course, mutate
from tests.course_fixture import RULES, SPEC, git, make_repo

CLAMP = "shop/rules.py:clamp_qty"
NAME = "shop/rules.py:check_name"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return make_repo(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def course(repo):
    return make_course.build_course(repo, [CLAMP, NAME], course_id="shop", title="Shop rules", tests=["tests/test_rules.py"],
                                    specs=SPEC)


def stage_of(course, name):
    return next(s for s in course["stages"] if s["source"]["function"] == name)


# ---- mutants ----

CLAMP_SOURCE = make_course.function_source(RULES, "clamp_qty")


def test_mutants_change_exactly_one_line_and_still_parse():
    mutants = mutate.generate(CLAMP_SOURCE)
    assert len(mutants) >= 6
    original = CLAMP_SOURCE.splitlines()
    for m in mutants:
        ast.parse(m.source)
        changed = [i for i, (a, b) in enumerate(zip(original, m.source.splitlines())) if a != b]
        assert len(m.source.splitlines()) == len(original)
        assert changed == [m.line]
        assert m.original != m.replacement and m.description


def test_mutants_cover_comparisons_booleans_constants_and_arithmetic():
    src = "def f(a, b):\n    if a > b and not a:\n        return a + 1\n    return b is None\n"
    ops = {m.kind for m in mutate.generate(src)}
    assert {"compare", "boolean", "negation", "constant", "arithmetic", "identity"} <= ops


def test_mutants_are_deterministic_and_unique():
    a, b = mutate.generate(CLAMP_SOURCE), mutate.generate(CLAMP_SOURCE)
    assert [m.source for m in a] == [m.source for m in b]
    assert len({m.source for m in a}) == len(a)


def test_a_mutant_never_equals_the_original_source():
    assert all(m.source != CLAMP_SOURCE for m in mutate.generate(CLAMP_SOURCE))


# ---- finding functions ----

def test_function_source_returns_the_def_with_its_docstring():
    src = make_course.function_source(RULES, "check_name")
    assert src.startswith("def check_name(name):") and '"""Return a reason' in src and src.rstrip().endswith("return None")


def test_unknown_function_is_a_clear_error():
    with pytest.raises(make_course.CourseError, match="nope"):
        make_course.function_source(RULES, "nope")


def test_target_parsing():
    assert make_course.parse_target(CLAMP) == ("shop/rules.py", "clamp_qty")
    assert make_course.module_name("shop/rules.py") == "shop.rules"
    with pytest.raises(make_course.CourseError):
        make_course.parse_target("no-colon-here")


# ---- predict: the answers come from running the code ----

def test_predict_pool_expected_values_come_from_the_real_function(course, repo):
    import importlib.util
    spec = importlib.util.spec_from_file_location("shop_rules_real", repo / "shop" / "rules.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    stage = stage_of(course, "clamp_qty")
    pool = stage["predict"]["pool"]
    assert len(pool) >= 6 and stage["predict"]["show"] == 3
    for case in pool:
        assert case["call"].startswith("clamp_qty(")
        assert case["expected_value"] == eval(case["call"], {"clamp_qty": real.clamp_qty})


def test_predict_pool_has_variety_not_one_repeated_answer(course):
    values = {json.dumps(c["expected_value"]) for c in stage_of(course, "clamp_qty")["predict"]["pool"]}
    assert len(values) >= 4
    oks = {c["expected_value"] for c in stage_of(course, "check_name")["predict"]["pool"]}
    assert oks == {True, False}


def test_predict_cases_have_an_answer_kind_for_the_form(course):
    assert {c["answer_kind"] for c in stage_of(course, "clamp_qty")["predict"]["pool"]} == {"int"}
    assert {c["answer_kind"] for c in stage_of(course, "check_name")["predict"]["pool"]} == {"bool"}


# ---- find the bug: real mutants, caught by the repo's own tests ----

def test_the_bug_is_a_real_mutant_that_the_repos_tests_catch(course, repo):
    for stage in course["stages"]:
        bug = stage["bug"]
        original = make_course.function_source((repo / stage["source"]["path"]).read_text(), stage["source"]["function"])
        lines = original.splitlines()
        shown = bug["excerpt"]
        assert 3 <= len(shown) <= 5 and 0 <= bug["line_index"] < len(shown)
        assert bug["detected_by"] == "tests/test_rules.py"
        assert lines[bug["line_number"] - 1].strip() == bug["original_line"].strip() != bug["mutated_line"].strip()
        assert shown[bug["line_index"]].strip() == bug["mutated_line"].strip()
        got = make_course.run_tests(repo, stage["source"]["path"], stage["source"]["function"], bug["mutant_code"],
                                    ["tests/test_rules.py"], restricted=False)
        assert got["passed"] is False


def test_bug_options_are_behaviours_from_real_runs_and_one_is_right(course):
    for stage in course["stages"]:
        bug = stage["bug"]
        assert len(bug["options"]) >= 3 and len({o["text"] for o in bug["options"]}) == len(bug["options"])
        assert [o["id"] for o in bug["options"] if o["id"] == bug["answer"]] == [bug["answer"]]
        right = next(o for o in bug["options"] if o["id"] == bug["answer"])
        ce = bug["counterexample"]
        assert ce["call"] in right["text"] and ce["expected"] != ce["got"]


def test_bug_explanation_names_the_changed_line(course):
    for stage in course["stages"]:
        b = stage["bug"]
        assert b["explanation"] and b["mutant"]
        assert b["original_line"].strip() in b["explanation"] and b["mutated_line"].strip() in b["explanation"]


def test_every_generated_mutant_is_classified_honestly(course):
    for stage in course["stages"]:
        s = stage["strength"]
        assert s["mutants"] >= s["behaviour_changing"] >= s["caught_by_tests"] >= 1
        assert s["tests"] == ["tests/test_rules.py"]


# ---- write: graded by the repo's tests, in a box ----

def test_write_stub_keeps_signature_and_docstring_and_has_no_body(course):
    w = stage_of(course, "check_name")["write"]
    assert w["stub"].startswith("def check_name(name):") and '"""Return a reason' in w["stub"]
    assert w["stub"].rstrip().endswith("raise NotImplementedError")
    assert "return None" not in w["stub"] and "startswith" not in w["stub"]
    assert w["tests"] == ["tests/test_rules.py"] and w["function"] == "check_name"


def test_reference_code_is_the_real_function(course, repo):
    stage = stage_of(course, "clamp_qty")
    assert stage["code"] == make_course.function_source((repo / "shop/rules.py").read_text(), "clamp_qty")


def test_the_real_function_passes_the_write_grader(course, repo):
    stage = stage_of(course, "clamp_qty")
    result = course_grader.grade_write(stage, stage["code"], repo=repo)
    assert result["passed"] is True, result


def test_a_different_correct_implementation_passes(course, repo):
    stage = stage_of(course, "clamp_qty")
    src = "def clamp_qty(qty, limit=MAX_QTY):\n    return max(0, min(qty, limit))\n"
    assert course_grader.grade_write(stage, src, repo=repo)["passed"] is True


def test_a_wrong_implementation_fails_with_the_failing_test_named(course, repo):
    stage = stage_of(course, "clamp_qty")
    src = "def clamp_qty(qty, limit=MAX_QTY):\n    return min(qty, limit)\n"
    result = course_grader.grade_write(stage, src, repo=repo)
    assert result["passed"] is False and "test_clamp_floors_negatives" in result["error"]


def test_the_stub_itself_fails(course, repo):
    stage = stage_of(course, "check_name")
    assert course_grader.grade_write(stage, stage["write"]["stub"], repo=repo)["passed"] is False


@pytest.mark.parametrize("body", [
    "import os\n    return 1", "return open('/etc/passwd').read()", "return ().__class__", "return eval('1')",
    "return getattr(qty, 'real')",
])
def test_write_sandbox_refuses_escape_attempts(course, repo, body):
    stage = stage_of(course, "clamp_qty")
    result = course_grader.grade_write(stage, f"def clamp_qty(qty, limit=MAX_QTY):\n    {body}\n", repo=repo)
    assert result["passed"] is False and "not allowed" in result["error"] or "imports" in result["error"]


def test_an_endless_loop_times_out(course, repo):
    stage = stage_of(course, "clamp_qty")
    result = course_grader.grade_write(stage, "def clamp_qty(qty, limit=MAX_QTY):\n    while True:\n        pass\n", repo=repo,
                                       timeout=3)
    assert result["passed"] is False and "timed out" in result["error"]


def test_a_module_the_function_cannot_reach_is_not_exposed(course, repo):
    names = stage_of(course, "clamp_qty")["write"]["names"]
    assert "MAX_QTY" in names and "os" not in names and "json" not in names


# ---- explain: a rubric made from the docstring ----

def test_rubric_points_come_from_the_docstring_and_branches(course):
    rubric = stage_of(course, "check_name")["explain"]["rubric"]
    text = " ".join(p["point"] for p in rubric)
    assert "start with a space" in text and "1 to 12" in text
    assert all(p["keywords"] for p in rubric)


def test_a_good_explanation_covers_the_points(course):
    stage = stage_of(course, "check_name")
    cases = [c for c in stage["predict"]["pool"]][:3]
    mine = [{**c, "id": f"p{i + 1}"} for i, c in enumerate(cases)]
    shown = mine[0]
    stage = {**stage, "explain": {**stage["explain"], "example_case": "p1"}}
    text = ("The function returns a reason when the name is unusable and None otherwise. "
            "A name must be between 1 and 12 characters, so an empty or too long name is rejected with a reason. "
            "A name that starts with a space is also rejected with its own reason. "
            f"For example {shown['call']} gives {shown['expected']} when I run it.")
    result = course_grader.grade_explain(stage, text, cases=mine)
    assert result["passed"] is True, result


def test_a_padded_explanation_does_not_pass(course):
    stage = stage_of(course, "check_name")
    result = course_grader.grade_explain(stage, "the function is good and nice and does what it should do well " * 4)
    assert result["passed"] is False and result["missed"]


# ---- commit ranges ----

def test_functions_from_a_commit_range_and_their_diff(tmp_path):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    path = repo / "shop" / "rules.py"
    path.write_text(RULES.replace("if qty > limit:", "if qty >= limit:  # a quantity AT the limit is the limit"))
    git(repo, "commit", "-qam", "clamp at the limit")
    found = make_course.functions_from_range(repo, f"{base}..HEAD")
    assert [f["target"] for f in found] == [CLAMP]
    assert "qty >= limit" in found[0]["diff"]
    stage = make_course.build_stage(repo, CLAMP, order=1, tests=["tests/test_rules.py"], spec=SPEC[CLAMP], diff=found[0]["diff"])
    assert any("AT the limit" in p["point"] for p in stage["explain"]["rubric"])


def test_build_course_from_a_commit_range(tmp_path):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "shop" / "rules.py").write_text(RULES.replace('startswith(" ")', 'startswith((" ", "-"))'))
    git(repo, "commit", "-qam", "reject a leading dash")
    built = make_course.build_course(repo, commit_range=f"{base}..HEAD", course_id="r", title="R", tests=["tests/test_rules.py"],
                                     specs=SPEC)
    assert [s["source"]["function"] for s in built["stages"]] == ["check_name"]
    assert built["source"]["commit_range"] == f"{base}..HEAD"


def test_the_engine_refuses_a_function_the_tests_do_not_exercise(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "shop" / "rules.py").write_text(RULES + "\n\ndef untested(x):\n    return x + 1\n")
    with pytest.raises(make_course.CourseError, match="repository's tests do not catch"):
        make_course.build_stage(repo, "shop/rules.py:untested", order=1, tests=["tests/test_rules.py"],
                                spec={"inputs": ["1", "2", "3"], "views": [{"id": "v", "question": "q", "observe": "result"}]})
