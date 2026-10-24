"""The docs must say what the code and the committed data say."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
DEMO = (ROOT / "docs" / "DEMO.md").read_text(encoding="utf-8")


def scenario_count() -> int:
    return len(json.loads((ROOT / "data" / "chaos_summary.json").read_text())["by_scenario"])


def test_the_readme_scenario_count_is_the_one_in_chaos_summary():
    n = scenario_count()
    assert re.search(rf"\b{n} scenarios\b", README), f"README must say {n} scenarios (data/chaos_summary.json)"
    assert not re.search(r"\b24 (hostile )?scenarios\b", README.replace("24 hostile ones", ""))
    assert "board shows 25" in README or f"board shows {n}" in README


def test_the_chaos_summary_agrees_with_itself():
    d = json.loads((ROOT / "data" / "chaos_summary.json").read_text())
    assert d["runs"] == sum(v["runs"] for v in d["by_scenario"].values())
    assert d["failed"] == sum(v["failed"] for v in d["by_scenario"].values())


def test_the_demo_preflight_command_turns_on_phone_writes():
    preflight = DEMO.split("## Before you go on", 1)[1].split("## Beat 1", 1)[0]
    first_block = preflight.split("```bash", 1)[1].split("```", 1)[0]
    assert "STILLCOUNTS_LEARN_WRITE_LAN=1" in first_block and "scripts/run.sh" in first_block
    assert "runs on the demo laptop" in preflight


def test_nothing_in_the_docs_calls_built_features_in_progress():
    assert "Understanding Map" not in README and "in progress" not in README
