"""Runs the referee against the merge (optionally a deliberately broken copy). On failure it pushes a phone notification
and can start a Devin session to investigate. Used for the 'break it on purpose' demo beat."""
from __future__ import annotations

import sys
from typing import Callable

from oracle.mutation import MUTANTS, SOURCE, _load, referee_failure

def guard(mutant: str | None, notify: Callable[[str, str], None],
          start_session: Callable[[str], str] | None = None) -> dict:
    source = SOURCE.read_text()
    if mutant:
        spec = next(m for m in MUTANTS if m["name"] == mutant)
        source = source.replace(spec["old"], spec["new"], 1)
    reason = referee_failure(_load(source))
    if reason is None:
        return {"ok": True}
    message = f"Referee failed (merge variant: {mutant or 'none'}): {reason}"
    notify("Merge broke the referee", message)
    session = None
    if start_session:
        session = start_session(f"The referee failed with the merge variant '{mutant}': {reason}. "
                                "Run `python -m oracle.mutation` and the tests, find the cause in stillcounts/crdt.py, "
                                "write a failing test first, then a minimal fix as a pull request. Do not edit oracle/.")
    return {"ok": False, "error": reason, "session": session}


def main() -> None:
    import os
    from stillcounts.ntfy import notify
    topic = os.environ.get("NTFY_TOPIC", "")
    send = (lambda t, m: notify(topic, t, m, priority=5, tags=["rotating_light"])) if topic else (lambda t, m: print(t, m))
    print(guard(sys.argv[1] if len(sys.argv) > 1 else None, send))


if __name__ == "__main__":
    main()
