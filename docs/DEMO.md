# Demo script: 3 minutes

Pitch in one line: **a field-team ledger for any disaster. It keeps counting with no signal, and when signal returns every phone agrees.** The Kochi relief camp is the worked example, not the limit: floods, earthquakes, clinics, food banks, anywhere a team counts stock on phones with bad connectivity.

Track: *Works anyway* (unreliable internet). Lead with the ledger and the proof. Learn and Race are one closing slide: how we made the invisible merge visible.

## Before you go on (2 minutes, off stage)

```bash
scripts/run.sh                                   # base laptop, port 8000
# Optional: let phones on the room's WiFi submit code in Learn. Off by default because it runs their code on this laptop.
# STILLCOUNTS_LEARN_WRITE_LAN=1 scripts/run.sh
```
Open `http://<laptop-ip>:8000/board` on the projector. Two phones scan the QR code and open the app. Press **Load starting stock (seeded)**. For the phone push in beats 4 and 5, export `NTFY_TOPIC=<your topic>` in the shell before `scripts/run.sh` (the scripts read the environment, not `.env`); without it the guard prints to the terminal instead.

## Beats

**0:00 Problem (20 s).** "A team counts blankets, rice and medicine. The signal drops. Most apps lose the counts or overwrite each other."

**0:20 Go offline (30 s).** Put both phones in airplane mode. On phone A tap Blankets +10, then -1. On phone B tap Blankets -10 and type a note. Show both pills: `offline · N queued`. The counts are on the phones, not lost.

**0:50 Reconnect (30 s).** Turn WiFi back on. The pills turn to `online · synced`. On the board the headline reads *Every phone holds every operation* and the tile `phones caught up` shows 2 / 2. Say what this is: each phone reports how many operations it holds; it is a live indicator, the proof comes next.

**1:20 Conflict and oversell (30 s).** Both phones set a different note on Rice while offline, then reconnect. Show `conflicts merged` go up and both phones show the same winner (Lamport time, never the phone's wall clock). Then issue more water than counted in on two phones: the board says *1 item(s) need a human* and, with `NTFY_TOPIC` set, the push arrives. We flag it, we do not hide it.

**1:50 The proof (40 s).** Board, **Attack** tab. "This is a deterministic simulation of 24 hostile scenarios: dropped, duplicated and reordered requests, server crashes, torn log lines, two tabs on one phone, clock jumps." Read the committed summary honestly: how many scenarios pass, which two still fail (README, Chaos simulation). These are simulated phones, not the ones in the room. Then break the referee on purpose:

```bash
.venv/bin/python -m ops.oracle_guard lww-picks-earliest
```
The referee catches the broken merge and pushes an alert (or prints it without `NTFY_TOPIC`). "14 of 14 deliberately broken merges are caught" is on the board panel.

**2:30 Devin (20 s).** `docs/devin.md`: 9 sessions, 5 merged pull requests, and the work we overrode (the Learn grader accepted `import os`; we rewrote it). Agents attacked the product; they never edit the referee.

**2:50 One closing slide (10 s).** Learn and Race: "Earn the Code" unlocks the real merge code by predicting, finding a bug and writing a function; Referee Race lets a room race to predict the merged stock. Then stop.

## If something fails

- No WiFi at all: the laptop can host its own hotspot; the phones only need to reach the board address.
- A phone shows `PHONE STORAGE FULL`: it is telling the truth; reload after it syncs.
- Fallback: `docs/screenshots/` has the phone, board, attack, race and learn screens; the CSV export (`/api/export.csv`) is the paper fallback.
- No recording of the demo is committed: record a 60-second phone video yourself before the event if you want a backup.
