"""The Devin bridge: the browser talks to /api/devin, this module talks to the Devin v3 API. The token (DEVIN_TOKEN and
DEVIN_ORG, from the environment or the file named by STILLCOUNTS_DEVIN_ENV) never leaves the server.

The real agent obeys the gate: it works ONE stage, STOPS (status_detail waiting_for_user), and is sent "continue" only after the
learner earned the stage before it. The bridge enforces that itself: a continue that the learner has not earned is refused here,
whatever the browser asks. The code of a stage is withheld until the learner passes predict, find the bug and write."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ops.devin_client import DevinClient, DevinError, load_env
from server import live_course as lc
from server.learn_api import (LEARNER_PATTERN, MAX_ANSWER, MAX_EXPLAIN, MAX_SOURCE_BYTES, WRITE_WINDOW, AnswerIn, RateLimiter,
                              Learner, SourceIn, TextIn, is_remote, remote_write_allowed)

PR_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)$")
LIMIT_DETAILS = {"usage_limit_exceeded", "out_of_credits", "out_of_quota", "no_quota_allocation", "payment_declined", "org_usage_limit_exceeded"}
MESSAGE_REASONS = ("continue", "adapt", "user")
MAX_MESSAGE = 1200
SNAPSHOT_TTL = 2.0
PR_TTL = 30.0


def reply(status: int, detail: str, code: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"detail": detail}
    if code:
        body["code"] = code
    return JSONResponse(body, status_code=status)


def not_yours() -> JSONResponse:
    return reply(403, "this session belongs to another browser", "not_yours")


def mine(rec: dict, learner: str) -> bool:
    """A session is graded only by the browser that started it (the server-issued cookie). Old records without an owner stay open."""
    return not rec.get("learner") or rec["learner"] == learner


def devin_from_env() -> DevinClient | None:
    token, org = os.environ.get("DEVIN_TOKEN"), os.environ.get("DEVIN_ORG")
    path = os.environ.get("STILLCOUNTS_DEVIN_ENV")
    if path and Path(path).is_file() and not (token and org):
        try:
            values = load_env(path)
        except (OSError, DevinError):
            values = {}
        token, org = token or values.get("DEVIN_TOKEN"), org or values.get("DEVIN_ORG")
    return DevinClient(token, org) if token and org else None


class GitHubInfo:
    """CI status and Devin Review comments for a pull request, from the public GitHub API (GITHUB_TOKEN raises the rate limit).
    Every failure degrades to 'no CI reported' / 'no review yet'; nothing here is ever guessed."""

    def __init__(self, http: Any | None = None, token: str | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.http, self.token, self.clock, self.cache = http, token if token is not None else os.environ.get("GITHUB_TOKEN"), clock, {}

    def _get(self, path: str) -> Any:
        http = self.http
        if http is None:
            import requests as http
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "stillcounts-bridge"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = http.get("https://api.github.com" + path, headers=headers, timeout=5)
        if resp.status_code >= 400:
            raise DevinError(f"GitHub HTTP {resp.status_code}", resp.status_code)
        return resp.json()

    def lookup(self, url: str) -> dict:
        hit = self.cache.get(url)
        if hit and self.clock() - hit[0] < PR_TTL:
            return hit[1]
        m = PR_URL.match(url)
        info: dict[str, Any] = {"title": "", "state": "open", "ci": "none", "review": {"state": "none", "comments": 0}}
        if m:
            base = f"/repos/{m[1]}/{m[2]}"
            try:
                pr = self._get(f"{base}/pulls/{m[3]}")
                info["title"] = str(pr.get("title") or "")[:140]
                info["state"] = "merged" if pr.get("merged") else str(pr.get("state") or "open")
                sha = (pr.get("head") or {}).get("sha")
                if sha:
                    runs = self._get(f"{base}/commits/{sha}/check-runs").get("check_runs", [])
                    concl = [r.get("conclusion") for r in runs]
                    if runs:
                        info["ci"] = ("pending" if any(c is None for c in concl) else
                                      "failing" if any(c in ("failure", "timed_out", "cancelled", "action_required") for c in concl) else "passing")
            except Exception:                               # rate limit, private repo, offline: say nothing rather than guess
                pass
            try:
                bots = [x for x in self._get(f"{base}/pulls/{m[3]}/comments") + self._get(f"{base}/pulls/{m[3]}/reviews")
                        if "devin" in str((x.get("user") or {}).get("login", "")).lower()]
                info["review"] = {"state": "reviewed" if bots else "none", "comments": len(bots)}
            except Exception:
                pass
        self.cache[url] = (self.clock(), info)
        return info


class LiveStore:
    """Per-session state, in one JSON file: the learner's progress on each stage and which stages Devin was told to build."""

    def __init__(self, path: Path) -> None:
        self.path, self.lock = Path(path), threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data: dict[str, dict] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, sid: str) -> dict | None:
        return self.data.get(sid)

    def create(self, sid: str, learner: str | None, difficulty: int) -> None:
        with self.lock:
            self.data[sid] = {"learner": learner, "difficulty": difficulty, "progress": {}, "created": time.time(), "playbook": None}
            self._save()

    def progress(self, sid: str, n: int) -> dict[str, bool]:
        saved = ((self.data.get(sid) or {}).get("progress") or {}).get(str(n)) or {}
        return {k: saved.get(k) is True for k in lc.CHECKPOINTS}

    def earned_count(self, sid: str) -> int:
        n = 0
        while all(self.progress(sid, n + 1).get(k) for k in lc.REQUIRED):
            n += 1
        return n

    def mark(self, sid: str, n: int, kind: str) -> dict[str, bool]:
        with self.lock:
            entry = self.data[sid].setdefault("progress", {}).setdefault(str(n), {})
            if not entry.get(kind):
                entry[kind] = True
                self._save()
            return self.progress(sid, n)

    def set_difficulty(self, sid: str, level: int) -> None:
        with self.lock:
            self.data[sid]["difficulty"] = level
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


