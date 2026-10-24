# Demo script: 3 minutes, two beats and a close

The one line: **people paste AI code they do not understand. Earn the Code makes that gap visible, then closes it: code stays locked until you earn it, and it still works with the Wi-Fi pulled.**

Track fit: primary Track 2 (make the invisible visible), secondary Track 3 (works anyway), tertiary Track 4 (play).

Check `README.md` first: the Understanding Map, gap score, fading help and proof card are marked in progress there. Only show on stage what is on screen and working. If the map is not on main yet, beat 1 uses the per-stage lock state, which is real today.

## Before you go on (off stage, about 5 minutes)

```bash
scripts/run.sh          # prints the board link with this run's join token
```

1. Open the printed `http://localhost:8000/board?t=...` link on the projector. The QR code on it carries the join token.
2. A volunteer who is **not a coder** gets a phone. Scan the QR code, open the Learn link, and let the lessons load once with signal (they are cached after the first visit).
3. HTTPS is needed on the phone for the offline beat. Browsers install the offline app only on `https://` or `localhost`. Set it up as below, load the Learn page once with signal, and confirm the "not saved for offline use" warning is gone. **This has not been tried on the real demo phones yet: rehearse it.**
   ```bash
   brew install mkcert && mkcert -install
   mkcert <laptop-ip> localhost
   STILLCOUNTS_TLS_CERT=<laptop-ip>+1.pem STILLCOUNTS_TLS_KEY=<laptop-ip>+1-key.pem scripts/run.sh
   ```
   Each phone must trust mkcert's root certificate (`mkcert -CAROOT` shows where it is).
4. Rehearse once with a throwaway learner so the volunteer's progress starts clean.

## Beat 1: the invisible gap (0:00 to 1:40)

1. Say: "Devin wrote this app's merge code. Raise a hand if you could explain it." Show the pasted-code state: every stage locked, the real code hidden. This is what understanding looks like when you have only pasted: nothing earned. (If the Understanding Map is on main, show it here: every function unseen, gap score low. If it is not, say the map is the next step and show the lock state instead.)
2. Hand the volunteer stage 1, **Counted once, even when sent twice**. **Predict**: two phones, one signal drop, one operation sent twice. What is the blanket count? They answer; the real merge runs and checks them. Show that a wrong guess is graded by running the code, not by anyone's opinion.
3. **Find the bug**: the same code with one line changed. They pick what is wrong from four statements. The grader is plain code.
4. Say where it goes next: **Write** (a small function, judged by the same referee that tests the real app) and **Explain** (keyword-matched against a rubric shown after, practice only). Passing predict, find the bug and write unlocks the real code.

## Beat 2: offline (1:40 to 2:30)

1. Pull the Wi-Fi: airplane mode on the volunteer's phone.
2. The volunteer does another stage's **Predict** checkpoint. It is graded in the browser with the same merge, so it works. Show the pill or banner as the phone reports it.
3. Say which parts do need the base: find the bug, write and explain go through `/api/learn`. Do not hide that. If the phone shows a warning that it is offline, read it out.
4. Turn Wi-Fi back on. Progress is on the base laptop.

## Close (2:30 to 3:00)

- **Play**: two phones open `/race` (QR code on the board's Race tab). Same operations, shuffled differently on each phone; first to the right merged stock wins.
- **Devin, honestly**: `docs/devin.md` has the table. 9 cloud sessions, 5 pull requests, all merged by a human; a 2.3 MB file was replaced, a sandbox that allowed `import os` was rewritten by hand, and the cloud quota ran out. Later work is labelled Devin CLI, local.
- Do not quote the quiz as a result. A few volunteers is a demo, not a study.

## If something fails

- No venue Wi-Fi: the laptop can host a hotspot; phones only need to reach the base address.
- A phone shows `join link needed`: it did not scan this run's QR code. Scan again.
- Lessons show "Connect once to download them": the phone never loaded them with signal. Reconnect and open the Learn page once.
- Write checkpoint says it is not allowed from this device: by default only the base laptop's own browser may use it (`STILLCOUNTS_LEARN_WRITE_LAN=1` changes that).
- Fallbacks: `docs/screenshots/` has the phone, board, attack and learn screens. No demo video is committed; record a 60-second airplane-mode clip yourself before the event.
