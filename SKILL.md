---
name: filmstrip
description: Make short looping animations that show software working (a web app, the DIE editor or its extensions, any app you can screenshot) for READMEs, docs and pull requests. A script captures each screen, and filmstrip composes them, with spotlights, zooms, a pointer with clicks, key captions, cuts and fades, into a lossless animated WebP. Use when asked for a demo GIF, animation, screencast, product tour or walkthrough clip, to record or re-record one, to make one shorter, smaller or clearer, or to add an effect to one.
---

# filmstrip

Make the animation from a script, not a screen recording:

1. **Capture.** A script drives the app to each state and saves a screenshot: Playwright (`filmstrip.web`) for web apps, a native renderer such as DIE's for apps that draw themselves. For each screen it also records how long it shows, and where the things you'll point at are.
2. **Film.** `filmstrip` turns the screens and effects into the frames of a loop and writes **lossless animated WebP**.

```
capture script ──> shots/*.png + manifest.json ──> filmstrip ──> demo.webp (+ sheet.png to check it)
```

Scripting it makes the result:

- **reproducible:** after a UI change, re-record with one command;
- **private:** invented data, no real names, paths or emails;
- **sharp:** text captured at 2–4x;
- **small:** each frame stores only what changed;
- **precise:** effects aim at elements by selector or by text.

Only record the screen live (QuickTime) for what no script can drive.

This folder is the filmstrip repository:
- `src/filmstrip/film.py`: the `filmstrip` command;
- `src/filmstrip/web.py`: the Playwright recorder;
- `examples/tides/`: an invented web app and its capture script, which is also the end-to-end test;
- `tests/`: the regression tests.

## Setup (once per machine)

```sh
SKILL=~/.claude/skills/filmstrip   # this folder
python3 -m venv ~/.cache/filmstrip/venv
~/.cache/filmstrip/venv/bin/pip install -e "$SKILL[web]"
~/.cache/filmstrip/venv/bin/python -m playwright install chromium-headless-shell
```

If `~/.cache/filmstrip/venv/bin/filmstrip` exists, setup is done. Run capture scripts with `~/.cache/filmstrip/venv/bin/python`.

Smoke test (about 10 s):

```sh
PY=~/.cache/filmstrip/venv/bin/python; T=$(mktemp -d)
$PY $SKILL/examples/tides/capture.py $T/cap && ~/.cache/filmstrip/venv/bin/filmstrip $T/cap/manifest.json -o $T/tides.webp --sheet $T/sheet.png
```

In another project, `pip install "filmstrip[web] @ git+https://github.com/0xDEADBEE7/filmstrip"` does the same.

## Capture a web app: `filmstrip.web`

```python
from filmstrip.web import Recorder

with Recorder("capture/demo", "http://localhost:5173/", viewport=(1100, 680),
              now="2026-03-02T09:30:00") as rec:
    rec.shot(hold=1300)                                    # always start with a shot
    rec.click("#search", hold=300)                         # pointer glides there, clicks
    rec.type("#search", "port", per_key=110, hold=900)     # one shot per key
    rec.click("text=Portland", hold=1300, via="dip", wait="#chart")  # a new page
    rec.spotlight("#next-high", hold=1400)                 # dim the rest, ring it
    rec.zoom("#chart", hold=1800, part=(0.15, 0, 0.65, 1)) # zoom on part of the chart
    rec.press("Escape", hold=1100, caption="Esc")          # captioned key
# Leaving the `with` block writes capture/demo/manifest.json (not if it raised).
```

Run it against the app's dev server (start it first and wait until it answers), a built copy, or a `file://` page.

**What it does for you:**
- It captures at `scale=4` device pixels per CSS pixel. The film is drawn at 2x, so zooms up to 2× stay sharp.
- **Before every capture,** it waits until no request has been in flight for 250 ms, fonts and images have loaded, and scrolling has stopped. A page that never goes quiet (polling) costs `settle_timeout_ms` (5000) per capture, and it prints a warning saying so.
- **For app-specific readiness**, such as a debounced search or a result rendered after an animation frame, pass `wait=` a selector to wait for, or a `fn(page)`.
- **It quiets the page:** animations and transitions off, caret and scrollbars hidden, smooth scrolling off, reduced motion. This also applies to `set_content` pages.
- **`now` fixes the page's `Date`** (ISO string or datetime). A bare time is read in the page's timezone: UTC unless `context_options={"timezone_id": ...}`. The locale is en-GB.
- **Timers still run in real time.** The page doesn't know how long a shot is held in the film. To show something that changes after a delay (a toast that disappears, a spinner that finishes), wait for it in the script, e.g. `rec.act(lambda p: p.wait_for_timeout(4000), capture=False)` before the next shot.

**Methods**, each taking a selector or Playwright Locator:

