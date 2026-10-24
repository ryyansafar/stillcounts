"""A small, tested client for the Devin v3 API. Never logs or raises with the token."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import requests

BASE = "https://api.devin.ai/v3/organizations"
TERMINAL = {"exit", "error", "suspended"}


class DevinError(RuntimeError):
    pass


def load_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    token = values.get("DEVIN_TOKEN", "")
    half = len(token) // 2
    if token and half and token[:half] == token[half:]:
        raise DevinError("DEVIN_TOKEN looks pasted twice; a doubled token returns 403 on every call")
    return values


class DevinClient:
    def __init__(self, token: str, org: str, http: Any | None = None):
        self._token = token
        self._org = org
        self._http = http or requests

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{BASE}/{self._org}{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        resp = self._http.request(method, url, headers=headers, json=body, timeout=30)
        if resp.status_code >= 400:
            detail = str(resp.text)[:300].replace(self._token, "***")
            raise DevinError(f"{method} {path} failed with HTTP {resp.status_code}: {detail}")
        return resp.json()

    def create_session(self, prompt: str, *, title: str | None = None, tags: list[str] | None = None,
                       max_acu_limit: int = 3, playbook_id: str | None = None,
                       structured_output_schema: dict | None = None) -> dict:
        body: dict[str, Any] = {"prompt": prompt, "max_acu_limit": max_acu_limit}
        for key, val in (("title", title), ("tags", tags), ("playbook_id", playbook_id),
                         ("structured_output_schema", structured_output_schema)):
            if val is not None:
                body[key] = val
        return self._call("POST", "/sessions", body)

    def get_session(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}")

    def message(self, session_id: str, text: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/messages", {"message": text})

    def create_playbook(self, title: str, body: str, macro: str, schema: dict | None = None) -> dict:
        payload: dict[str, Any] = {"title": title, "body": body, "macro": macro}
        if schema is not None:
            payload["structured_output_schema"] = schema
        return self._call("POST", "/playbooks", payload)

    def poll(self, session_id: str, *, interval: float = 10, timeout: float = 600,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
        start = clock()
        while True:
            data = self.get_session(session_id)
            if data.get("status") in TERMINAL or data.get("status_detail") in {"waiting_for_user", "finished"}:
                return data
            if clock() - start >= timeout:
                raise DevinError(f"session {session_id} did not finish within {timeout} s")
            sleep(interval)
