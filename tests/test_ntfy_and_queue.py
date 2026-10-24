import json

import pytest

from stillcounts.ntfy import Action, NtfyError, build_headers, notify
from stillcounts.outbox import Outbox


class Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text


class Http:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return self.responses.pop(0) if self.responses else Resp()


def test_headers_carry_title_priority_tags_and_actions():
    h = build_headers("Issue filed", priority=4, tags=["warning"],
                      actions=[Action("view", "Open", "https://github.com/x/y/issues/1"),
                               Action("http", "Approve", "https://example.com/ok", method="POST")])
    assert h["Title"] == "Issue filed"
    assert h["Priority"] == "4"
    assert h["Tags"] == "warning"
    assert h["Actions"] == ("view, Open, https://github.com/x/y/issues/1; "
                            "http, Approve, https://example.com/ok, method=POST")


def test_more_than_three_actions_is_rejected():
    acts = [Action("view", str(i), "https://x") for i in range(4)]
    with pytest.raises(ValueError):
        build_headers("t", actions=acts)


def test_priority_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        build_headers("t", priority=9)


def test_notify_posts_to_the_topic_url():
    http = Http()
    notify("kannu-abc123", "Hello", "body text", http=http)
    assert http.calls[0]["url"] == "https://ntfy.sh/kannu-abc123"
    assert http.calls[0]["data"] == "body text".encode()
    assert http.calls[0]["headers"]["Title"] == "Hello"


def test_messages_over_the_limit_are_truncated_not_rejected():
    http = Http()
    notify("kannu-abcdef123456", "T", "x" * 5000, http=http)
    assert len(http.calls[0]["data"]) <= 4096


def test_notify_refuses_obvious_secrets():
    with pytest.raises(ValueError):
        notify("kannu-abcdef123456", "T", "token cog_abcdefghijklmnopqrstuvwxyz0123456789", http=Http())
    with pytest.raises(ValueError):
        notify("kannu-abcdef123456", "T", "key ghp_" + "a" * 36, http=Http())


def test_notify_raises_on_http_error():
    with pytest.raises(NtfyError):
        notify("kannu-abcdef123456", "T", "m", http=Http([Resp(429, "slow down")]))


def test_a_short_guessable_topic_is_rejected():
    with pytest.raises(ValueError):
        notify("test", "T", "m", http=Http())


def test_outbox_queues_when_the_sender_fails_and_flushes_later(tmp_path):
    box = Outbox(tmp_path / "out.jsonl")
    sent = []

    def down(item):
        raise ConnectionError("offline")

    box.enqueue({"n": 1})
    box.enqueue({"n": 2})
    assert box.flush(down) == (0, 2)       # nothing sent, two waiting
    assert box.pending() == 2

    def up(item):
        sent.append(item)

    assert box.flush(up) == (2, 0)
    assert sent == [{"n": 1}, {"n": 2}]
    assert box.pending() == 0


def test_outbox_stops_at_the_first_failure_and_keeps_order(tmp_path):
    box = Outbox(tmp_path / "o.jsonl")
    for n in range(3):
        box.enqueue({"n": n})
    calls = []

    def flaky(item):
        calls.append(item["n"])
        if item["n"] == 1:
            raise ConnectionError("blip")

    assert box.flush(flaky) == (1, 2)
    assert calls == [0, 1]
    assert [json.loads(l)["n"] for l in (tmp_path / "o.jsonl").read_text().splitlines()] == [1, 2]


def test_outbox_survives_a_restart(tmp_path):
    Outbox(tmp_path / "o.jsonl").enqueue({"a": 1})
    assert Outbox(tmp_path / "o.jsonl").pending() == 1


def test_outbox_ignores_a_corrupt_line(tmp_path):
    p = tmp_path / "o.jsonl"
    p.write_text('{"ok": 1}\nnot json\n')
    assert Outbox(p).pending() == 1


def test_outbox_keeps_an_item_enqueued_while_a_flush_is_in_flight(tmp_path):
    box = Outbox(tmp_path / "o.jsonl")
    box.enqueue({"n": 1})

    def slow_send(item):
        box.enqueue({"n": 99})          # a new message arrives while the send is in progress

    assert box.flush(slow_send) == (1, 0)
    assert box.pending() == 1           # the new one must survive
