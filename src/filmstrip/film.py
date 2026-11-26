"""filmstrip: turn captured screens into a looping, lossless animated WebP, with effects.

    filmstrip MANIFEST.json -o OUT.webp [--sheet SHEET.png] [--frames DIR] [--max]

The manifest comes from a capture script (filmstrip.web for web pages, or any program
that saves screenshots). Coordinates are logical pixels (CSS pixels for a web page). Each
screenshot says how many of its pixels make one logical pixel (its `scale`, default 2),
and the film draws `scale` pixels per logical pixel (default 2). See SKILL.md.

Only lossless WebP is written: lossy WebP smears text and leaves artifacts.
"""

import argparse
import json
import math
import sys
from numbers import Real
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("filmstrip: needs Pillow: pip install pillow")

# Effect timings and looks. A manifest's "style" overrides any of them.
STYLE = {
    "frame_ms": 40,  # one frame of an effect
    "accent": "#e0b050",  # spotlight ring and click ring
    "dim": 0.35,  # brightness outside a spotlight
    "ring": 2,  # spotlight ring thickness, logical px (0: none)
    "radius": 0,  # spotlight corner radius, logical px
    "fade": 3,  # frames to fade a spotlight in, and out
    "zoom_in": 8,  # frames to zoom in
    "zoom_out": 6,  # frames to zoom out
    "dip": 3,  # frames to fade out, and in, on a "dip"
    "dip_ms": 30,  # one frame of a dip
    "after": 250,  # ms on the plain screen after a spotlight or zoom
    "move_ms": 480,  # the pointer's glide to a click
    "click": 4,  # frames of the click ring
    "pointer": 20,  # the pointer's height, logical px
    "caption_size": 13,  # caption text, logical px
    "caption_font": None,  # a .ttf/.ttc path; else a monospace system font
    "matte": None,  # what shows through transparent pixels; else the image's main colour
}
COUNTS = ("fade", "zoom_in", "zoom_out", "dip", "click")  # whole numbers >= 1
POSITIVE = ("frame_ms", "dip_ms", "move_ms", "pointer", "caption_size")  # > 0
NONNEGATIVE = ("ring", "radius", "after")

# Each step has one of these keys, and only the keys listed with it (and any "_note").
STEP_KEYS = {
    "shot": {"shot", "scale", "hires", "hires_scale", "hold", "caption", "via"},
    "spotlight": {"spotlight", "pad", "radius", "hold", "after"},
    "zoom": {"zoom", "pad", "hold", "after"},
    "click": {"click", "hold"},
    "pointer": {"pointer", "hold"},
    "pause": {"pause"},
}
TOP_KEYS = {"scale", "loop", "style", "steps"}

SS = 4  # supersampling for drawn shapes, so their edges are smooth
MIN_MS = 20  # browsers stretch frames shorter than this
SMALL_CHANGE = 0.15  # a dip over less of the frame than this is a plain crossfade
MONO_FONTS = [  # Menlo first: SF Mono has no ↵ ⇥ ⎋
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
]


class FilmError(Exception):
    pass


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def rgb(c, what="colour"):
    """`#rgb`, `#rrggbb` or [r, g, b] as a tuple."""
    if isinstance(c, str):
        h = c.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        try:
            if len(h) != 6:
                raise ValueError
            return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            raise FilmError(f"{what}: {c!r} isn't #rgb or #rrggbb") from None
    if isinstance(c, (list, tuple)) and len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c):
        return tuple(c)
    raise FilmError(f"{what}: {c!r} isn't a colour")


def number(v, what, minimum=None, above=None):
    if isinstance(v, bool) or not isinstance(v, Real):
        raise FilmError(f"{what}: {v!r} isn't a number")
    if minimum is not None and v < minimum:
        raise FilmError(f"{what}: {v!r} is below {minimum}")
    if above is not None and v <= above:
        raise FilmError(f"{what}: {v!r} must be above {above}")
    return v


