"""Call the real FastAPI app in-process on the simulation's own thread: no sockets, no worker threads."""
from __future__ import annotations

import asyncio
import contextlib
import random
import uuid
from typing import Iterator

import fastapi.routing


async def _inline(func, *args, **kwargs):
    return func(*args, **kwargs)


@contextlib.contextmanager
def deterministic_runtime(rng: random.Random) -> Iterator[None]:
    """Run sync endpoints inline (instead of in a thread pool) and draw uuid4 values from the simulation's RNG."""
    saved_pool, saved_uuid4 = fastapi.routing.run_in_threadpool, uuid.uuid4
    fastapi.routing.run_in_threadpool = _inline
    uuid.uuid4 = lambda: uuid.UUID(int=rng.getrandbits(128), version=4)
    try:
        yield
    finally:
        fastapi.routing.run_in_threadpool, uuid.uuid4 = saved_pool, saved_uuid4


class AsgiClient:
    """A minimal HTTP/1.1-over-ASGI client. Returns (status, body bytes); an app exception becomes a 500."""

    def __init__(self, app) -> None:
        self.app = app
        self.loop = asyncio.new_event_loop()

    def request(self, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
        return self.loop.run_until_complete(self._call(method, path, body))

    async def _call(self, method: str, path: str, body: bytes) -> tuple[int, bytes]:
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"base"), (b"content-type", b"application/json"),
                             (b"content-length", str(len(body)).encode())],
                 "client": ("phone", 1), "server": ("base", 80)}
        state = {"sent": False, "status": None, "body": b""}

        async def receive():
            if not state["sent"]:
                state["sent"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            elif message["type"] == "http.response.body":
                state["body"] += message.get("body", b"")

        try:
            await self.app(scope, receive, send)
        except Exception:                      # ServerErrorMiddleware re-raises after sending its 500
            return 500, state["body"]
        return state["status"] or 500, state["body"]

    def close(self) -> None:
        self.loop.close()
