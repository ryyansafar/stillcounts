"""Regression tests for bugs found during browser QA (see docs/QA.md)."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIP_NAMES = {"segno": "segno", "fastapi": "fastapi", "pydantic": "fastapi", "uvicorn": "uvicorn"}


def _third_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    local = {"stillcounts", "server", "oracle", "ops", "scripts"}
    return {m for m in mods if m not in sys.stdlib_module_names and m not in local and m != "__future__"}


def _pip_packages(text: str) -> set[str]:
    pkgs = set()
    for line in text.splitlines():
        m = re.search(r"pip install (.*)", line)
        if m:
            pkgs.update(w for w in m.group(1).split() if not w.startswith("-"))
    return pkgs


def test_every_server_dependency_is_installed_by_run_script_and_ci():
    needed = {PIP_NAMES.get(m, m) for m in _third_party_imports(ROOT / "server" / "app.py")}
    assert "segno" in needed
    for f in (ROOT / "scripts" / "run.sh", ROOT / ".github" / "workflows" / "ci.yml"):
        missing = needed - _pip_packages(f.read_text())
        assert not missing, f"{f.name} does not install {sorted(missing)}"


def test_activity_feed_describes_a_new_item_in_plain_words(tmp_path):
    from fastapi.testclient import TestClient

    from server.app import create_app

    c = TestClient(create_app(tmp_path / "ops.jsonl"))
    c.post("/api/sync", json={"replica": "ph-a", "name": "Maya", "cursor": 0, "ops": [
        {"id": "ph-a:1", "replica": "ph-a", "lamport": 1, "kind": "create", "item": "lanterns", "value": "Lanterns"}]})
    act = c.get("/api/state").json()["activity"][-1]
    assert act == {"who": "Maya", "text": "added Lanterns"}
