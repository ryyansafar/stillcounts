"""Command line: `python -m chaos.run --seeds 0:500 --scenario all`. One JSON line per run on stdout, and a summary
in data/chaos_results.json for the board."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from chaos.scenarios import SCENARIOS, run_one

ROOT = Path(__file__).resolve().parent.parent
SUMMARY_KEYS = ("seed", "scenario", "ok", "ops", "conflicts", "lost", "label")


def parse_seeds(text: str) -> list[int]:
    if ":" in text:
        a, b = text.split(":", 1)
        return list(range(int(a, 0), int(b, 0)))
    return [int(s, 0) for s in text.split(",")]


def summarise(runs: list[dict], seeds: list[int]) -> dict:
    """The small committed summary (data/chaos_summary.json): per-scenario run, failure and lost-op counts."""
    by: dict[str, dict] = {}
    for r in runs:
        row = by.setdefault(r["scenario"], {"runs": 0, "failed": 0, "lost": 0})
        row["runs"] += 1
        row["failed"] += 0 if r["ok"] else 1
        row["lost"] += r["lost"]
    return {"note": "Summary of a real run: python -m chaos.run --seeds %d:%d --summary data/chaos_summary.json. Full per-run "
                    "output is regenerated locally and git-ignored." % (min(seeds), max(seeds) + 1),
            "seeds": [min(seeds), max(seeds) + 1], "runs": len(runs), "failed": sum(v["failed"] for v in by.values()),
            "lost_ops": sum(v["lost"] for v in by.values()), "by_scenario": by}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Deterministic chaos simulation of the Still Counts sync")
    p.add_argument("--seeds", default="0:20", help="a range a:b (b excluded) or a comma list")
    p.add_argument("--seed", type=lambda s: int(s, 0), help="a single seed (overrides --seeds)")
    p.add_argument("--scenario", default="all", help="'all', a name, or a comma list")
    p.add_argument("--out", default=str(ROOT / "data" / "chaos_results.json"))
    p.add_argument("--summary", help="also write the small per-scenario summary to this path")
    p.add_argument("--trace", action="store_true", help="include the event trace in each JSON line")
    p.add_argument("--list", action="store_true")
    a = p.parse_args(argv)
    if a.list:
        for name, (_, scout, title) in SCENARIOS.items():
            print(f"{name:36} {scout:20} {title}")
        return 0
    names = list(SCENARIOS) if a.scenario == "all" else a.scenario.split(",")
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        p.error(f"unknown scenario(s): {', '.join(unknown)}")
    seeds = [a.seed] if a.seed is not None else parse_seeds(a.seeds)
    runs, started = [], time.time()
    for seed in seeds:
        for name in names:
            try:
                r = run_one(seed, name, trace=a.trace)
            except Exception as exc:                       # a harness or product crash is a failed run too
                r = {"seed": seed, "scenario": name, "ok": False, "ops": 0, "conflicts": 0, "lost": 0,
                     "label": "harness exception", "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(r, ensure_ascii=True), flush=True)
            runs.append({k: r[k] for k in SUMMARY_KEYS})
    if a.summary:
        Path(a.summary).write_text(json.dumps(summarise(runs, seeds), indent=1) + "\n")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"runs": runs}, ensure_ascii=True, indent=0))
    bad = sum(1 for r in runs if not r["ok"])
    print(json.dumps({"summary": True, "runs": len(runs), "failed": bad, "ops": sum(r["ops"] for r in runs),
                      "lost": sum(r["lost"] for r in runs), "seconds": round(time.time() - started, 1)}),
          file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
