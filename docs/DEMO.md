# Demo script: 3 minutes, four beats

The one line to remember: **a field-team ledger for any disaster. It keeps counting with no signal, and when signal returns every phone agrees.** A Kochi relief camp is the worked example, not the limit: floods, earthquakes, a clinic after a storm, a food bank.

Track: *Works anyway* (unreliable internet). Everything after the four beats (Devin, Learn, Race) goes on one closing slide, not into the talk.

## Before you go on (about 5 minutes, off stage)

```bash
scripts/run.sh          # prints the board link with this run's join token; open THAT link on the laptop
```

1. Open the printed `http://localhost:8000/board?t=...` link on the projector. The QR code on it carries the join token; phones that scan it can write, anything else on the venue WiFi gets a 401. (`STILLCOUNTS_OPEN=1 scripts/run.sh` turns the token off for an isolated network.)
2. Two phones scan the QR code and open the app. Press **Load starting stock (seeded)** on the board.
3. **HTTPS for the phones (do this, or the airplane-mode beat is fragile).** Browsers only install the offline app on `https://` or `localhost`. Over plain `http://<laptop-ip>:8000` the page keeps working only while its tab stays open: a reload or a tab the phone killed in airplane mode shows a browser error. The phone app says so at the bottom ("app not saved for offline use yet"). To fix it:
   ```bash
   brew install mkcert && mkcert -install
   mkcert <laptop-ip> localhost            # makes <laptop-ip>+1.pem and <laptop-ip>+1-key.pem
   STILLCOUNTS_TLS_CERT=<laptop-ip>+1.pem STILLCOUNTS_TLS_KEY=<laptop-ip>+1-key.pem scripts/run.sh
   ```
   Each demo phone must trust mkcert's root certificate (`mkcert -CAROOT` shows where it is; iOS needs it installed as a profile and switched on under Certificate Trust Settings, Android needs it installed as a CA certificate). Then open `https://<laptop-ip>:8000/`, load it once with signal, and confirm the footer no longer shows the "not saved" warning. **This has not been tried on the real demo phones yet: rehearse it.**
4. Optional push alerts: `export NTFY_TOPIC=<topic>` before `scripts/run.sh`. ntfy.sh needs the internet, so the board's own flash is the alert; the push is a bonus.

## Beats

**0:00 Offline taps (50 s).** "A team counts blankets, rice and medicine. The signal drops." Put both phones in airplane mode. Phone A: Blankets +10 then -1. Phone B: Blankets -10, and type a note. Both pills read `offline · N queued`, and the cards show *not yet sent to the base*. The counts live on the phones. Made a wrong tap? **Undo my last tap** adds the opposite adjustment; nothing is edited.

**0:50 Reconnect and agree (40 s).** Turn WiFi back on. The pills turn to `online · synced`, the board headline reads *Every phone holds every operation* and `phones caught up` shows 2 / 2. Say what that is: each phone reports how many operations it holds, so it is a live indicator, not the proof. Both phones now show the same numbers because the ledger is a function of the set of operations, not of the order they arrived in.

**1:30 Oversell (40 s).** Issue more water than counted in, on both phones, while offline, then reconnect. The board headline flashes red: *1 item needs a human*, and the row sorts to the top. We flag it, we do not hide it. (With `NTFY_TOPIC` and internet, a push also arrives. Do not make that the payoff.)

**2:10 Attack tab (50 s).** Board, **Attack** tab. "A simulation of 24 hostile scenarios, replayed from 500 seeds each: dropped, duplicated and reordered requests, a server killed mid-write, a phone clock that jumps, two tabs on one phone." Read the headline as it is on screen (how many still fail) and name the open ones in plain words. These are simulated phones, not the ones in the room. Then open the **Proof** tab and press **Break the referee on purpose**: the referee catches a merge that picks the earliest writer.

Stop there. The closing slide is one line each: Devin scouted and scaffolded (`docs/devin.md` says exactly what it did and what we overrode); Learn and Race make the merge visible.

## If something fails

- No WiFi at all: the laptop can host its own hotspot; phones only need to reach the board address.
- A phone shows `join link needed`: it did not scan this run's QR code. Scan it again.
- A phone shows `PHONE STORAGE FULL`: it is telling the truth; reload after it syncs.
- No venue WiFi and no hotspot: the **Phone to phone** panel in the app merges two phones by copy and paste, with no base at all.
- Fallbacks: `docs/screenshots/` has the phone, board, attack, proof and learn screens; `/api/export.csv` is the paper fallback.
- No demo video is committed. Record a 60-second two-phone airplane-mode clip yourself before the event and keep it on the laptop.
