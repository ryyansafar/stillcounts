"""An offline queue: messages wait on disk when the network is down and flush, in order, when it returns."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


class Outbox:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _read(self) -> list[dict]:
        if not self._path.exists():
            return []
        items = []
        for line in self._path.read_text().splitlines():
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a corrupt line must not block the rest
        return items

    def _write(self, items: list[dict]) -> None:
        self._path.write_text("".join(json.dumps(i) + "\n" for i in items))

    def enqueue(self, item: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as fh:
            fh.write(json.dumps(item) + "\n")

    def pending(self) -> int:
        return len(self._read())

    def flush(self, send: Callable[[dict], None]) -> tuple[int, int]:
        """Send in order; stop at the first failure. Returns (sent, still_waiting)."""
        items = self._read()
        sent = 0
        for item in items:
            try:
                send(item)
            except Exception:
                break
            sent += 1
        self._write(items[sent:])
        return sent, len(items) - sent