| Method | What it does |
| --- | --- |
| `shot(hold, caption, via)` | Settle, then capture. |
| `click(target, hold, via, caption, glide=True, wait)` | Glide the pointer to the target's centre, ring the click, click for real, then capture. |
| `type(target, text, per_key, hold, caption=False, wait)` | Type at the end of the target's text, one key at a time, capturing each. |
| `press(key, hold, caption, via, wait)` | Press a key (Playwright names: `"Enter"`, `"Control+K"`), then capture. |
| `act(fn, hold, caption, via, wait, capture=True)` | Run any `fn(page)`, then capture. `capture=False` runs it off camera. |
| `scroll(target or y=, hold=700, via, align="center")` | Bring a target into view (or scroll to `y`), then capture. |
| `spotlight(target, hold, pad=6, radius=6, part)` | Spotlight effect (see below). |
| `zoom(target, hold, pad=24, part)` | Zoom effect (see below). |
| `pointer(at or None)` | Show the pointer at a point or target, or hide it. |
| `pause(ms)` | Hold the last frame longer. |

- **`part=(left, top, right, bottom)`** takes a fraction of the target's box, e.g. one region of a wide chart. A zoom on a full-width element has nothing to zoom into without it.
- **Effects and clicks aim at the last shot.** Their target must be on screen there, and the page must not have scrolled since. Otherwise they raise ``target is off-screen … rec.scroll(target) first``. A missing or hidden target raises at once.
- **`rec.page`** is the Playwright page, for anything else.
- **`headless=False`** needs the full browser: `python -m playwright install chromium`.

**Data.** Point the app at fixtures, or mock its API with `rec.page.route(...)`, so every run shows the same invented names, numbers and dates. Never record a real account.

## Capture a native renderer (DIE)

An app that can draw its own screen into a PNG at a chosen scale makes the sharpest films. Draw each screen at 2x, and draw the screens you'll zoom into at 4x too (`"hires"`). Then find the targets in the app's own model of the screen, not in pixels.

DIE's example is `examples/die_shots.rs` in the die-config repository. Run it with `cargo run --release --example die_shots -- OUT`, then `filmstrip OUT/manifest.json -o delta.webp`.

- **Recording.** It drives core's `Harness` with no window, through die-config's keys, theme and faces. It draws as DIE.app does: `Renderer::new(&Font::term(16.0, 2.0))`, a cell 19 × 40 px, which is 9.5 × 20 logical.
- **Captions.** It writes key captions into the empty command line itself.
- **Targets.** It finds them from the scene's text, e.g. the row containing `→`, or the first bold cell on a `+` line.
- **Invented repository.** It reuses `Repo` and the history from `delta_shot.rs`.

DIE-specific lessons:
- Give the status line a fake HOME (`die_dev::status_text(home, Some(home))`) so it reads `~/`.
- Size the window's columns so nothing wraps.
- Assert every key is error-free.
- Call `run_until_idle()` after each key so background work such as git finishes.
- In a search prompt `j` types into the query; `<Esc>` leaves the prompt and keeps the query.

## Capture anything else

Any PNG screenshots work. Write the manifest by hand:

- A shot's `scale` is its pixels per logical pixel. The default is 2, a Retina screenshot.
- Targets are in logical pixels, so halve what an image editor shows for a Retina screenshot.
- Transparent pixels (window corners) show `style.matte`, or by default the image's main colour.

## The manifest

JSON. Coordinates are logical pixels, which are CSS pixels for a web page. Every screenshot must be the same logical size, and the same pixel size once drawn at the film's scale.

```json
{
  "scale": 2,
  "loop": "dip",
  "style": {"accent": "#e0b050", "_note": "keys starting with _ are comments, here too"},
  "steps": [
    {"shot": "shots/000.png", "scale": 4, "hold": 1300},
    {"click": [412, 207]},
    {"shot": "shots/001.png", "scale": 4, "hold": 1300, "via": "dip", "caption": "RET"},
    {"spotlight": [40, 300, 320, 96], "pad": 6, "radius": 6, "hold": 1400},
    {"zoom": [180, 420, 460, 220], "pad": 24, "hold": 1800},
    {"pointer": "hide"},
    {"pause": 500}
  ]
}
```

**Top level**

| Key | Meaning | Default |
| --- | --- | --- |
| `scale` | The film's pixels per logical pixel. It doesn't change a shot's own `scale`. | 2 |
| `loop` | `dip` blends the end into the start; `cut` jumps. | `dip` |
| `style` | Overrides for `STYLE` (below). Values are checked. | |

**Steps**

| Step | Keys (default) | What it does |
| --- | --- | --- |
| `shot` | `scale` (2), `hires`, `hires_scale` (inferred), `hold` (1000), `caption`, `via` (`cut`) | A new screen. `hires` is a sharper copy of the same screen, used only for zooms. `caption` puts a one-line key-caption pill bottom right, on this shot's own frame only. |
| `spotlight` | `[x,y,w,h]`, `pad` (0), `radius` (style), `hold` (1400), `after` (250) | Dim everything else to 35%, ring the rectangle, hold, then undo. The screen doesn't change. |
| `zoom` | `[x,y,w,h]`, `pad` (24), `hold` (1800), `after` (250) | Zoom until the rectangle, widened to the frame's shape, fills it; hold, then zoom out. |
| `click` | `[x,y]`, `hold` (120) | The pointer glides (480 ms) to the point and a ring pulses there. It stays on screen afterwards. |
| `pointer` | `[x,y]` or `"hide"`, `hold` (0) | Show the pointer at a point, or hide it. |
| `pause` | ms | Lengthen the last frame. |

