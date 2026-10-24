"""Phone notifications through ntfy (free push, no account). Topics are public: never put secrets in a message."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

BASE = "https://ntfy.sh"
MAX_BYTES = 4096
MIN_TOPIC_LEN = 12
_SECRET = re.compile(r"(cog_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|apk_[A-Za-z0-9]{20,})")


class NtfyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Action:
    kind: str    # view | http | broadcast | copy
    label: str
    url: str
    method: str | None = None

    def render(self) -> str:
        parts = [self.kind, self.label, self.url]
        if self.method:
            parts.append(f"method={self.method}")
        return ", ".join(parts)


def build_headers(title: str, priority: int = 3, tags: list[str] | None = None,
                  actions: list[Action] | None = None) -> dict[str, str]:
    if not 1 <= priority <= 5:
        raise ValueError("priority must be between 1 and 5")
    if actions and len(actions) > 3:
        raise ValueError("ntfy allows at most 3 action buttons")
    headers = {"Title": title, "Priority": str(priority)}
    if tags:
        headers["Tags"] = ",".join(tags)
    if actions:
        headers["Actions"] = "; ".join(a.render() for a in actions)
    return headers


def notify(topic: str, title: str, message: str, *, priority: int = 3, tags: list[str] | None = None,
           actions: list[Action] | None = None, http: Any = requests, base: str = BASE) -> None:
    if len(topic) < MIN_TOPIC_LEN:
        raise ValueError("topic is too short to be unguessable; use a long random topic")
    if _SECRET.search(title) or _SECRET.search(message):
        raise ValueError("message looks like it contains a secret; ntfy topics are public")
    body = message.encode("utf-8")[:MAX_BYTES]
    resp = http.post(f"{base}/{topic}", data=body, headers=build_headers(title, priority, tags, actions), timeout=10)
    if resp.status_code >= 400:
        raise NtfyError(f"ntfy returned HTTP {resp.status_code}: {str(resp.text)[:120]}")
