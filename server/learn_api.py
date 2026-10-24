"""'Earn the Code' Learn mode. The guided path for each stage (the reference solution reveal and the per-learner unlock
record kept here) opens for a learner once they pass three checkpoints graded by plain code in learn/grader.py: predict,
find the bug and write. The merge code itself is public in this repository; what the server holds back is the reveal and
the record. Explain is practice: it is graded for length, one sentence per rubric point and the learner's own numbers,
and it never unlocks anything.

Predict cases are generated per learner (learn/cases.py) and the server is the grader. A wrong guess names only the wrong
case and replaces it with a new one; a wrong find-the-bug pick locks the options for BUG_LOCK_SECONDS and reshuffles them."""
from __future__ import annotations

import importlib
import ipaddress
import json
import os
import random
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from learn import cases as cases_mod

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGES = ROOT / "learn" / "stages.json"
CHECKPOINTS = ("predict", "bug", "write", "explain")
REQUIRED = ("predict", "bug", "write")
LEARNER_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MAX_ANSWER = 2000
MAX_EXPLAIN = 4000
MAX_SOURCE_BYTES = 4096            # learner source that the referee executes
WRITE_WINDOW = 60.0                # seconds
WRITE_LIMIT = 5                    # executed writes per learner per window
GLOBAL_WRITE_LIMIT = 30            # executed writes across all learners per window (a browser can still drop its cookie)
MAX_LEARNERS = 10_000
BUG_LOCK_SECONDS = 30.0            # options stay locked this long after a wrong find-the-bug pick

# Fields a locked stage may show. Everything else (code, expected answers, solutions, rubrics, ...) stays hidden.
PUBLIC_STAGE = ("id", "order", "title", "summary")
PUBLIC_CHECKPOINT = ("prompt", "question", "hint", "ops", "choices", "options", "excerpt", "snippet", "mutant_source", "diff",
                     "function", "signature", "stub", "starter", "docstring", "min_points", "min_words", "example_case")
PUBLIC_CASE = ("id", "name", "prompt", "question", "ops", "item", "field")      # predict cases: show the ops, never the expected result

LEARNER_COOKIE = "sc_learner"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def learner_id(request: Request, response: Response) -> str:
    """The learner is the browser that holds the server-issued cookie. The id is random and HttpOnly, so page
    scripts cannot read or choose it, and any X-Learner-Id header is ignored. A missing or malformed cookie gets a
    fresh id."""
    current = request.cookies.get(LEARNER_COOKIE, "")
    if re.fullmatch(LEARNER_PATTERN, current):
        return current
    fresh = secrets.token_urlsafe(24)
    response.set_cookie(LEARNER_COOKIE, fresh, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    return fresh


Learner = Annotated[str, Depends(learner_id)]


class AnswerIn(BaseModel):
    answer: Any = None


class SourceIn(BaseModel):
    source: str = Field(max_length=4 * MAX_SOURCE_BYTES)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_EXPLAIN)


def remote_write_allowed() -> bool:
    """Learner code runs on the base laptop. By default only a browser on that laptop may submit it; the host opts a
    LAN in with STILLCOUNTS_LEARN_WRITE_LAN=1 (do that only on a trusted network or inside a container)."""
    return os.environ.get("STILLCOUNTS_LEARN_WRITE_LAN") == "1"


