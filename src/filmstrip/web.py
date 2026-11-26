"""filmstrip.web: capture a web page for filmstrip with Playwright.

    from filmstrip.web import Recorder

    with Recorder("capture/demo", "http://localhost:5173/", viewport=(1100, 680)) as rec:
        rec.shot(hold=1200)
        rec.click("text=Dover", hold=1100, via="dip")
        rec.type("#search", "dov", hold=900)
        rec.press("Enter", hold=1000, caption="Enter")
        rec.spotlight("#high-tide", hold=1400)
        rec.zoom("#chart", hold=1800)
    # then: filmstrip capture/demo/manifest.json -o demo.webp --sheet sheet.png

Each screen is captured at `scale` (default 4) device pixels per CSS pixel, so a zoom
up to scale/2 stays sharp in a film drawn at 2x. Targets are Playwright selectors or
Locators; their boxes come from the page, in CSS pixels, which is what the manifest uses.
Before each capture the page is left to settle: no request in flight for a moment, fonts
and images loaded, scrolling stopped. Animations are off, the caret hidden and motion
reduced. Needs Playwright and its browser: pip install "filmstrip[web]", then
python -m playwright install chromium-headless-shell.
"""

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as e:  # pragma: no cover
    raise ImportError('filmstrip.web needs Playwright: pip install "filmstrip[web]"') from e

# A film shows content, not chrome: no scrollbars, no caret, no smooth scrolling.
QUIET_CSS = """
html { scrollbar-width: none; }
::-webkit-scrollbar { display: none; }
* { caret-color: transparent !important; scroll-behavior: auto !important; }
"""
STYLE_ID = "filmstrip-quiet"
ADD_STYLE = (
    "(css) => { if (document.getElementById('" + STYLE_ID + "')) return;"
    " const s = document.createElement('style'); s.id = '" + STYLE_ID + "'; s.textContent = css;"
    " (document.head || document.documentElement).appendChild(s); }"
)
# Fonts and images loaded, and no scroll position changing for three frames (at most 2 s).
AT_REST = """async () => {
  await document.fonts.ready;
  await Promise.all([...document.images].filter(i => !i.complete).map(i =>
    new Promise(r => { i.addEventListener('load', r); i.addEventListener('error', r); setTimeout(r, 3000); })));
  await new Promise(done => {
    let last = null, still = 0;
    const t0 = performance.now();
    const where = () => {
      let p = scrollX + ',' + scrollY;
      for (const e of document.querySelectorAll('*'))
        if (e.scrollTop || e.scrollLeft) p += '|' + e.scrollTop + ',' + e.scrollLeft;
      return p;
    };
    const tick = () => {
      const p = where();
      still = p === last ? still + 1 : 0;
      last = p;
      if (still >= 3 || performance.now() - t0 > 2000) done(); else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}"""
# Requests that never finish by design, so the page can be at rest while they're open.
STREAMING = ("eventsource", "websocket")