**`via`:**
- `cut` shows the new screen at once.
- `dip` handles a big change by fading the whole screen to its background colour, then fading the new one in (180 ms). A small change, under 15% of the frame such as a caption or a badge, simply crossfades.

Unknown keys are errors, so typos fail loudly. Keys starting with `_` are comments.

**`STYLE`** (defaults in `film.py`):
- `accent #e0b050` (`#rgb` works too), `dim 0.35`, `ring 2` (0 for none), `radius 0`;
- `frame_ms 40`, `fade 3`, `zoom_in 8`, `zoom_out 6`, `dip 3` (at `dip_ms 30`), `after 250`;
- `move_ms 480`, `click 4`, `pointer 20`;
- `caption_size 13`, `caption_font` (a .ttf/.ttc path; Menlo or another monospace font by default), `matte` (none).

## filmstrip

```sh
filmstrip capture/demo/manifest.json -o demo.webp [--sheet sheet.png] [--frames frames/] [--max]
```

- **`--sheet`** writes every frame as a numbered thumbnail with its duration. Read it to check the film's flow. Then read the frames that matter at full size from `--frames` (emptied first): the first frame, each new screen, and each effect's peak (the frame holding 1400 or 1800 ms).
- **Warnings** flag:
  - a zoom that would enlarge pixels: capture at a higher scale or pass `hires`;
  - a zoom target that nearly fills the frame: zoom on `part` of it;
  - a caption cut off or missing a glyph.
- **`--max`** gives the smallest file: about 4% smaller, but about 35× slower to encode (around 100 s rather than 3 s for 100 frames). Use it only for the final file, if at all.

## Directing a good demo

- **One story, 10–15 seconds, three or four screens.** Show the main path, not every feature. 17 seconds dragged; 13 was right.
- **Holds:**
  - a new screen: 1.1–1.4 s;
  - a cursor or selection move: 260 ms, or 450 ms on the last move of a run;
  - a typed key: 90–110 ms;
  - a finished query: 900 ms;
  - a spotlight: 1.4 s;
  - a zoom: 1.8 s.
- **Real interactions are instant cuts.** Apps respond at once; a fade on every click misrepresents them.
- **`via="dip"` marks a jump:** a new page, or time skipped off camera. Never crossfade two screens of text: halfway through, the letters overlap into a jumble. filmstrip doesn't do that for big changes.
- **Cuts are free.** Do steps off camera, then dip into the result:
  - web: `rec.act(fn, capture=False)` or `rec.page` directly, then `rec.shot(via="dip")`;
  - DIE: `press` with no shot.
- **Use effects sparingly:** one spotlight and one or two zooms per film. Zoom to about 1.6–2× so whole lines stay readable. Spotlight the row, card or value the story is about.
- **Captions** show keys a viewer can't see being pressed (`Esc`, `RET`, `Ctrl+K`). A caption shows only on its own shot. Don't caption clicks; the pointer shows those.
- **Loop seamlessly.** End where you began (clear the search, go back), or let `loop: "dip"` blend the end into the start.
- **Use invented data that looks real:**
  - a small believable project with two or three people at `example.com`;
  - fixed dates;
  - nothing cut off or wrapped;
  - every feature the story sells visible at least once.

## Output

- **Lossless WebP only.** Lossy WebP leaves visible artifacts throughout an animation, and for text it isn't even smaller.
- **Typical sizes** at 2x: a plain 13 s tour is about 0.5 MB, and 1.3–2.7 MB with effects.
  - Every frame that changes the whole screen (zoom steps, dips, spotlight fades) costs about 50 KB.
  - Pointer glides are cheap.
  - Over ~3 MB, cut effect frames before reaching for `--max`.
- **Not GIF:** 256 colours band and dither anti-aliased text.
- **Not video in a repository:** GitHub plays video only from files uploaded as attachments, while a repository's `.webp` is served as `image/webp` and plays.

## In a README

- Put the animation straight after the one- or two-line pitch: `<img src="docs/demo.webp" width="100%" alt="…">`.
- Make the alt text tell the whole film step by step.
- Add a one-line caption: the data is invented, and what made it.
- Keep the capture script in the project (`examples/` or `scripts/`), with filmstrip as a dev dependency and the record command in the development docs, so anyone can re-record.
- Write captures to a gitignored folder (`capture/`) and commit only the `.webp`.
- After pushing, check that the README plays it.

## Checklist

1. The data is invented and fixed (clock, names, numbers), with no real paths, accounts or emails.
2. The viewport or window is sized so nothing wraps; every effect's target is on screen in its shot.
3. The film is 10–15 s, holds new screens 1.1–1.4 s, and has one spotlight and at most two zooms.
4. Real interactions cut, and jumps dip.
5. filmstrip printed no warnings; you've read the sheet and the key frames at full size.
6. It's lossless `.webp`, with size checked.
7. The README has full alt text, the caption, and the record command in its development docs.
