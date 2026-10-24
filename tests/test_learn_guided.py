"""The guided multiple choice for Write (used at difficulty level 1) must stay a real exam: exactly one of the four bodies passes
the referee, the others fail, and a locked stage's public view carries the choices but never says which is right."""
import json

import pytest
from fastapi.testclient import TestClient

from learn import grader
from server.app import create_app

STAGES = json.loads(open("learn/stages.json").read())


def assemble(stub, body):
    assert stub.rstrip().endswith("raise NotImplementedError")
    return stub.replace("    raise NotImplementedError\n", body)


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s["id"])
def test_exactly_one_guided_choice_passes_the_real_grader(stage):
    choices = stage["write"]["guided"]["choices"]
    assert len(choices) == 4 and len({c["id"] for c in choices}) == 4
    passing = [c["id"] for c in choices if grader.grade_write(stage["id"], assemble(stage["write"]["stub"], c["body"]))["passed"]]
    assert len(passing) == 1


def test_the_locked_view_offers_the_choices_without_marking_the_right_one(tmp_path):
    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    view = c.get("/api/learn/stages", headers={"X-Learner-Id": "g1"}).json()["stages"][0]
    guided = view["write"]["guided"]
    assert len(guided["choices"]) == 4
    assert set(guided["choices"][0]) == {"id", "body"} and "answer" not in guided and "correct" not in json.dumps(guided).lower()
