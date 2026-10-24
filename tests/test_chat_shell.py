"""The chat page is the root of the product; the ledger and the other pages stay reachable; the chat is offline-capable."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app

CLIENT = Path(__file__).resolve().parent.parent / "client"
CHAT_FILES = ("chat.html", "chat.css", "chat_core.js", "chat_view.js", "chat_bridge.js", "chat_replay.js", "chat_ctl.js", "chat.js")


def app(tmp_path):
    return TestClient(create_app(tmp_path / "ops.jsonl"))


def test_root_is_the_chat_and_the_ledger_moved_to_ledger(tmp_path):
    c = app(tmp_path)
    home = c.get("/").text
    assert 'id="thread"' in home and 'id="prompt"' in home and 'id="newchat"' in home
    ledger = c.get("/ledger")
    assert ledger.status_code == 200 and "Field camp ledger" in ledger.text


def test_every_other_page_is_one_link_away_from_the_chat(tmp_path):
    home = app(tmp_path).get("/").text
    for href in ("/ledger", "/board", "/static/learn.html", "/race"):
        assert f'href="{href}"' in home, href


def test_chat_scripts_and_styles_are_served_and_cached_for_offline(tmp_path):
    c = app(tmp_path)
    sw = c.get("/sw.js").text
    for name in CHAT_FILES:
        assert c.get("/static/" + name).status_code == 200, name
        assert "/static/" + name in sw, name
    assert '"/ledger"' in sw and "/static/icons/logo.svg" in sw


def test_every_script_the_chat_page_loads_exists_and_is_in_the_cache_list(tmp_path):
    html = (CLIENT / "chat.html").read_text()
    sw = (CLIENT / "sw.js").read_text()
    for src in re.findall(r'<script src="(/static/[^"]+)"', html):
        assert (CLIENT / src.removeprefix("/static/")).exists(), src
        assert src in sw, src


def test_the_chat_page_never_loads_a_remote_script_and_carries_the_support_links(tmp_path):
    html = (CLIENT / "chat.html").read_text()
    assert not re.search(r'<script[^>]+src="https?://', html)
    js = (CLIENT / "chat.js").read_text()
    for url in ("https://ryyansafar.site", "https://github.com/ryyansafar", "https://buymeacoffee.com/ryyansafar",
                "https://www.paypal.com/paypalme/ryyansafar", "https://razorpay.me/@ryyansafar"):
        assert url in js


def test_the_browser_never_holds_a_devin_token():
    for name in CHAT_FILES:
        text = (CLIENT / name).read_text().lower()
        assert "bearer" not in text and "devin_token" not in text and "authorization" not in text, name


def test_the_shell_is_an_ordinary_chat_layout_with_quiet_secondary_links():
    html = (CLIENT / "chat.html").read_text()
    for needle in ('id="newchat"', 'id="chatlist"', 'id="thread"', 'id="prompt"', 'id="send"', 'id="hr-widget"'):
        assert needle in html, needle
    assert html.index('id="hr-widget"') < html.index('id="thread"'), "the heart rate item lives in the top bar"
    menu = re.search(r'<details class="menu".*?</details>', html, re.S).group(0)
    for href in ("/ledger", "/board", "/static/learn.html", "/race", "/static/results.html"):
        assert f'href="{href}"' in menu, href


def test_no_decoration_one_font_stack_and_no_remote_fonts():
    css = (CLIENT / "chat.css").read_text() + (CLIENT / "hr_widget.css").read_text()
    assert "box-shadow: 0 0" not in css and "text-shadow" not in css
    assert "@keyframes" not in css, "no animated flourishes"
    assert "@font-face" not in css and not re.search(r"https?://fonts\.", css)
    assert "system-ui" in css


def test_the_empty_state_has_no_marketing_copy():
    js = (CLIENT / "chat.js").read_text()
    body = js[js.index("function renderWelcome"):js.index("function renderComposer")]
    assert "lede" not in body and "stage by stage" not in body