def check_style(st):
    for k in COUNTS:
        if isinstance(st[k], bool) or not isinstance(st[k], int) or st[k] < 1:
            raise FilmError(f"style {k}: {st[k]!r} must be a whole number of frames, at least 1")
    for k in POSITIVE:
        number(st[k], f"style {k}", above=0)
    for k in NONNEGATIVE:
        number(st[k], f"style {k}", minimum=0)
    if not 0 <= number(st["dim"], "style dim") <= 1:
        raise FilmError("style dim: must be from 0 to 1")
    st["accent"] = rgb(st["accent"], "style accent")
    if st["matte"] is not None:
        st["matte"] = rgb(st["matte"], "style matte")
    return st


def main_colour(img):
    small = img.resize((max(1, img.width // 4), max(1, img.height // 4)), Image.Resampling.NEAREST)
    return max(small.getcolors(small.width * small.height))[1]


def load_rgb(path, matte):
    """An image as RGB, its transparent pixels over `matte` (or its main opaque colour)."""
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        img = img.convert("RGBA")
        if img.getchannel("A").getextrema()[0] < 255:
            if matte is None:
                opaque = img.copy()
                opaque.putalpha(255)
                mask = img.getchannel("A").point(lambda a: 255 if a == 255 else 0)
                small = Image.composite(opaque, Image.new("RGBA", img.size), mask)
                colours = [c for c in small.resize((max(1, img.width // 4), max(1, img.height // 4)),
                                                   Image.Resampling.NEAREST).getcolors(1 << 24) if c[1][3]]
                matte = max(colours)[1][:3] if colours else (255, 255, 255)
            bg = Image.new("RGBA", img.size, tuple(matte) + (255,))
            img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


class Shot:
    """One captured screen: its picture at the film's size, and a source to zoom into."""

    def __init__(self, path, scale, hires, hires_scale, out_scale, matte):
        self.path = path
        number(scale, f"{path.name} scale", above=0)
        img = load_rgb(path, matte)
        lw, lh = img.width / scale, img.height / scale
        self.logical = (lw, lh)
        self.out = (round(lw * out_scale), round(lh * out_scale))
        if img.size == self.out:  # the screen as captured, when the scales match
            self.base = img
        else:
            f = Image.Resampling.BOX if scale >= out_scale else Image.Resampling.LANCZOS
            self.base = img.resize(self.out, f)
        self.src, self.src_scale = img, scale  # what zooms crop: the sharpest there is
        if hires:
            h = load_rgb(hires, matte)
            if hires_scale is None:
                hires_scale = h.width / lw
            number(hires_scale, f"{hires.name} hires_scale", above=0)
            if abs(h.width / hires_scale - lw) > 0.5 or abs(h.height / hires_scale - lh) > 0.5:
                raise FilmError(f"{hires}: not the same screen as {path} at hires_scale {hires_scale:g}")
            if hires_scale > scale:
                self.src, self.src_scale = h, hires_scale

    def view(self, v):
        """The logical rectangle `v` drawn at the film's size: the base for the whole
        screen, else cropped from the sharpest source."""
        lw, lh = self.logical
        x, y, w, h = v
        w, h = min(w, lw), min(h, lh)
        x, y = min(max(x, 0.0), lw - w), min(max(y, 0.0), lh - h)
        if w >= lw - 1e-6 and h >= lh - 1e-6:
            return self.base.copy()
        s = self.src_scale
        box = (x * s, y * s, min((x + w) * s, self.src.width), min((y + h) * s, self.src.height))
        f = Image.Resampling.BOX if w * s >= self.out[0] else Image.Resampling.LANCZOS
        return self.src.resize(self.out, f, box=box)

    def sharp_zoom(self, v):
        """Whether `v` can be shown without enlarging pixels."""
        return v[2] * self.src_scale >= self.out[0] - 0.5


class Film:
    def __init__(self, manifest, root):
        if not isinstance(manifest, dict):
            raise FilmError("the manifest must be a JSON object")
        extra = {k for k in manifest if not k.startswith("_")} - TOP_KEYS
        if extra:
            raise FilmError(f"unknown manifest keys: {', '.join(sorted(extra))}")
        self.root = root
        self.scale = number(manifest.get("scale", 2), "scale", above=0)
        style = {k: v for k, v in manifest.get("style", {}).items() if not k.startswith("_")}
        unknown = set(style) - set(STYLE)
        if unknown:
            raise FilmError(f"unknown style keys: {', '.join(sorted(unknown))}")
        self.st = check_style({**STYLE, **style})
        self.loop = manifest.get("loop", "dip")
        if self.loop not in ("dip", "cut"):
            raise FilmError(f'loop must be "dip" or "cut", not {self.loop!r}')
        self.frames = []  # [Image, ms]
        self.size = None  # the film's logical size
        self.out = None  # and in pixels
        self.shot = None
        self.caption = None  # on the current shot's own frame only
        self.pointer = None  # logical (x, y) or None
        self.warnings = []
        self._font = None

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    # ---- frames

    def emit(self, img, ms):
        ms = int(round(ms))
        if self.frames and same(self.frames[-1][0], img):
            self.frames[-1][1] += ms
        else:
            self.frames.append([img, ms])  # 0 ms frames stay, so a pause can lengthen them

    def full(self):
        return (0.0, 0.0, self.size[0], self.size[1])

    def compose(self, base, v=None, ring=None):
        """`base` with the caption, the pointer and a click ring on top. `v` is the view
        `base` shows, which places the pointer."""
        v = v or self.full()
        img = base.copy()
        if ring:
            at, r, alpha = ring
            self.draw_ring(img, self.to_out(at, v), r, alpha)
        if self.pointer:
            self.draw_pointer(img, self.to_out(self.pointer, v))
        if self.caption:
            self.draw_caption(img, self.caption)
        return img

    def to_out(self, p, v):
        k = self.out[0] / v[2]
        return ((p[0] - v[0]) * k, (p[1] - v[1]) * k)

    # ---- drawing

    def patch(self, box, mode, draw_fn):
        """Draw at SS times the size inside `box` (output px, floats), smooth it down, and
        return the patch with its integer origin."""
        x0, y0 = math.floor(box[0]) - 1, math.floor(box[1]) - 1
        x1, y1 = math.ceil(box[2]) + 1, math.ceil(box[3]) + 1
        big = Image.new(mode, ((x1 - x0) * SS, (y1 - y0) * SS), 0)
        draw_fn(ImageDraw.Draw(big), lambda x, y: ((x - x0) * SS, (y - y0) * SS))
        return big.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS), (x0, y0)

    def over(self, img, patch, at):
        """Composite the RGBA `patch` onto `img` at `at`, clipped to the frame."""
        x, y = at
        box = (max(0, x), max(0, y), min(img.width, x + patch.width), min(img.height, y + patch.height))
        if box[0] >= box[2] or box[1] >= box[3]:
            return
        region = img.crop(box).convert("RGBA")
        part = patch.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
        img.paste(Image.alpha_composite(region, part).convert("RGB"), box[:2])

    def draw_pointer(self, img, at):
        k = self.st["pointer"] / 18.0 * self.scale
        shape = [(0, 0), (0, 15.5), (3.8, 12.2), (6.4, 18), (9, 16.9), (6.5, 11.3), (11.4, 11.3)]
        pts = [(at[0] + x * k, at[1] + y * k) for x, y in shape]
        box = (min(p[0] for p in pts) - 2 * k, min(p[1] for p in pts) - 2 * k,
               max(p[0] for p in pts) + 3 * k, max(p[1] for p in pts) + 3 * k)

        def draw(d, t):
            d.polygon([t(x + 0.6 * k, y + 1.0 * k) for x, y in pts], fill=(0, 0, 0, 70))
            d.polygon([t(x, y) for x, y in pts], fill=(255, 255, 255, 255),
                      outline=(0, 0, 0, 255), width=max(1, round(1.2 * k * SS)))

        p, o = self.patch(box, "RGBA", draw)
        self.over(img, p, o)

    def draw_ring(self, img, at, r_logical, alpha):
        r = r_logical * self.scale
        wdt = 2.5 * self.scale
        col = self.st["accent"] + (round(255 * alpha),)

        def draw(d, t):
            d.ellipse([t(at[0] - r, at[1] - r), t(at[0] + r, at[1] + r)], outline=col,
                      width=max(1, round(wdt * SS)))

        p, o = self.patch((at[0] - r - wdt, at[1] - r - wdt, at[0] + r + wdt, at[1] + r + wdt),
                          "RGBA", draw)
        self.over(img, p, o)

    def font(self):
        if self._font:
            return self._font
        size = round(self.st["caption_size"] * self.scale * SS)
        if self.st["caption_font"]:
            try:
                self._font = ImageFont.truetype(self.st["caption_font"], size)
            except OSError as e:
                raise FilmError(f"style caption_font: can't load {self.st['caption_font']}: {e}") from None
            return self._font
        for p in MONO_FONTS:
            try:
                self._font = ImageFont.truetype(p, size)
                return self._font
            except OSError:
                continue
        self._font = ImageFont.load_default(size)
        return self._font

    def draw_caption(self, img, text):
        """A pill at the bottom right, laid out from the font's metrics, so its size and
        baseline don't move as the letters change."""
        f = self.font()
        missing = [ch for ch in text if not ch.isspace() and f.getmask(ch).getbbox() is None]
        if missing:
            self.warn(f"the caption font has no glyph for {''.join(missing)!r}; "
                      "set style caption_font to one that does")
        ascent, descent = f.getmetrics()
        tw, th = f.getlength(text) / SS, (ascent + descent) / SS
        padx, pady = 9 * self.scale, 4 * self.scale
        m = 14 * self.scale
        w, h = tw + 2 * padx, th + 2 * pady
        if w > img.width - 2 * m:
            self.warn(f"caption {text!r} is wider than the frame and is cut off")
        x1, y1 = img.width - m, img.height - m
        box = (x1 - w, y1 - h, x1, y1)
        baseline = box[1] + pady + ascent / SS

        def draw(d, t):
            d.rounded_rectangle([t(box[0], box[1]), t(box[2], box[3])], radius=h / 2 * SS * 0.6,
                                fill=(28, 28, 30, 225), outline=(255, 255, 255, 70),
                                width=max(1, round(self.scale * SS)))
            d.text(t(box[0] + padx, baseline), text, font=f, fill=(238, 238, 238, 255), anchor="ls")

        p, o = self.patch(box, "RGBA", draw)
        self.over(img, p, o)

    # ---- steps

    def run(self, steps):
        if not isinstance(steps, list):
            raise FilmError("steps must be a list")
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise FilmError(f"step {i}: must be an object, not {step!r}")
            kinds = [k for k in STEP_KEYS if k in step]
            if len(kinds) != 1:
                raise FilmError(f"step {i}: needs exactly one of {', '.join(STEP_KEYS)}: {step}")
            extra = {k for k in step if not k.startswith("_")} - STEP_KEYS[kinds[0]]
            if extra:
                raise FilmError(f"step {i} ({kinds[0]}): unknown keys {', '.join(sorted(extra))}")
            if kinds[0] != "shot" and not self.shot:
                raise FilmError(f"step {i}: {kinds[0]} before the first shot")
            try:
                getattr(self, "step_" + kinds[0])(step)
            except FilmError as e:
                raise FilmError(f"step {i} ({kinds[0]}): {e}") from None
        if not self.frames:
            raise FilmError("no frames: the manifest has no shots")
        if self.loop == "dip" and len(self.frames) > 1:
            self.dip(self.frames[-1][0], self.frames[0][0])

    def ms(self, s, key, default):
        return number(s.get(key, default), key, minimum=0)

    def step_shot(self, s):
        path = self.root / s["shot"]
        hires = self.root / s["hires"] if s.get("hires") else None
        if "hires_scale" in s and not hires:
            raise FilmError("hires_scale without hires")
        caption = s.get("caption")
        if caption is not None and (not isinstance(caption, str) or any(c in caption for c in "\r\n")):
            raise FilmError(f"caption: {caption!r} must be one line of text")
        shot = Shot(path, s.get("scale", 2), hires, s.get("hires_scale"), self.scale, self.st["matte"])
        if self.size is None:
            self.size, self.out = shot.logical, shot.out
        elif shot.out != self.out or max(abs(a - b) for a, b in zip(shot.logical, self.size)) > 0.5:
            raise FilmError(
                f"{s['shot']}: {shot.logical[0]:g}x{shot.logical[1]:g} logical px "
                f"({shot.out[0]}x{shot.out[1]} in the film), but the film is "
                f"{self.size[0]:g}x{self.size[1]:g} ({self.out[0]}x{self.out[1]})")
        else:
            shot.logical = self.size
        via = s.get("via", "cut")
        if via not in ("cut", "dip"):
            raise FilmError(f'via must be "cut" or "dip", not {via!r}')
        prev = self.frames[-1][0] if self.frames else None
        self.shot, self.caption = shot, caption
        frame = self.compose(shot.base)
        if via == "dip" and prev is not None:
            self.dip(prev, frame)
        self.emit(frame, self.ms(s, "hold", 1000))
        self.caption = None

    def dip(self, a, b):
        """A small change (a caption, a badge) crossfades. A bigger one fades the whole
        screen to its background colour and the new screen in, so two screens of text
        never overlap."""
        box = ImageChops.difference(a, b).getbbox()
        if not box:
            return
        n, ms = self.st["dip"], self.st["dip_ms"]
        area = (box[2] - box[0]) * (box[3] - box[1]) / (a.width * a.height)
        if area < SMALL_CHANGE:
            for i in range(1, 2 * n + 1):
                self.emit(Image.blend(a, b, ease(i / (2 * n + 1))), ms)
            return
        flat = Image.new("RGB", a.size, main_colour(a))
        for i in range(1, n + 1):
            self.emit(Image.blend(a, flat, ease(i / n)), ms)
        for i in range(1, n + 1):
            self.emit(Image.blend(flat, b, ease(i / (n + 1))), ms)

    def rect(self, s, key):
        r = s[key]
        if not isinstance(r, list) or len(r) != 4:
            raise FilmError(f"needs [x, y, w, h], not {r!r}")
        r = [number(v, key) for v in r]
        if r[2] <= 0 or r[3] <= 0:
            raise FilmError(f"needs w and h above 0: {r}")
        return r

    def step_spotlight(self, s):
        x, y, w, h = self.rect(s, "spotlight")
        pad = number(s.get("pad", 0), "pad")
        x, y, w, h = x - pad, y - pad, w + 2 * pad, h + 2 * pad
        k = self.scale
        box = (x * k, y * k, (x + w) * k, (y + h) * k)
        ring = self.st["ring"] * k
        rad = number(s.get("radius", self.st["radius"]), "radius", minimum=0) * k
        base = self.shot.base
        dark = Image.eval(base, lambda v: int(v * self.st["dim"]))

        def hole(d, t):
            d.rounded_rectangle([t(box[0], box[1]), t(box[2], box[3])], radius=rad * SS, fill=255)

        mask = Image.new("L", base.size, 0)
        m, o = self.patch(box, "L", hole)
        mask.paste(m, o)
        lit = Image.composite(base, dark, mask)
        if ring > 0:
            outer = (box[0] - ring, box[1] - ring, box[2] + ring, box[3] + ring)

            def frame(d, t):
                d.rounded_rectangle([t(outer[0], outer[1]), t(outer[2], outer[3])],
                                    radius=(rad + ring) * SS if rad else 0,
                                    outline=self.st["accent"] + (255,), width=max(1, round(ring * SS)))

            p, o = self.patch(outer, "RGBA", frame)
            self.over(lit, p, o)
        plain, lit = self.compose(base), self.compose(lit)
        n, ms = self.st["fade"], self.st["frame_ms"]
        for i in range(1, n + 1):
            self.emit(Image.blend(plain, lit, ease(i / (n + 1))), ms)
        self.emit(lit, self.ms(s, "hold", 1400))
        for i in range(n, 0, -1):
            self.emit(Image.blend(plain, lit, ease(i / (n + 1))), ms)
        self.emit(plain, self.ms(s, "after", self.st["after"]))

    def zoom_view(self, target, pad):
        W, H = self.size
        x, y, w, h = target[0] - pad, target[1] - pad, target[2] + 2 * pad, target[3] + 2 * pad
        if w / h < W / H:
            w2 = h * W / H
            x, w = x - (w2 - w) / 2, w2
        else:
            h2 = w * H / W
            y, h = y - (h2 - h) / 2, h2
        if w > W:
            return self.full()
        return (min(max(x, 0), W - w), min(max(y, 0), H - h), w, h)

    def step_zoom(self, s):
        target = self.rect(s, "zoom")
        end = self.zoom_view(target, number(s.get("pad", 24), "pad"))
        W, H = self.size
        if W / end[2] < 1.15:
            self.warn(f"zoom on {target}: it nearly fills the frame, so there's little to zoom; "
                      "zoom on part of it")
        elif not self.shot.sharp_zoom(end):
            need = self.out[0] / end[2]
            self.warn(f"zoom on {target}: soft; capture {self.shot.path.name} at scale >= {need:.1f}, "
                      "or give the shot a sharper 'hires' image")

        def at(e):
            w = min(W, math.exp(math.log(W) + (math.log(end[2]) - math.log(W)) * e))
            h = w * H / W
            cx = W / 2 + (end[0] + end[2] / 2 - W / 2) * e
            cy = H / 2 + (end[1] + end[3] / 2 - H / 2) * e
            v = (min(max(cx - w / 2, 0), W - w), min(max(cy - h / 2, 0), H - h), w, h)
            return self.compose(self.shot.view(v), v)

        ms = self.st["frame_ms"]
        n = self.st["zoom_in"]
        for i in range(1, n + 1):
            self.emit(at(ease(i / n)), ms if i < n else self.ms(s, "hold", 1800))
        n = self.st["zoom_out"]
        for i in range(n - 1, -1, -1):
            self.emit(at(ease(i / n)), ms if i else self.ms(s, "after", self.st["after"]))

    def step_click(self, s):
        to = s["click"]
        if not isinstance(to, list) or len(to) != 2:
            raise FilmError(f"needs [x, y], not {to!r}")
        to = [number(v, "click") for v in to]
        W, H = self.size
        start = self.pointer or (min(to[0] + 90, W - 20), min(to[1] + 70, H - 30))
        steps = max(4, round(self.st["move_ms"] / self.st["frame_ms"]))
        for i in range(1, steps + 1):
            e = ease(i / steps)
            self.pointer = (start[0] + (to[0] - start[0]) * e, start[1] + (to[1] - start[1]) * e)
            self.emit(self.compose(self.shot.base), self.st["frame_ms"])
        n = self.st["click"]
        for i in range(n):
            t = i / n
            ring = (to, 5 + 17 * ease(t), 0.95 * (1 - t))
            self.emit(self.compose(self.shot.base, ring=ring), self.st["frame_ms"])
        self.emit(self.compose(self.shot.base), self.ms(s, "hold", 120))

    def step_pointer(self, s):
        p = s["pointer"]
        if p in (None, "hide"):
            self.pointer = None
        elif isinstance(p, list) and len(p) == 2:
            self.pointer = tuple(number(v, "pointer") for v in p)
        else:
            raise FilmError(f'needs [x, y] or "hide", not {p!r}')
        self.emit(self.compose(self.shot.base), self.ms(s, "hold", 0))

    def step_pause(self, s):
        self.frames[-1][1] += int(number(s["pause"], "pause", minimum=0))

    # ---- output

    def write(self, out, effort=75):
        """Lossless always. `effort` 75 encodes a 100-frame film in seconds; 100 is ~4%
        smaller and ~35 times slower."""
        for f in self.frames:
            f[1] = max(f[1], MIN_MS)
        imgs = [f[0] for f in self.frames]
        imgs[0].save(out, "WEBP", save_all=True, append_images=imgs[1:],
                     duration=[f[1] for f in self.frames], loop=0,
                     lossless=True, quality=effort, method=6)

    def dump(self, d):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("[0-9][0-9][0-9].png"):
            old.unlink()
        with open(d / "durations", "w") as fh:
            for i, (img, ms) in enumerate(self.frames):
                img.save(d / f"{i:03}.png")
                fh.write(f"{i:03}.png {ms}\n")

    def sheet(self, path, cols=4, width=420):
        """Every frame, small, numbered with its duration: one image to check a film by."""
        w0, h0 = self.frames[0][0].size
        th = round(width * h0 / w0)
        rows = math.ceil(len(self.frames) / cols)
        label = 22
        sheet = Image.new("RGB", (cols * (width + 8) + 8, rows * (th + label + 8) + 8), (40, 40, 44))
        d = ImageDraw.Draw(sheet)
        f = ImageFont.load_default(15)
        for i, (img, ms) in enumerate(self.frames):
            x = 8 + (i % cols) * (width + 8)
            y = 8 + (i // cols) * (th + label + 8)
            sheet.paste(img.resize((width, th), Image.Resampling.BOX), (x, y + label))
            d.text((x + 2, y + 2), f"{i}  {ms} ms", font=f, fill=(230, 230, 230))
        sheet.save(path)


def same(a, b):
    return a.size == b.size and ImageChops.difference(a, b).getbbox() is None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="filmstrip", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True, help="the .webp to write")
    ap.add_argument("--frames", type=Path, help="also write every frame as PNG here")
    ap.add_argument("--sheet", type=Path, help="also write a contact sheet of every frame")
    ap.add_argument("--max", action="store_true",
                    help="smallest file: ~4%% smaller, ~35x slower to encode")
    a = ap.parse_args(argv)
    if a.out.suffix.lower() != ".webp":
        sys.exit("filmstrip: the output is lossless WebP; name it .webp")
    try:
        manifest = json.loads(a.manifest.read_text())
        film = Film(manifest, a.manifest.parent)
        film.run(manifest.get("steps", []))
        a.out.parent.mkdir(parents=True, exist_ok=True)
        film.write(a.out, effort=100 if a.max else 75)
        if a.frames:
            film.dump(a.frames)
        if a.sheet:
            a.sheet.parent.mkdir(parents=True, exist_ok=True)
            film.sheet(a.sheet)
    except (FilmError, OSError, KeyError, ValueError, TypeError, RuntimeError) as e:
        sys.exit(f"filmstrip: {e}")
    total = sum(f[1] for f in film.frames)
    w, h = film.frames[0][0].size
    print(f"{a.out}: {len(film.frames)} frames, {total / 1000:.1f} s, {w}x{h}, "
          f"{a.out.stat().st_size / 1e6:.2f} MB")
    for warning in film.warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
