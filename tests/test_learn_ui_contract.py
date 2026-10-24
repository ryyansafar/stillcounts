"""learn.js against the real API: the stage list the server sends must be what the page needs, and the browser's merge
must agree with the real grader on every predict case. Skipped when node is not installed."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from learn.grader import load_stages
from server.app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_learn_js_accepts_the_real_stage_list(tmp_path):
    client = TestClient(create_app(tmp_path / "ops.jsonl"))
    api = client.get("/api/learn/stages", headers={"X-Learner-Id": "contract"}).json()
    expected = {s["id"]: {c["id"]: c["expected"] for c in s["predict"]["cases"]} for s in load_stages()}
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"api": api, "expected": expected}))
    run = subprocess.run([shutil.which("node"), "--test", str(ROOT / "client" / "test" / "learn_contract.js")], cwd=ROOT,
                         env={**os.environ, "LEARN_FIXTURE": str(fixture)},
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "# pass 2" in run.stdout
