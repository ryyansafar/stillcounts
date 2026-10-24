import pytest

from ops.devin_client import DevinClient, DevinError, load_env


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def client(responses):
    http = FakeHttp(responses)
    return DevinClient("cog_secrettoken", "org-123", http=http), http


def test_create_session_posts_to_v3_with_caps_and_tags():
    c, http = client([FakeResp(200, {"session_id": "abc", "url": "https://app.devin.ai/sessions/abc"})])
    out = c.create_session("do it", title="scout", tags=["a", "b"], max_acu_limit=3, playbook_id="pb-1",
                           structured_output_schema={"type": "object"})
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.devin.ai/v3/organizations/org-123/sessions"
    assert call["headers"]["Authorization"] == "Bearer cog_secrettoken"
    assert call["json"]["prompt"] == "do it"
    assert call["json"]["max_acu_limit"] == 3
    assert call["json"]["playbook_id"] == "pb-1"
    assert call["json"]["tags"] == ["a", "b"]
    assert call["json"]["structured_output_schema"] == {"type": "object"}
    assert out["session_id"] == "abc"


def test_optional_fields_are_omitted():
    c, http = client([FakeResp(200, {"session_id": "x"})])
    c.create_session("p")
    assert set(http.calls[0]["json"]) == {"prompt", "max_acu_limit"}


def test_message_and_get_session_urls():
    c, http = client([FakeResp(200, {}), FakeResp(200, {"status": "running"})])
    c.message("abc", "INTERRUPT: stop")
    c.get_session("abc")
    assert http.calls[0]["url"].endswith("/sessions/abc/messages")
    assert http.calls[0]["json"] == {"message": "INTERRUPT: stop"}
    assert http.calls[1]["method"] == "GET" and http.calls[1]["url"].endswith("/sessions/abc")


def test_create_playbook():
    c, http = client([FakeResp(200, {"playbook_id": "pb-9"})])
    c.create_playbook("Scout", "body", "chillu-scout", {"type": "object"})
    assert http.calls[0]["url"].endswith("/playbooks")
    assert http.calls[0]["json"] == {"title": "Scout", "body": "body", "macro": "chillu-scout",
                                     "structured_output_schema": {"type": "object"}}


def test_errors_carry_status_but_never_the_token():
    c, _ = client([FakeResp(403, {"detail": "Unauthorized"})])
    with pytest.raises(DevinError) as exc:
        c.get_session("abc")
    assert "403" in str(exc.value)
    assert "cog_secrettoken" not in str(exc.value)


def test_poll_returns_when_terminal_state_reached():
    c, http = client([FakeResp(200, {"status": "running"}), FakeResp(200, {"status": "running"}),
                      FakeResp(200, {"status": "suspended", "structured_output": {"ok": 1}})])
    sleeps = []
    out = c.poll("abc", interval=10, timeout=100, sleep=sleeps.append, clock=iter(range(0, 1000, 10)).__next__)
    assert out["status"] == "suspended"
    assert sleeps == [10, 10]


def test_poll_times_out():
    c, _ = client([FakeResp(200, {"status": "running"})] * 20)
    with pytest.raises(DevinError):
        c.poll("abc", interval=10, timeout=30, sleep=lambda s: None, clock=iter(range(0, 1000, 10)).__next__)


def test_load_env_reads_values_without_exposing_them(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DEVIN_TOKEN=cog_x\nDEVIN_ORG=org-1\n# comment\n")
    assert load_env(p) == {"DEVIN_TOKEN": "cog_x", "DEVIN_ORG": "org-1"}


def test_load_env_rejects_a_doubled_token(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DEVIN_TOKEN=cog_abccog_abc\nDEVIN_ORG=org-1\n")
    with pytest.raises(DevinError):
        load_env(p)