class SessionBody(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    user_prompt: str = Field(default="", max_length=2000)
    max_acu_limit: int = Field(default=3, ge=1, le=5)
    difficulty: int = Field(default=3, ge=1, le=3)
    structured_output_schema: Any = None             # accepted and ignored: the server owns the schema


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE)
    reason: str = "user"
    n: int | None = None


def devin_router(data_dir: Path, client: DevinClient | None = None, client_factory: Callable[[], DevinClient | None] = devin_from_env,
                 github: GitHubInfo | None = None, clock: Callable[[], float] = time.monotonic) -> APIRouter:
    router = APIRouter(prefix="/api/devin")
    store = LiveStore(Path(data_dir) / "devin_live.json")
    gh = github or GitHubInfo()
    limiter = RateLimiter(clock)
    snaps: dict[str, tuple[float, dict]] = {}
    playbook: dict[str, str | None] = {"id": None, "tried": False}

    def devin() -> DevinClient | None:
        return client or client_factory()

    def failure(exc: Exception) -> JSONResponse:
        status = getattr(exc, "status", None)
        if status in (402, 429):
            return reply(429, "Devin refused: the usage or ACU limit was reached", "quota")
        return reply(502, str(exc)[:300], "api_error")

    def ensure_playbook(d: DevinClient) -> str | None:
        if not playbook["tried"]:
            playbook["tried"] = True
            try:
                playbook["id"] = d.create_playbook(lc.PLAYBOOK_TITLE, lc.PLAYBOOK_BODY, "!earnthecode", lc.SCHEMA).get("playbook_id")
            except Exception:                                   # a missing playbook must not stop the build: the prompt carries the rules
                playbook["id"] = None
        return playbook["id"]

    def known(sid: str) -> dict | None:
        return store.get(sid)

    def stages_of(out: Any) -> tuple[list[dict], list[dict], int]:
        out = out if isinstance(out, dict) else {}
        plan = lc.clean_plan(out.get("plan"))
        stages = sorted((s for s in (lc.clean_stage(x) for x in (out.get("stages") or [])) if s), key=lambda s: s["n"])
        done = out.get("stages_done") if isinstance(out.get("stages_done"), int) else len(stages)
        return plan, [s for s in stages if s["n"] <= max(done, 0)], done

    def snapshot(sid: str) -> dict:
        hit = snaps.get(sid)
        if hit and clock() - hit[0] < SNAPSHOT_TTL:
            return hit[1]
        d = devin()
        s = d.get_session(sid)
        try:
            msgs = d.list_messages(sid)
        except DevinError:
            msgs = []
        plan, stages, _ = stages_of(s.get("structured_output"))
        prs = []
        for i, p in enumerate(s.get("pull_requests") or []):
            url = str((p or {}).get("pr_url") or "")
            if not PR_URL.match(url):
                continue
            info = gh.lookup(url)
            m = re.search(r"stage\s*(\d+)", info["title"], re.I)
            prs.append({"url": url, "title": info["title"] or "Pull request", "state": info["state"] if info["title"] else str((p or {}).get("pr_state") or "open"),
                        "ci": info["ci"], "review": info["review"], "stage": int(m[1]) if m else i + 1})
        views = [lc.public_view(st, store.progress(sid, st["n"]), f"s{st['n']}") | {"n": st["n"]} for st in stages]
        out = {"status": s.get("status"), "status_detail": s.get("status_detail"), "acu_consumed": float(s.get("acus_consumed") or 0),
               "acu_limit": s.get("max_acu_limit") or (known(sid) or {}).get("max_acu"), "url": s.get("url"), "simulated": False,
               "plan": plan, "stages": views,
               "messages": [{"id": str(m.get("event_id") or m.get("id") or i), "source": "user" if m.get("source") == "user" else "devin",
                             "text": str(m.get("message") or m.get("text") or "")[:1500]} for i, m in enumerate(msgs)],
               "pull_requests": prs}
        snaps[sid] = (clock(), out)
        return out

    def stage_raw(sid: str, n: int) -> dict | None:
        _, stages, _ = stages_of(devin().get_session(sid).get("structured_output"))
        return next((s for s in stages if s["n"] == n), None)

    @router.post("/sessions")
    def start(body: SessionBody, learner: Learner):
        d = devin()
        if d is None:
            return reply(503, "this server has no Devin token configured (set DEVIN_TOKEN and DEVIN_ORG)", "no_token")
        user = (body.user_prompt or body.prompt).strip()
        if not user:
            return reply(422, "say what to build")
        try:
            pb = ensure_playbook(d)
            s = d.create_session(lc.build_prompt(user), title="Earn the Code: " + user[:60], tags=["stillcounts", "earn-the-code"],
                                 max_acu_limit=body.max_acu_limit, playbook_id=pb, structured_output_schema=lc.SCHEMA)
        except DevinError as exc:
            return failure(exc)
        sid = str(s.get("session_id") or s.get("devin_id") or "")
        if not sid:
            return reply(502, "Devin did not return a session id", "api_error")
        store.create(sid, learner, body.difficulty)
        store.data[sid]["max_acu"] = body.max_acu_limit
        return {"session_id": sid, "url": s.get("url"), "acu_limit": body.max_acu_limit, "playbook": bool(pb)}

    @router.get("/sessions/{sid}")
    def poll(sid: str):
        if known(sid) is None:
            return reply(410, "unknown session: it was not started through this server")
        try:
            return snapshot(sid)
        except DevinError as exc:
            return failure(exc)

    @router.post("/sessions/{sid}/message")
    def message(sid: str, body: MessageBody, learner: Learner):
        rec = known(sid)
        if rec is None:
            return reply(410, "unknown session")
        if not mine(rec, learner):
            return not_yours()
        if body.reason not in MESSAGE_REASONS:
            return reply(422, "reason must be continue, adapt or user")
        if body.reason == "continue":
            if not isinstance(body.n, int) or body.n < 2:
                return reply(422, "continue needs the stage number")
            if store.earned_count(sid) < body.n - 1:         # no lookahead: stage n is built only after stage n-1 is earned
                return reply(403, "stage " + str(body.n - 1) + " is not earned yet, so Devin is not told to continue", "not_earned")
        if body.reason == "adapt":
            store.set_difficulty(sid, 1 if "stressed" in body.text.lower() else 3)
        try:
            devin().message(sid, body.text)
        except DevinError as exc:
            return failure(exc)
        snaps.pop(sid, None)
        return {"sent": True, "reason": body.reason, "n": body.n}

    @router.post("/sessions/{sid}/stop")
    def stop(sid: str, learner: Learner):
        rec = known(sid)
        if rec is None:
            return reply(410, "unknown session")
        if not mine(rec, learner):
            return not_yours()
        try:
            devin().terminate(sid)
        except DevinError as exc:
            return failure(exc)
        snaps.pop(sid, None)
        return {"stopped": True}

    def reported(sid: str, n: int, learner: str) -> tuple[dict | None, JSONResponse | None]:
        rec = known(sid)
        if rec is None:
            return None, reply(410, "unknown session")
        if not mine(rec, learner):
            return None, not_yours()
        try:
            st = stage_raw(sid, n)
        except DevinError as exc:
            return None, failure(exc)
        if st is None:
            return None, reply(404, "Devin has not built this stage yet", "not_built")
        if store.earned_count(sid) < n - 1:
            return None, reply(403, "earn the earlier stage first", "locked")
        return st, None

    def graded(sid: str, st: dict, kind: str, result: dict) -> dict:
        passed = result.get("passed") is True
        n = st["n"]
        progress = store.mark(sid, n, kind) if passed else store.progress(sid, n)
        is_open = all(progress[k] for k in lc.REQUIRED)
        snaps.pop(sid, None)
        return {"stage": f"s{n}", "checkpoint": kind, "passed": passed, "result": result, "progress": progress, "unlocked": is_open,
                "code": st["code"] if is_open else None}

    @router.get("/sessions/{sid}/stages/{n}")
    def one_stage(sid: str, n: int, learner: Learner):
        st, err = reported(sid, n, learner)
        return err or lc.public_view(st, store.progress(sid, n), f"s{n}") | {"n": n}

    @router.post("/sessions/{sid}/stages/{n}/predict")
    def predict(sid: str, n: int, body: AnswerIn, learner: Learner):
        st, err = reported(sid, n, learner)
        if err:
            return err
        if len(json.dumps(body.answer)) > MAX_ANSWER:
            return reply(422, "answer too long")
        return graded(sid, st, "predict", lc.grade_predict(st, body.answer))

    @router.post("/sessions/{sid}/stages/{n}/bug")
    def bug(sid: str, n: int, body: AnswerIn, learner: Learner):
        st, err = reported(sid, n, learner)
        return err or graded(sid, st, "bug", lc.grade_bug(st, body.answer))

    @router.post("/sessions/{sid}/stages/{n}/write")
    def write(sid: str, n: int, body: SourceIn, request: Request, learner: Learner):
        if is_remote(request) and not remote_write_allowed():
            return reply(403, "writing code is graded on the base laptop only; the host can allow phones with STILLCOUNTS_LEARN_WRITE_LAN=1")
        st, err = reported(sid, n, learner)
        if err:
            return err
        if len(body.source.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:
            return reply(413, "source too large")
        if not limiter.allow(sid):
            return JSONResponse({"detail": "too many attempts, wait a minute"}, status_code=429, headers={"Retry-After": str(int(WRITE_WINDOW))})
        return graded(sid, st, "write", lc.grade_write(st, body.source))

    @router.post("/sessions/{sid}/stages/{n}/explain")
    def explain(sid: str, n: int, body: TextIn, learner: Learner):
        st, err = reported(sid, n, learner)
        if err:
            return err
        result = lc.grade_explain(st, body.text[:MAX_EXPLAIN])
        return graded(sid, st, "explain", result)

    return router