class Recorder:
    def __init__(self, out, url=None, *, viewport=(1100, 680), scale=4, film_scale=2,
                 color_scheme="light", now=None, css="", headless=True, loop="dip",
                 style=None, browser="chromium", context_options=None, quiet_ms=250,
                 settle_timeout_ms=5000):
        """Open `url` in a fresh browser.

        `now` fixes the page's clock (an ISO string or datetime; a bare time is in the
        page's timezone, UTC unless context_options sets timezone_id) so dates on screen
        never change. Timers still run in real time. `css` is added to every page.
        `quiet_ms` is how long no request may be in flight before a capture, and
        `settle_timeout_ms` the longest a capture waits for that. headless=False needs
        the full browser: python -m playwright install chromium.
        """
        self.out = Path(out)
        shutil.rmtree(self.out / "shots", ignore_errors=True)
        (self.out / "manifest.json").unlink(missing_ok=True)
        (self.out / "shots").mkdir(parents=True, exist_ok=True)
        self.film_scale, self.loop, self.style = film_scale, loop, style or {}
        self.quiet_ms, self.settle_timeout_ms = quiet_ms, settle_timeout_ms
        self.css = QUIET_CSS + css
        self.steps = []
        self.warnings = []
        self._requests = {}  # in flight: request -> when it started
        self._scroll = None  # the scroll position at the last shot
        self._pw = self.browser = None
        self._pw = sync_playwright().start()
        try:
            self.browser = getattr(self._pw, browser).launch(headless=headless)
            opts = dict(viewport={"width": viewport[0], "height": viewport[1]},
                        device_scale_factor=scale, color_scheme=color_scheme,
                        reduced_motion="reduce", locale="en-GB", timezone_id="UTC")
            opts.update(context_options or {})
            self.scale = opts["device_scale_factor"]
            self.viewport = (opts["viewport"]["width"], opts["viewport"]["height"])
            self.context = self.browser.new_context(**opts)
            self.context.add_init_script(
                f"(() => {{ const add = () => ({ADD_STYLE})({json.dumps(self.css)});"
                " if (document.head) add(); else document.addEventListener('DOMContentLoaded', add); })()")
            self.page = self.context.new_page()
            self.page.on("request", self._started)
            self.page.on("requestfinished", self._ended)
            self.page.on("requestfailed", self._ended)
            if now is not None:
                self.page.clock.set_fixed_time(_instant(now, opts.get("timezone_id")))
            if url:
                self.page.goto(url)
        except BaseException:
            self.close()
            raise

    # ---- context manager

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        try:
            if kind is None:
                self.save()
        finally:
            self.close()

    def close(self):
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    # ---- the network

    def _started(self, request):
        if request.resource_type not in STREAMING:
            self._requests[request] = time.monotonic()

    def _ended(self, request):
        self._requests.pop(request, None)

    def settle(self):
        """Wait until the page is at rest: no request in flight for `quiet_ms` (requests
        older than 30 s are taken as abandoned), then fonts and images loaded and
        scrolling stopped. The page keeps the quiet style even after set_content()."""
        t0 = quiet = time.monotonic()
        while True:
            now = time.monotonic()
            if any(now - t < 30 for t in self._requests.values()):
                quiet = now
            elif (now - quiet) * 1000 >= self.quiet_ms:
                break
            if (now - t0) * 1000 >= self.settle_timeout_ms:
                self._warn("the page never went quiet (polling or a long request?): each capture "
                           f"waits {self.settle_timeout_ms} ms. Pass wait= for what matters and "
                           "lower settle_timeout_ms.")
                break
            self.page.wait_for_timeout(25)
        self.page.evaluate(ADD_STYLE, self.css)
        self.page.evaluate(AT_REST)

    def _warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)
            print(f"filmstrip.web: {message}")

    # ---- targets

    def locator(self, target):
        return self.page.locator(target) if isinstance(target, str) else target

    def box(self, target, part=None):
        """The target's [x, y, w, h] in CSS pixels, in the viewport as it is now. `part`
        is (left, top, right, bottom) as fractions of it: (0, 0, 0.5, 1) is its left half."""
        loc = self.locator(target)
        if loc.count() == 0:
            raise ValueError(f"no element matches {target!r}")
        loc = loc.first
        if not loc.is_visible():
            raise ValueError(f"{target!r} isn't visible")
        b = loc.bounding_box(timeout=5000)
        x, y, w, h = b["x"], b["y"], b["width"], b["height"]
        if part:
            left, top, right, bottom = part
            x, y, w, h = x + w * left, y + h * top, w * (right - left), h * (bottom - top)
        return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]

    def _where(self):
        return self.page.evaluate("[scrollX, scrollY]")

    def _on_screen(self, target, part=None, centre=False):
        """The target's box, checked against the last shot: it must be on screen there."""
        if self._scroll is None:
            raise ValueError("start with rec.shot(): an effect or click needs a screen to be drawn on")
        if self._where() != self._scroll:
            raise ValueError("the page has scrolled since the last shot; take one first "
                             "(rec.shot() or rec.scroll(target))")
        b = self.box(target, part)
        vw, vh = self.viewport
        x, y, w, h = b
        if centre:
            off = not (0 <= x + w / 2 <= vw and 0 <= y + h / 2 <= vh)
        else:
            off = x + w <= 0 or y + h <= 0 or x >= vw or y >= vh
        if off:
            raise ValueError(f"{target!r} is off-screen in the last shot; bring it into view "
                             f"with rec.scroll({target!r}) first")
        return b

    def _wait(self, wait):
        if wait is None:
            return
        if isinstance(wait, str):
            self.page.locator(wait).first.wait_for(state="visible")
        else:
            wait(self.page)

    # ---- steps

    def shot(self, hold=1000, caption=None, via="cut"):
        """Let the page settle, then capture it as it is."""
        self.settle()
        n = sum(1 for s in self.steps if "shot" in s)
        name = f"shots/{n:03}.png"
        self.page.screenshot(path=str(self.out / name), animations="disabled", caret="hide")
        self._scroll = self._where()
        step = {"shot": name, "scale": self.scale, "hold": hold}
        if caption:
            step["caption"] = caption
        if via != "cut":
            step["via"] = via
        self.steps.append(step)

    def click(self, target, hold=1000, via="cut", caption=None, glide=True, wait=None):
        """Glide the pointer to the target and click it there, then capture the result.
        `wait` is a selector to wait for, or a function of the page, before the capture."""
        x, y, w, h = self._on_screen(target, centre=True)
        if glide:
            self.steps.append({"click": [round(x + w / 2, 2), round(y + h / 2, 2)]})
        self.locator(target).first.click()
        self._wait(wait)
        self.shot(hold=hold, caption=caption, via=via)

    def type(self, target, text, per_key=90, hold=900, caption=False, wait=None):
        """Type `text` at the end of the target's text a key at a time, capturing each key."""
        loc = self.locator(target).first
        focused = loc.evaluate("e => e === document.activeElement || e.contains(document.activeElement)")
        loc.focus()
        if not focused:
            loc.evaluate("""e => {
              if (typeof e.value === 'string' && e.setSelectionRange) {
                try { e.setSelectionRange(e.value.length, e.value.length); } catch (_) {}
              } else if (e.isContentEditable) {
                const r = document.createRange(); r.selectNodeContents(e); r.collapse(false);
                const s = getSelection(); s.removeAllRanges(); s.addRange(r);
              }
            }""")
        for i, ch in enumerate(text):
            loc.press_sequentially(ch)
            last = i == len(text) - 1
            if last:
                self._wait(wait)
            self.shot(hold=hold if last else per_key, caption=text[: i + 1] if caption else None)

    def press(self, key, hold=900, caption=None, via="cut", wait=None):
        """Press a key (Playwright's names: "Enter", "Control+K"), then capture."""
        self.page.keyboard.press(key)
        self._wait(wait)
        self.shot(hold=hold, caption=caption, via=via)

    def act(self, fn, hold=900, caption=None, via="cut", wait=None, capture=True):
        """Do anything to the page (`fn(page)`), then capture. With capture=False it
        happens off camera: follow it with rec.shot(via="dip") to show the result."""
        fn(self.page)
        self._wait(wait)
        if capture:
            self.shot(hold=hold, caption=caption, via=via)

    def scroll(self, target=None, *, y=None, hold=700, via="cut", align="center"):
        """Scroll the target into view (`align`: start, center, end, nearest), or the page
        to `y`, then capture."""
        if target is not None:
            self.locator(target).first.evaluate(
                "(e, a) => e.scrollIntoView({block: a, inline: 'nearest', behavior: 'instant'})", align)
        elif y is not None:
            self.page.evaluate("y => window.scrollTo({top: y, behavior: 'instant'})", y)
        else:
            raise ValueError("scroll needs a target or y=")
        self.shot(hold=hold, via=via)

    def spotlight(self, target, hold=1400, pad=6, radius=6, part=None):
        """Dim all but the target, ring it, hold, and undo; the screen doesn't change."""
        self.steps.append({"spotlight": self._on_screen(target, part), "pad": pad, "radius": radius,
                           "hold": hold})

    def zoom(self, target, hold=1800, pad=24, part=None):
        """Zoom in until the target (or `part` of it) fills the frame, hold, and zoom out.
        Sharp up to scale / film_scale times (2x by default)."""
        self.steps.append({"zoom": self._on_screen(target, part), "pad": pad, "hold": hold})

    def pointer(self, at=None):
        """Show the pointer at `at` ([x, y], or a target's centre), or hide it with None."""
        if self._scroll is None:
            raise ValueError("start with rec.shot(): the pointer needs a screen to be drawn on")
        if at is not None and not isinstance(at, (list, tuple)):
            x, y, w, h = self._on_screen(at, centre=True)
            at = [x + w / 2, y + h / 2]
        self.steps.append({"pointer": list(at) if at is not None else "hide"})

    def pause(self, ms):
        self.steps.append({"pause": ms})

    def save(self):
        manifest = {"scale": self.film_scale, "loop": self.loop, "steps": self.steps}
        if self.style:
            manifest["style"] = self.style
        path = self.out / "manifest.json"
        path.write_text(json.dumps(manifest, indent=1) + "\n")
        return path


def _instant(now, zone):
    """`now` as an aware datetime; a bare time is in `zone` (UTC by default)."""
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    if not isinstance(now, datetime):
        raise TypeError(f"now= takes an ISO date-time string or a datetime, not {now!r}")
    if now.tzinfo is None:
        if zone and zone != "UTC":
            from zoneinfo import ZoneInfo
            now = now.replace(tzinfo=ZoneInfo(zone))
        else:
            now = now.replace(tzinfo=timezone.utc)
    return now
