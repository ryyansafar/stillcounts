# Earn the Code: build spec

This is the single source of truth for building the project. Read all of it before changing anything.

## One-sentence pitch
AI can now build an app in minutes, but people cannot explain what it built. Earn the Code is a chat app where an AI builds one stage at a time and **stops until you prove you understand it**. It also notices when you are stressed (heart rate) and makes the steps easier.

## What the user sees (plain chatbot, nothing fancy)
- A normal chat screen like ChatGPT or Claude: left sidebar (New chat, chat list, quiet footer links), one centred message column, a composer at the bottom. One neutral dark theme, one font, one muted accent. No glow, no coloured card borders, no big banners, no marketing copy.
- The user types what to build. Devin (or a clearly labelled replay of a past real Devin session) posts a plan of 3 to 4 stages and builds stage 1, then **stops**.
- The stage's code is locked. A checkpoint appears as an ordinary assistant message: **Predict** (what does this return), **Find the bug**, **Write** (one small function), **Explain** (in your own words, shown against a hidden rubric, never decides the unlock alone).
- Pass: the code unlocks with a highlighted diff, and the build continues. Fail: a hint and a retry; the build stays stopped.
- A small heart-rate item in the top bar (simulator slider 40 to 190 bpm, Spike, Calm, Calibrate buttons in a popover). A sudden rise against the baseline lowers the difficulty one level and the thread says why ("HR 112 vs baseline 72: switching to easier steps"). Levels: 3 normal, 2 hint plus smaller steps, 1 guided fill-in or multiple choice plus a breathing prompt. It never auto-passes anything. Heart rate stays on the device.
- Course 1 is the offline ledger in this repo (an operation-log merge with Lamport clocks): it is the real code the learner earns. The older pages (Ledger, Board, Race, Results) stay reachable from the footer.
- Everything simulated or replayed is labelled with one small plain line, never hidden.

## Must be true (acceptance criteria)
1. `./scripts/run.sh` starts the app; the root page is the chat. `./scripts/run.sh test` is green (Python and JS). CI is green on every PR.
2. The demo path works with no internet and no Devin call (built-in course, replay labelled SIMULATED): new chat, build, stop, fail a checkpoint (stays locked, hint), pass it (unlock plus diff, build continues), spike the heart rate (level drops with the reason, easier variant), calm (gradual restore).
3. Grading is by plain code for Predict, Find the bug and Write. Learner code runs in the sandbox (bare namespace, no imports, builtin whitelist, rlimits, 5 s timeout, 256 MB allocation guard). The exact escape probe `json.codecs.sys.modules['os']` stays a regression test.
4. The server decides unlocks. A pass is recorded per learner; the browser cannot unlock a stage. Learner identity is a server-issued random id in an HttpOnly cookie, not a client header.
5. Honest copy: the JS and Python reference code are public; what is gated is the guided path, the reference-solution reveal and the unlock record. Do not call the algorithm secret.
6. The optional live Devin bridge (`server/devin_bridge.py`, `client/devin_live.js`) works when a token is configured and falls back to the labelled replay on any error. The token never reaches the browser.
7. No secrets in the repo. README, CONTRIBUTING and `docs/devin.md` match the code, and keep the socials block (portfolio ryyansafar.site first, then github.com/ryyansafar, buymeacoffee.com/ryyansafar, paypal.me/ryyansafar, razorpay.me/@ryyansafar).

## Rules
- Never edit `oracle/`, `stillcounts/crdt.py` or `.github/CODEOWNERS`.
- Tests first. Small conventional commits. No `--no-verify`. One pull request per job, with a clear description. Run `.venv/bin/python -m pytest -q` and `node --test client/test/` before you push.
- Do not commit generated or live data files (`data/ops.jsonl*`, `data/chaos_results.json`).

## Known issues found by blind review (fix if still present)
- Predict: a wrong guess must not reveal the answer; cases are per-learner and graded by the server when online; offline passes are provisional until the server re-checks them.
- Find the bug: show a short excerpt, require clicking the changed line, lock options after a wrong pick.
- Explain: needs real sentences, never unlocks alone.
- `learn.js`: explanation not shown after a correct bug pick (reads `res.explanation`, API nests it under `res.result`); the failure counterexample is hidden behind a generic error string.
- The write checkpoint returns 403 to phones unless `STILLCOUNTS_LEARN_WRITE_LAN=1`: document the flag in `docs/DEMO.md`.
- README says 24 scenarios, the board data says 25: reconcile.

## Branches that already hold work (merge, do not redo)
`origin/pivot/frontdoor`, `origin/pivot/story`, `origin/pivot/consolidate`, `origin/pivot/chatshell` (plain chat UI, heart-rate engine, live bridge, built-in course). They overlap in `client/` and `server/`; reconcile them by reading the code.
