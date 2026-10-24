"""'Earn the Code' Learn mode. The real merge code of each stage stays locked for a learner until they pass three
checkpoints graded by plain code in learn/grader.py: predict, find the bug and write. Explain is graded against a hidden
rubric that is shown after the learner submits."""
from __future__ import annotations

import importlib
import ipaddress
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

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
GLOBAL_WRITE_LIMIT = 30            # executed writes across all learners per window (learner ids are self-chosen)
MAX_LEARNERS = 10_000

# Fields a locked stage may show. Everything else (code, expected answers, solutions, rubrics, ...) stays hidden.
PUBLIC_STAGE = ("id", "order", "title", "summary")
PUBLIC_CHECKPOINT = ("prompt", "question", "hint", "ops", "choices", "options", "snippet", "line", "mutant_source", "diff",
                     "function", "signature", "stub", "starter", "docstring", "min_points")
PUBLIC_CASE = ("id", "name", "prompt", "question", "ops", "item", "field")      # predict cases: show the ops, never the expected result

Learner = Annotated[str, Header(alias="X-Learner-Id", pattern=LEARNER_PATTERN)]
MaybeLearner = Annotated[str | None, Header(alias="X-Learner-Id", pattern=LEARNER_PATTERN)]


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


def stage_view(stage: dict, progress: dict[str, bool]) -> dict:
    """What a learner may see of a stage: everything once it is unlocked, otherwise prompts only."""
    is_open = unlocked(progress)
    if is_open:
        view = dict(stage)
    else:
        view = {k: stage[k] for k in PUBLIC_STAGE if k in stage}
        for c in CHECKPOINTS:
            part = stage.get(c)
            view[c] = {k: part[k] for k in PUBLIC_CHECKPOINT if k in part} if isinstance(part, dict) else {}
            if isinstance(part, dict) and isinstance(part.get("cases"), list):
                view[c]["cases"] = [{k: case[k] for k in PUBLIC_CASE if k in case}
                                    for case in part["cases"] if isinstance(case, dict)]
        explain = stage.get("explain")
        if progress["explain"] and isinstance(explain, dict) and "rubric" in explain:
            view["explain"]["rubric"] = explain["rubric"]
    view.update(code=stage.get("code") if is_open else None, unlocked=is_open, progress=progress)
    return view


def learn_router(progress_path: Path, stages_path: Path | None = None, grader: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> APIRouter:
    router = APIRouter(prefix="/api/learn")
    stages = load_stages(stages_path or DEFAULT_STAGES)
    grader = grader or importlib.import_module("learn.grader")
    progress = Progress(progress_path)
    limiter = RateLimiter(clock)

    def find(stage_id: str) -> dict:
        if stage_id not in stages:
            raise HTTPException(status_code=404, detail="unknown stage")
        return stages[stage_id]

    def grade(learner: str, stage_id: str, checkpoint: str, value: Any) -> dict:
        try:
            result = getattr(grader, f"grade_{checkpoint}")(stage_id, value)
            if not isinstance(result, dict):
                raise TypeError("grader must return a dict")
        except Exception:                          # a broken grader or crashing learner code is a failed attempt
            result, errored = {"passed": False, "error": "the grader could not check this attempt"}, True
        else:
            errored = False
        passed = result.get("passed") is True
        if passed or (checkpoint == "explain" and not errored):
            state = progress.mark(learner, stage_id, checkpoint)
        else:
            state = progress.get(learner, stage_id)
        is_open = unlocked(state)
        return {"stage": stage_id, "checkpoint": checkpoint, "passed": passed, "result": result,
                "progress": state, "unlocked": is_open, "code": stages[stage_id].get("code") if is_open else None}

    @router.get("/stages")
    def list_stages(learner: MaybeLearner = None) -> dict:
        return {"stages": [stage_view(s, progress.get(learner, sid)) for sid, s in stages.items()]}

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
