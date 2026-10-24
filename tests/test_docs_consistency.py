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


CONTRIBUTING = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
CLIENT = "".join((ROOT / "client" / f).read_text(encoding="utf-8") for f in ("learn.js", "learn_views.js"))
CLAIMS = re.compile(r"stays locked|arrives locked|stays hidden|keeps the real merge code locked|the algorithm is secret|real code is unlocked",
                    re.I)


def test_no_doc_or_ui_string_claims_the_algorithm_is_secret_or_the_code_locked():
    for name, text in (("README", README), ("DEMO", DEMO), ("CONTRIBUTING", CONTRIBUTING), ("client", CLIENT)):
        assert not CLAIMS.search(text), (name, CLAIMS.search(text).group(0))


def test_the_docs_say_what_is_public_and_what_is_gated():
    assert "nothing here is secret" in README.lower()
    assert "client/crdt.js" in README and "public" in README
    assert "reference-solution reveal" in README and "unlock record" in README
    assert "merge code is public" in CLIENT and "nothing here is secret" in CLIENT


def test_the_memory_claim_is_per_platform():
    assert re.search(r"RLIMIT_AS[^.]*Linux", README), "say that RLIMIT_AS is Linux only"
    assert "macOS ignores" in README and "256 MB" in README
    import learn.grader as grader
    header = (ROOT / "learn" / "grader.py").read_text(encoding="utf-8").split("SAFE_BUILTINS", 1)[0]
    assert "Linux-only" in header and "256 MB" in header and grader.ALLOCATION_LIMIT == 256 * 1024 * 1024
