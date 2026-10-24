# Demo script: 3 minutes

The one line: **AI can build an app in minutes, but people cannot explain what it built. Here the AI builds one stage, stops, and only goes on when you prove you understand it. It also notices when you are stressed and makes the steps easier.** Be straight about it if asked: the merge code and its JavaScript twin are public, so nothing is secret. What is gated is the guided path, the reveal of the reference code and your unlock record.

Say plainly that the build you watch is a **labelled replay** unless you configured a Devin token (see the end). Every simulated line says so on screen.

## Before you go on (off stage, about 2 minutes)

```bash
scripts/run.sh          # prints the board link with this run's join token; the root page is the chat
# or, for a quick look on your own laptop with no token:
STILLCOUNTS_OPEN=1 scripts/run.sh
```

Open `http://localhost:8000/`. Open **More** in the sidebar once to know where the other pages are (Ledger, Board, Learn, Race, Results). Delete old chats so you start clean.

The **Write** checkpoint runs code on the laptop. A browser on the laptop itself is always allowed. A phone or another machine gets HTTP 403 unless you start the server with `STILLCOUNTS_LEARN_WRITE_LAN=1`; code a learner types then runs on the demo laptop in the sandbox (defence in depth, not a security boundary), so use it only on a network you trust, such as your own hotspot, never on venue Wi-Fi with strangers.

No internet and no Devin call are needed: the built-in course and its replay run from the laptop.

## The path (about 3 minutes)

1. **New chat, build, stop (0:00).** Press "Try the built-in demo" (or type what to build with the offline replay toggle on). A plan of four stages appears and stage 1, "Counted once, even when sent twice", is built. The status line reads "Devin has stopped. Earn stage 1 to let it go on." The code panel says **Code locked**. Point at the label: this is a simulated replay.
2. **Fail a checkpoint (0:30).** In **Predict**, type a wrong number for each case and press Check. The chat names which cases are wrong, gives a hint, and says Devin stays stopped. It does not show the answer, and the wrong cases are replaced by new numbers. The code is still locked and stage 2 is not started.
3. **Pass it (1:00).** Answer the new cases (the real merge decides). Then **Find the bug**: click the changed line in the short excerpt and pick the cause; a wrong pick locks the options for 30 seconds and reshuffles them. Then **Write** `dedupe`. When the third passes, the code **unlocks with a highlighted diff** and Devin is told to continue with stage 2; it builds it and stops again. **Explain** is practice: real sentences, shown against a hidden rubric, and it never unlocks anything alone.
4. **Heart rate (2:00).** Click the heart item in the top bar. Press **Sudden spike**. The thread says "HR 96 vs baseline 72: switching to guided answers and a slower pace" and the difficulty drops (stage 2's checkpoint now shows hints and one case at a time; at level 1 write is guided fill-in or multiple choice, with a breathing prompt). Nothing is passed for you. Say that the reading stays on the device.
5. **Calm (2:30).** Press **Calm**. The level is restored one step at a time, each with a line in the thread (about 20 seconds per step).
6. **Offline (2:45).** Turn the network off (airplane mode, or the browser's offline switch) and reload: the chat, its history and the replay still open from the service worker's cache. Be honest that grading itself needs the base laptop: an answer submitted with the base unreachable says it needs a signal and is kept in the box. The Learn page (`/static/learn.html`) is where predict still works with no signal; that pass is provisional until the server re-checks it.

Optional close: `/race` on two phones (same operations, shuffled differently on each, faster correct answers rank higher), and `docs/devin.md` for what Devin did and what a human rewrote. Do not quote the Results numbers as a study.

## Live Devin (optional, not required for the demo)

With `DEVIN_TOKEN` and `DEVIN_ORG` set (or `STILLCOUNTS_DEVIN_ENV` pointing at an env file), a prompt starts a real Devin session and the thread shows its status, plan, log and pull requests as they arrive (the chat then says LIVE DEVIN). It was not exercised against a real session in the final integration run, so rehearse it before relying on it. On any error, usage limit or missing token the chat says why in the thread and switches to the labelled replay.

## If something fails

- The chat says "Live Devin is not available" and switches to the replay: expected without a token.
- A checkpoint says "Needs a signal": the base is unreachable. Reconnect.
- Write says it is graded on the base laptop only (HTTP 403): you are on a phone and started the server without `STILLCOUNTS_LEARN_WRITE_LAN=1`. Restart with it, or use the laptop's own browser. Progress is kept.
- A phone shows `join link needed`: it did not scan this run's QR code from the board. Scan again, or start with `STILLCOUNTS_OPEN=1` on a network you trust.
- HTTPS for installing the offline app on phones (browsers need `https://` or `localhost`); this has not been tried on real demo phones, so rehearse it:
  ```bash
  brew install mkcert && mkcert -install
  mkcert <laptop-ip> localhost
  STILLCOUNTS_TLS_CERT=<laptop-ip>+1.pem STILLCOUNTS_TLS_KEY=<laptop-ip>+1-key.pem scripts/run.sh
  ```
  Each phone must trust mkcert's root certificate (`mkcert -CAROOT` shows where it is).
- Fallbacks: `docs/screenshots/` has the chat at each step (empty, stopped, failed checkpoint, bug hunt, unlocked diff, heart-rate spike and calm, offline reload, mobile) and the other pages. No demo video is committed.
