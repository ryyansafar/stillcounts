# Devin evidence

Generated from `data/devin_evidence.json` by `python -m ops.devin_evidence`. Do not edit by hand. Session and pull request facts were checked against GitHub (PRs, issues, branches) and the local .sessions*.json launch files.

**9 sessions, 5 pull requests (all merged by a human), 5 issues filed.**

| Role | Kind | Session | Outcome | Links |
|---|---|---|---|---|
| SCOUT-NETWORK | read-only scout | [551fbd32](https://app.devin.ai/sessions/551fbd3284f14211bbd5bae9b6780529) | Proposed 8 hostile network scenarios (structured output saved in docs/scout_scenarios.json). No code, no PR. | none |
| SCOUT-IDS-CLOCKS | read-only scout | [f45df954](https://app.devin.ai/sessions/f45df95472c54203984b4e2bdae67af9) | Proposed 8 scenarios about ids, Lamport values and text (docs/scout_scenarios.json). No code, no PR. | none |
| SCOUT-APP-LIFECYCLE | read-only scout | [2bba2ee0](https://app.devin.ai/sessions/2bba2ee01718488d9eb27e2e5308c7d2) | Proposed 8 scenarios about tabs, storage, restarts and clocks (docs/scout_scenarios.json). No code, no PR. | none |
| WRITER-chaos-sim | writer | [140578aa](https://app.devin.ai/sessions/140578aa9dab4f60a205cbd33a937fa4) | Built chaos/, a deterministic simulation of the 24 scout scenarios, with regression tests. Its runs showed 8 scenarios failing. The playbook asked for a separate fix pull request per failure; none was opened, so the failures were pinned as strict expected failures. | [PR #1](https://github.com/ryyansafar/stillcounts/pull/1) (merged) |
| LEARN-CONTENT | writer | [188bd5c0](https://app.devin.ai/sessions/188bd5c02d94454bb0e7454e2cf36aa7) | Four Learn stages and learn/grader.py. Merged, then partly overridden: its write grader accepted `import os`, so the sandbox was rewritten by hand (see Rejected or overridden work). | [PR #5](https://github.com/ryyansafar/stillcounts/pull/5) (merged) |
| LEARN-API | writer | [b07e7de9](https://app.devin.ai/sessions/b07e7de909c548838985ef0ece21ee3d) | server/learn_api.py: locked code, per-learner progress, capped and rate-limited writes. Merged. | [PR #2](https://github.com/ryyansafar/stillcounts/pull/2) (merged) |
| LEARN-UI | writer | [3011bd74](https://app.devin.ai/sessions/3011bd74d3e74b43980c02cce89f9a14) | client/learn.html, learn.js, learn.css. Merged. | [PR #3](https://github.com/ryyansafar/stillcounts/pull/3) (merged) |
| PLAY-RACE | writer | [b62f854c](https://app.devin.ai/sessions/b62f854c22854c36a5e8393e1bef45bc) | Referee Race: server/race_api.py, client/race.*, board tab. Merged. | [PR #4](https://github.com/ryyansafar/stillcounts/pull/4) (merged) |
| QA-RECORD | browser QA | [64a5017f](https://app.devin.ai/sessions/64a5017f4b964c65b808ac4ac157e6cc) | Drove the app in a browser, filed 5 issues with screenshots and pushed branch devin/qa-fixes (3 fix commits and a screenshots commit). It opened no pull request and wrote no docs/QA.md. Two of the three fixes were cherry-picked by hand; the screenshots stay on the branch because the issues link to them. | [issue #6](https://github.com/ryyansafar/stillcounts/issues/6), [issue #7](https://github.com/ryyansafar/stillcounts/issues/7), [issue #8](https://github.com/ryyansafar/stillcounts/issues/8), [issue #9](https://github.com/ryyansafar/stillcounts/issues/9), [issue #10](https://github.com/ryyansafar/stillcounts/issues/10) |

## ACU use

Not recorded. The Devin API was not queried when this page was written, so no ACU figure is claimed. data/devin_counts.json (a live snapshot, git-ignored) had reported 0.0, which means 'not reported', not zero. Each session was launched with an ACU cap: 3 by default (ops/devin_client.py) and 5 for the sessions started by ops/launch_extra.py. Run `python -m ops.devin_counts <env file> 30` with a Devin token to refresh the live panel.

## Rejected or overridden work

- LEARN-CONTENT's write grader (PR #5) only blocked imports of the project's own modules. A submitted function that imported os could write a file on the host. It was replaced with an AST check, a builtins whitelist and resource limits (tests/test_learn_sandbox.py), and the write endpoint is closed to the LAN by default.
- QA-RECORD's branch devin/qa-fixes was never turned into a pull request. Its client fix for two tabs and for half-typed notes was cherry-picked, then changed: a tab now takes over only operations it has never seen, so a stale record cannot undo a resync.
- QA-RECORD's third fix commit (install segno in run.sh and CI) was not cherry-picked because main already had the CI fix; run.sh was fixed separately.
- WRITER-chaos-sim reported 8 failing scenarios but opened no fix pull requests. Five are now fixed by hand, one is down to 4 of 500 seeds, and two remain open (see README).
- No Devin pull request was rejected outright; all five were merged by a human pressing merge.

## What Devin did not do

- It did not write the merge function (`stillcounts/crdt.py`) or the referee (`oracle/`); those are never edited by agents (`.github/CODEOWNERS`).
- The three scouts are read-only: they proposed scenarios and changed nothing.
- No session opened a pull request that fixes a bug the harness found.