def is_remote(request: Request) -> bool:
    """True when the caller is a non-loopback IP address. A missing or non-IP client (a unix socket, a test client)
    counts as local."""
    host = request.client.host if request.client else ""
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_stages(path: Path) -> dict[str, dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    stages = raw["stages"] if isinstance(raw, dict) else raw
    return {s["id"]: s for s in stages}


class Progress:
    """Per-learner progress in one JSON file: {learner: {stage: {checkpoint: bool}}}. Writes are atomic."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data: dict[str, dict[str, dict[str, bool]]] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, learner: str | None, stage: str) -> dict[str, bool]:
        mine = self.data.get(learner or "")
        saved = mine.get(stage) if isinstance(mine, dict) else None
        saved = saved if isinstance(saved, dict) else {}
        return {c: saved.get(c) is True for c in CHECKPOINTS}

    def mark(self, learner: str, stage: str, checkpoint: str) -> dict[str, bool]:
        with self.lock:
            if learner not in self.data and len(self.data) >= MAX_LEARNERS:
                raise HTTPException(status_code=503, detail="too many learners")
            current = self.get(learner, stage)
            if not isinstance(self.data.get(learner), dict):
                self.data[learner] = {}
            entry = self.data[learner][stage] = current
            if not entry.get(checkpoint):
                entry[checkpoint] = True
                self._save()
            return self.get(learner, stage)

    def rounds(self, learner: str, stage: str) -> dict[str, int]:
        """How many times each case of a stage has been replaced for this learner ("predict:p1", "bug")."""
        mine = self.data.get(learner)
        saved = mine.get("_rounds") if isinstance(mine, dict) else None
        found = saved.get(stage) if isinstance(saved, dict) else None
        return {k: v for k, v in found.items() if isinstance(v, int)} if isinstance(found, dict) else {}

    def bump(self, learner: str, stage: str, keys: list[str]) -> dict[str, int]:
        with self.lock:
            if learner not in self.data and len(self.data) >= MAX_LEARNERS:
                raise HTTPException(status_code=503, detail="too many learners")
            if not isinstance(self.data.get(learner), dict):
                self.data[learner] = {}
            book = self.data[learner].get("_rounds")
            book = self.data[learner]["_rounds"] = book if isinstance(book, dict) else {}
            mine = book[stage] = self.rounds(learner, stage)
            for key in keys:
                mine[key] = mine.get(key, 0) + 1
            self._save()
            return dict(mine)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


class RateLimiter:
    """Sliding-window limits, per learner and overall."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.per: dict[str, deque] = {}
        self.all: deque = deque()

    def allow(self, learner: str) -> bool:
        now = self.clock()
        with self.lock:
            mine = self.per.setdefault(learner, deque())
            for q in (mine, self.all):
                while q and q[0] <= now - WRITE_WINDOW:
                    q.popleft()
            if len(mine) >= WRITE_LIMIT or len(self.all) >= GLOBAL_WRITE_LIMIT:
                return False
            mine.append(now)
            self.all.append(now)
            for key in [k for k, q in self.per.items() if not q]:
                del self.per[key]
            return True


def unlocked(progress: dict[str, bool]) -> bool:
    return all(progress[c] for c in REQUIRED)


def bug_options(stage: dict, learner: str, rounds: dict[str, int]) -> list | None:
    """The find-the-bug options in this learner's order. The order changes after every wrong pick."""
    part = stage.get("bug")
    options = part.get("options") if isinstance(part, dict) else None
    if not isinstance(options, list) or len(options) < 2:
        return None
    shuffled = list(options)
    random.Random(cases_mod.seed_for(learner, stage.get("id", ""), "bug", rounds.get("bug", 0))).shuffle(shuffled)
    return shuffled


def stage_view(stage: dict, progress: dict[str, bool], mine: list[dict] | None = None, options: list | None = None,
               locked_for: float = 0.0) -> dict:
    """What a learner may see of a stage: everything once it is unlocked, otherwise prompts only. mine is this learner's
    own predict cases, options their shuffled find-the-bug options, locked_for the seconds left on a wrong-pick lock."""
    is_open = unlocked(progress)
    if is_open:
        view = dict(stage)
        if mine is not None:
            view["predict"] = {**stage["predict"], "cases": [{**c, "expected": cases_mod.answer(c)} for c in mine]}
    else:
        view = {k: stage[k] for k in PUBLIC_STAGE if k in stage}
        for c in CHECKPOINTS:
            part = stage.get(c)
            view[c] = {k: part[k] for k in PUBLIC_CHECKPOINT if k in part} if isinstance(part, dict) else {}
            if isinstance(part, dict) and isinstance(part.get("cases"), list):
                view[c]["cases"] = [{k: case[k] for k in PUBLIC_CASE if k in case}
                                    for case in part["cases"] if isinstance(case, dict)]
        if mine is not None:
            view["predict"]["cases"] = [{k: case[k] for k in PUBLIC_CASE if k in case} for case in mine]
        if options is not None:
            view["bug"]["options"] = options
        if locked_for > 0 and isinstance(view.get("bug"), dict):
            view["bug"]["locked_for"] = int(locked_for) + 1
        explain = stage.get("explain")
        if progress["explain"] and isinstance(explain, dict) and "rubric" in explain:
            view["explain"]["rubric"] = [{k: v for k, v in p.items() if k != "keywords"} if isinstance(p, dict) else p
                                         for p in explain["rubric"]]
    view.update(code=stage.get("code") if is_open else None, unlocked=is_open, progress=progress)
    return view


def learn_router(progress_path: Path, stages_path: Path | None = None, grader: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> APIRouter:
    router = APIRouter(prefix="/api/learn")
    stages = load_stages(stages_path or DEFAULT_STAGES)
    grader = grader or importlib.import_module("learn.grader")
    progress = Progress(progress_path)
    limiter = RateLimiter(clock)
    bug_locks: dict[tuple[str, str], float] = {}          # (learner, stage) -> clock time the lock ends

    def find(stage_id: str) -> dict:
        if stage_id not in stages:
            raise HTTPException(status_code=404, detail="unknown stage")
        return stages[stage_id]

    def mine(learner: str, stage_id: str) -> list[dict] | None:
        return cases_mod.personalise(stages[stage_id], learner, progress.rounds(learner, stage_id))

    def lock_left(learner: str, stage_id: str) -> float:
        return max(0.0, bug_locks.get((learner, stage_id), 0.0) - clock())

    def view_of(learner: str, stage_id: str) -> dict:
        stage = stages[stage_id]
        return stage_view(stage, progress.get(learner, stage_id), mine(learner, stage_id),
                          bug_options(stage, learner, progress.rounds(learner, stage_id)), lock_left(learner, stage_id))

    def run_grader(learner: str, stage_id: str, checkpoint: str, value: Any) -> tuple[dict, bool]:
        extra = {}
        if checkpoint in ("predict", "explain"):
            cases = mine(learner, stage_id)
            if cases is not None:
                extra = {"cases": cases}
        try:
            result = getattr(grader, f"grade_{checkpoint}")(stage_id, value, **extra)
            if not isinstance(result, dict):
                raise TypeError("grader must return a dict")
            return dict(result), False
        except Exception:                          # a broken grader or crashing learner code is a failed attempt
            return {"passed": False, "error": "the grader could not check this attempt"}, True

    def after_a_miss(learner: str, stage_id: str, checkpoint: str, result: dict) -> None:
        """Wrong predict cases are replaced; a wrong find-the-bug pick locks and reshuffles the options."""
        if checkpoint == "predict" and isinstance(result.get("wrong"), list):
            keys = [f"predict:{c}" for c in result["wrong"] if isinstance(c, str)]
            if keys:
                progress.bump(learner, stage_id, keys)
                fresh = {c["id"]: c for c in mine(learner, stage_id) or []}
                result["new_cases"] = [{k: fresh[c][k] for k in cases_mod.PUBLIC_CASE_FIELDS + ("ops", "prompt")
                                        if k in fresh[c]} for c in result["wrong"] if c in fresh]
        elif checkpoint == "bug":
            for stale in [k for k, until in bug_locks.items() if until <= clock()]:
                del bug_locks[stale]
            bug_locks[(learner, stage_id)] = clock() + BUG_LOCK_SECONDS
            progress.bump(learner, stage_id, ["bug"])
            result["locked_for"] = int(BUG_LOCK_SECONDS)
            options = bug_options(stages[stage_id], learner, progress.rounds(learner, stage_id))
            if options is not None:
                result["options"] = options

    def grade(learner: str, stage_id: str, checkpoint: str, value: Any) -> dict:
        if checkpoint == "bug" and lock_left(learner, stage_id) > 0:
            left = int(lock_left(learner, stage_id)) + 1
            raise HTTPException(status_code=429, detail=f"the options are locked for {left} more seconds after a wrong pick",
                                headers={"Retry-After": str(left)})
        result, errored = run_grader(learner, stage_id, checkpoint, value)
        passed = result.get("passed") is True
        if passed:
            state = progress.mark(learner, stage_id, checkpoint)
        else:
            state = progress.get(learner, stage_id)
            if not errored:
                after_a_miss(learner, stage_id, checkpoint, result)
        is_open = unlocked(state)
        return {"stage": stage_id, "checkpoint": checkpoint, "passed": passed, "result": result,
                "progress": state, "unlocked": is_open, "code": stages[stage_id].get("code") if is_open else None}

    @router.get("/stages")
    def list_stages(learner: Learner, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"        # the cases in this list are this learner's own
        return {"stages": [view_of(learner, sid) for sid in stages]}

    @router.post("/{stage_id}/predict")
    def predict(stage_id: str, body: AnswerIn, learner: Learner) -> dict:
        find(stage_id)
        if len(json.dumps(body.answer)) > MAX_ANSWER:
            raise HTTPException(status_code=422, detail=f"answer longer than {MAX_ANSWER} characters")
        return grade(learner, stage_id, "predict", body.answer)

    @router.post("/{stage_id}/bug")
    def bug(stage_id: str, body: AnswerIn, learner: Learner) -> dict:
        find(stage_id)
        if len(json.dumps(body.answer)) > MAX_ANSWER:
            raise HTTPException(status_code=422, detail=f"answer longer than {MAX_ANSWER} characters")
        return grade(learner, stage_id, "bug", body.answer)

    @router.post("/{stage_id}/write")
    def write(stage_id: str, body: SourceIn, learner: Learner, request: Request) -> dict:
        find(stage_id)
        if is_remote(request) and not remote_write_allowed():
            raise HTTPException(status_code=403, detail="writing code is graded on the base laptop only; "
                                "the host can allow phones with STILLCOUNTS_LEARN_WRITE_LAN=1")
        if len(body.source.encode("utf-8", errors="replace")) > MAX_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail=f"source larger than {MAX_SOURCE_BYTES} bytes")
        if not limiter.allow(learner):
            raise HTTPException(status_code=429, detail="too many attempts, wait a minute",
                                headers={"Retry-After": str(int(WRITE_WINDOW))})
        return grade(learner, stage_id, "write", body.source)

    @router.post("/{stage_id}/explain")
    def explain(stage_id: str, body: TextIn, learner: Learner) -> dict:
        find(stage_id)
        return grade(learner, stage_id, "explain", body.text)

    return router
