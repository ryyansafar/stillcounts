"""Results dashboard data for Learn and the chat: who earned which stage, and every moment the adaptation engine changed the
difficulty. Privacy: a heart rate never reaches this server. The browser sends the level change and a reason with every number
removed ("heart rate up vs baseline"), and this module strips digits again before anything is stored."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from server.learn_api import CHECKPOINTS, LEARNER_PATTERN, REQUIRED

MAX_EVENTS = 500
KINDS = ("stress", "misses", "calm", "manual")


class AdaptEvent(BaseModel):
    chat: str = Field(pattern=LEARNER_PATTERN)
    level: int = Field(ge=1, le=3)
    prev: int = Field(ge=1, le=3)
    kind: str = "stress"
    stage: int | None = Field(default=None, ge=1, le=20)
    reason: str = Field(default="", max_length=200)


def scrub(text: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "#", text).strip()[:160]


def results_router(progress_path: Path, events_path: Path, clock=time.time) -> APIRouter:
    router = APIRouter(prefix="/api/learn")
    lock = threading.Lock()

    def read_events() -> list[dict]:
        try:
            lines = Path(events_path).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-MAX_EVENTS:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    @router.post("/adaptations")
    def log_adaptation(ev: AdaptEvent) -> dict:
        row = {"at": round(clock(), 3), "chat": ev.chat, "level": ev.level, "prev": ev.prev, "kind": ev.kind if ev.kind in KINDS else "manual",
               "stage": ev.stage, "reason": scrub(ev.reason)}
        with lock:
            Path(events_path).parent.mkdir(parents=True, exist_ok=True)
            with open(events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        return {"logged": True}

    @router.get("/results")
    def results() -> dict:
        try:
            data = json.loads(Path(progress_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        learners = []
        for learner, stages in (data.items() if isinstance(data, dict) else []):
            if not isinstance(stages, dict):
                continue
            earned = [s for s, p in stages.items() if isinstance(p, dict) and all(p.get(k) is True for k in REQUIRED)]
            learners.append({"learner": learner, "earned": len(earned), "stages": {s: {c: bool(p.get(c)) for c in CHECKPOINTS}
                                                                                      for s, p in stages.items() if isinstance(p, dict)}})
        events = read_events()
        down = sum(1 for e in events if e["level"] < e["prev"])
        return {"learners": sorted(learners, key=lambda r: -r["earned"])[:100], "adaptations": events[-100:],
                "summary": {"events": len(events), "lowered": down, "restored": len(events) - down}}

    return router
