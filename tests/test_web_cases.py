"""filmstrip.web edge cases, each from a confirmed review finding, on small local pages.

Skipped when Playwright or its browser isn't installed.
"""

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")
from filmstrip.web import Recorder  # noqa: E402

PAGES = {
    "tall.html": """<!doctype html><body style="margin:0;height:2400px">
<div id="near" style="position:absolute;top:40px;left:40px;width:200px;height:60px;background:#36c">near</div>
<div id="far" style="position:absolute;top:1500px;left:100px;width:300px;height:80px;background:#d33">far</div>
</body>""",
    "slow.html": """<!doctype html><body><button id="go">Load</button><div id="out">idle</div>
<script>
document.getElementById('go').onclick = async () => {
  out.textContent = 'Loading…';
  const r = await fetch('/slow?ms=700'); out.textContent = await r.text();
};
</script></body>""",
    "poll.html": """<!doctype html><body><div id="n">0</div><script>
let n = 0; setInterval(() => fetch('/poll').then(() => { document.getElementById('n').textContent = ++n; }), 100);
</script></body>""",
    "edit.html": """<!doctype html><body><input id="in" value="Hello">
<div id="ce" contenteditable="true">Draft</div></body>""",
    "smooth.html": """<!doctype html><html style="scroll-behavior:smooth"><body style="margin:0">
<a id="down" href="#three">down</a><div style="height:1800px"></div><h2 id="three">three</h2>
<div style="height:1000px"></div></body></html>""",
    "clock.html": """<!doctype html><body><div id="t"></div><script>
document.getElementById('t').textContent = new Date().toLocaleTimeString('en-GB');</script></body>""",
}


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/slow":
            time.sleep(int(parse_qs(u.query).get("ms", ["500"])[0]) / 1000)
            return self.reply(b"Loaded")
        if u.path == "/poll":
            return self.reply(b"ok")
        name = u.path.lstrip("/")
        if name in PAGES:
            return self.reply(PAGES[name].encode(), "text/html; charset=utf-8")
        self.send_error(404)

    def reply(self, body, kind="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def rec_at(tmp_path, site):
    made = []

    def make(page, **kw):
        try:
            r = Recorder(tmp_path / f"cap{len(made)}", f"{site}/{page}", viewport=(600, 400), **kw)
        except Exception as e:
            if "Executable doesn't exist" in str(e):
                pytest.skip("run: python -m playwright install chromium-headless-shell")
            raise
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


def test_an_effect_on_an_off_screen_target_says_to_scroll_first(rec_at):
    rec = rec_at("tall.html")
    rec.shot()
    rec.spotlight("#near")
    with pytest.raises(ValueError, match="off-screen"):
        rec.spotlight("#far")
    rec.scroll("#far")
    rec.spotlight("#far")
    x, y, w, h = rec.steps[-1]["spotlight"]
    assert 0 <= y and y + h <= 400


def test_effects_need_a_shot_first(rec_at):
    rec = rec_at("tall.html")
    with pytest.raises(ValueError, match="start with rec.shot"):
        rec.click("#near")


def test_a_shot_waits_for_requests_a_click_started(rec_at):
    rec = rec_at("slow.html")
    rec.shot()
    t = time.monotonic()
    rec.click("#go")
    assert time.monotonic() - t >= 0.7
    assert rec.page.inner_text("#out") == "Loaded"


def test_a_polling_page_costs_at_most_the_settle_timeout_and_says_so(rec_at, capsys):
    rec = rec_at("poll.html", settle_timeout_ms=600)
    t = time.monotonic()
    rec.shot()
    assert time.monotonic() - t < 2.5
    assert "never went quiet" in capsys.readouterr().out


def test_typing_goes_at_the_end(rec_at):
    rec = rec_at("edit.html")
    rec.shot()
    rec.type("#in", " world", per_key=50, hold=100)
    rec.type("#ce", " text", per_key=50, hold=100)
    assert rec.page.input_value("#in") == "Hello world"
    assert rec.page.inner_text("#ce").replace("\xa0", " ") == "Draft text"


def test_smooth_scrolling_is_off(rec_at):
    rec = rec_at("smooth.html")
    rec.shot()
    rec.click("#down")
    assert rec.page.evaluate("scrollY") > 1000


def test_act_can_work_off_camera(rec_at):
    rec = rec_at("edit.html")
    rec.shot()
    rec.act(lambda p: p.fill("#in", "changed"), capture=False)
    assert sum(1 for s in rec.steps if "shot" in s) == 1
    rec.shot(via="dip")
    assert rec.steps[-1]["via"] == "dip"


def test_a_bare_now_is_in_the_pages_timezone(rec_at):
    rec = rec_at("clock.html", now="2026-03-02T09:30:00",
                 context_options={"timezone_id": "America/New_York"})
    assert rec.page.inner_text("#t").startswith("09:30")


def test_set_content_keeps_the_quiet_style(rec_at):
    rec = rec_at("edit.html", css="body { background: rgb(255, 0, 0); }")
    rec.page.set_content("<p>replaced</p>")
    rec.shot()
    assert rec.page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 0, 0)"


def test_the_manifest_records_the_real_scale(rec_at):
    rec = rec_at("edit.html", context_options={"device_scale_factor": 2})
    rec.shot()
    assert rec.steps[0]["scale"] == 2


def test_a_missing_target_fails_fast(rec_at):
    rec = rec_at("edit.html")
    rec.shot()
    t = time.monotonic()
    with pytest.raises(ValueError, match="no element matches"):
        rec.spotlight("#missing")
    assert time.monotonic() - t < 2


def test_a_failed_start_cleans_up_and_leaves_no_old_manifest(tmp_path, site):
    out = tmp_path / "cap"
    (out / "shots").mkdir(parents=True)
    (out / "manifest.json").write_text(json.dumps({"steps": [{"shot": "shots/000.png"}]}))
    with pytest.raises(Exception):
        Recorder(out, "http://127.0.0.1:9/")  # an unsafe port: goto fails
    assert not (out / "manifest.json").exists()
    rec = Recorder(out, f"{site}/edit.html")  # Playwright was stopped, so this works
    rec.close()
