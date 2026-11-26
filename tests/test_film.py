"""filmstrip.film on synthetic screens: no browser needed."""

import json

import pytest
from PIL import Image, ImageDraw

from filmstrip.film import Film, FilmError, main

W, H = 400, 250  # logical px


def screen(path, scale=2, label="A", colour=(30, 30, 34), mark=(60, 40)):
    """A dark screen with some text-like bars and a label, at `scale`."""
    img = Image.new("RGB", (W * scale, H * scale), colour)
    d = ImageDraw.Draw(img)
    for i in range(6):
        y = (30 + i * 28) * scale
        d.rectangle([20 * scale, y, (20 + 40 * (i + 2)) * scale, y + 10 * scale], fill=(200, 200, 205))
    x, y = mark
    d.rectangle([x * scale, y * scale, (x + 30) * scale, (y + 20) * scale], fill=(80, 160, 90))
    d.text((300 * scale, 200 * scale), label, fill=(255, 255, 255))
    img.save(path)
    return path.name


def film(tmp_path, steps, **top):
    m = {"scale": 2, "steps": steps, **top}
    f = Film(m, tmp_path)
    f.run(m["steps"])
    return f


def render(tmp_path, steps, **top):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"scale": 2, "steps": steps, **top}))
    out = tmp_path / "out.webp"
    frames = tmp_path / "frames"
    main([str(manifest), "-o", str(out), "--frames", str(frames)])
    return out, frames


def decoded(path):
    """Every frame of an animated WebP, as RGB, with its duration."""
    im = Image.open(path)
    out = []
    for i in range(im.n_frames):
        im.seek(i)
        out.append((im.convert("RGB"), im.info["duration"]))
    return out


def test_output_is_lossless_and_keeps_durations(tmp_path):
    a = screen(tmp_path / "a.png", 4, "A")
    b = screen(tmp_path / "b.png", 4, "B", mark=(200, 120))
    out, frames = render(tmp_path, [
        {"shot": a, "scale": 4, "hold": 900},
        {"click": [210, 130]},
        {"shot": b, "scale": 4, "hold": 800, "via": "dip", "caption": "RET"},
        {"spotlight": [190, 110, 50, 40], "pad": 4, "radius": 6, "hold": 700},
        {"zoom": [150, 90, 120, 80], "hold": 600},
    ])
    want = [(Image.open(frames / name).convert("RGB"), int(ms))
            for name, ms in (line.split() for line in (frames / "durations").read_text().splitlines())]
    got = decoded(out)
    assert len(got) == len(want)
    for i, ((g, gms), (w, wms)) in enumerate(zip(got, want)):
        assert g.tobytes() == w.tobytes(), f"frame {i} differs"
        assert gms == wms, f"frame {i}: {gms} ms, want {wms} ms"


def test_identical_frames_merge_and_no_frame_is_too_short(tmp_path):
    a = screen(tmp_path / "a.png")
    f = film(tmp_path, [
        {"shot": a, "hold": 500},
        {"shot": a, "hold": 300},
        {"pause": 200},
        {"pointer": [100, 100]},
        {"pointer": "hide"},
    ], loop="cut")
    assert f.frames[0][1] == 1000
    out = tmp_path / "o.webp"
    f.write(out)
    assert all(ms >= 20 for _, ms in decoded(out))


def test_mixed_scales_and_hires(tmp_path):
    a = screen(tmp_path / "a.png", 2)
    b = screen(tmp_path / "b.png", 4, "B")
    hi = screen(tmp_path / "a4.png", 4)
    f = film(tmp_path, [
        {"shot": a, "scale": 2, "hires": hi, "hires_scale": 4, "hold": 500},
        {"zoom": [40, 30, 140, 90], "hold": 500},
        {"shot": b, "scale": 4, "hold": 500},
    ])
    assert all(img.size == (W * 2, H * 2) for img, _ in f.frames)
    assert not f.warnings
    # the zoom ends on the plain screen, exactly
    plain = Image.open(tmp_path / a).convert("RGB")
    assert any(img.tobytes() == plain.tobytes() for img, _ in f.frames[1:])


def test_soft_and_pointless_zooms_warn(tmp_path):
    a = screen(tmp_path / "a.png", 2)
    f = film(tmp_path, [{"shot": a}, {"zoom": [100, 100, 40, 20], "hold": 300}])
    assert any("soft" in w for w in f.warnings)
    f = film(tmp_path, [{"shot": a}, {"zoom": [0, 0, W, 60], "hold": 300}])
    assert any("nearly fills" in w for w in f.warnings)


@pytest.mark.parametrize("rect", [[0, 0, 50, 30], [W - 50, H - 30, 50, 30], [0, 0, W, H]])
def test_effects_at_the_edges(tmp_path, rect):
    a = screen(tmp_path / "a.png", 4)
    f = film(tmp_path, [
        {"shot": a, "scale": 4},
        {"spotlight": rect, "pad": 8, "radius": 6},
        {"zoom": rect},
        {"click": [rect[0], rect[1]]},
        {"click": [W - 1, H - 1]},
    ])
    assert len(f.frames) > 10


def test_loop_dip_returns_to_the_first_frame(tmp_path):
    a = screen(tmp_path / "a.png")
    b = screen(tmp_path / "b.png", label="B", mark=(250, 60))
    f = film(tmp_path, [{"shot": a, "hold": 500}, {"shot": b, "hold": 500}])
    assert len(f.frames) > 2  # the dip back to a was added
    c = film(tmp_path, [{"shot": a, "hold": 500}, {"shot": b, "hold": 500}], loop="cut")
    assert len(c.frames) == 2


@pytest.mark.parametrize("steps, error", [
    ([{"shot": "a.png", "hlod": 100}], "unknown keys hlod"),
    ([{"shot": "a.png", "via": "fade"}], "via must be"),
    ([{"zoom": [0, 0, 10, 10]}], "before the first shot"),
    ([{"pause": 100}], "before the first shot"),
    ([{"shot": "a.png", "zoom": [0, 0, 1, 1]}], "exactly one"),
    ([], "no frames"),
])
def test_bad_manifests_fail_clearly(tmp_path, steps, error):
    screen(tmp_path / "a.png")
    with pytest.raises(FilmError, match=error):
        film(tmp_path, steps)


def test_screens_must_be_the_same_size(tmp_path):
    a = screen(tmp_path / "a.png", 2)
    Image.new("RGB", (300, 300)).save(tmp_path / "small.png")
    with pytest.raises(FilmError, match="logical px"):
        film(tmp_path, [{"shot": a}, {"shot": "small.png"}])


def test_unknown_manifest_and_style_keys_fail(tmp_path):
    with pytest.raises(FilmError, match="manifest keys"):
        Film({"steps": [], "loops": "cut"}, tmp_path)
    with pytest.raises(FilmError, match="style keys"):
        Film({"steps": [], "style": {"accnet": "#fff"}}, tmp_path)


def test_output_must_be_webp(tmp_path):
    with pytest.raises(SystemExit, match="WebP"):
        main([str(tmp_path / "m.json"), "-o", str(tmp_path / "out.gif")])


# ---- regressions from review

def pill_box(frame, base):
    """Where a frame differs from its base: the caption pill."""
    from PIL import ImageChops
    return ImageChops.difference(frame, base).getbbox()


def test_hires_scale_is_inferred(tmp_path):
    a = screen(tmp_path / "a.png", 2)
    hi = screen(tmp_path / "a4.png", 4)
    f = film(tmp_path, [{"shot": a, "hires": hi}, {"zoom": [40, 30, 140, 90]}])
    assert not f.warnings  # the 4x image was used: no soft zoom


def test_a_screen_one_pixel_off_fails_clearly(tmp_path):
    a = screen(tmp_path / "a.png", 2)
    Image.new("RGB", (W * 2 + 1, H * 2)).save(tmp_path / "wide.png")
    with pytest.raises(FilmError, match="logical px"):
        film(tmp_path, [{"shot": a}, {"shot": "wide.png"}])


def test_a_shots_scale_doesnt_follow_the_films(tmp_path):
    a = screen(tmp_path / "a.png", 2)  # a 2x screenshot, the default
    f = film(tmp_path, [{"shot": a}], scale=1)
    assert f.frames[0][0].size == (W, H)


def test_captions_keep_their_size_and_baseline(tmp_path):
    a = screen(tmp_path / "a.png")
    f = film(tmp_path, [{"shot": a, "caption": c, "hold": 100} for c in ("a", "ap", "apt", "Esc")], loop="cut")
    base = Image.open(tmp_path / a).convert("RGB")
    boxes = [pill_box(img, base) for img, _ in f.frames]
    assert len({(b[1], b[3]) for b in boxes}) == 1, boxes  # same top and bottom for every caption


def test_a_caption_is_one_line(tmp_path):
    a = screen(tmp_path / "a.png")
    with pytest.raises(FilmError, match="one line"):
        film(tmp_path, [{"shot": a, "caption": "two\nlines"}])


def test_a_caption_wider_than_the_frame_warns(tmp_path):
    a = screen(tmp_path / "a.png")
    f = film(tmp_path, [{"shot": a, "caption": "x" * 80}])
    assert any("wider than the frame" in w for w in f.warnings)


def test_a_caption_lasts_only_for_its_shot(tmp_path):
    a = screen(tmp_path / "a.png")
    f = film(tmp_path, [{"shot": a, "caption": "RET", "hold": 500},
                        {"spotlight": [20, 30, 100, 40], "hold": 777}], loop="cut")
    base = Image.open(tmp_path / a).convert("RGB")
    pill = pill_box(f.frames[0][0], base)
    held = next(img for img, ms in f.frames if ms == 777)
    plain_spot = film(tmp_path, [{"shot": a, "hold": 500},
                                 {"spotlight": [20, 30, 100, 40], "hold": 777}], loop="cut")
    want = next(img for img, ms in plain_spot.frames if ms == 777)
    assert held.crop(pill).tobytes() == want.crop(pill).tobytes()


def test_an_effect_with_no_after_still_ends_on_the_plain_screen(tmp_path):
    a = screen(tmp_path / "a.png", 4)
    f = film(tmp_path, [{"shot": a, "scale": 4, "hold": 300},
                        {"zoom": [40, 30, 140, 90], "after": 0}, {"pause": 700}], loop="cut")
    assert f.frames[-1][0].tobytes() == f.frames[0][0].tobytes()
    assert f.frames[-1][1] == 700


def test_transparent_pixels_show_the_main_colour(tmp_path):
    img = Image.new("RGBA", (W * 2, H * 2), (240, 240, 240, 255))
    img.paste((0, 0, 0, 0), (0, 0, 40, 40))  # a transparent corner
    img.save(tmp_path / "t.png")
    f = film(tmp_path, [{"shot": "t.png"}])
    assert f.frames[0][0].getpixel((5, 5)) == (240, 240, 240)
    f = film(tmp_path, [{"shot": "t.png"}], style={"matte": "#fff"})
    assert f.frames[0][0].getpixel((5, 5)) == (255, 255, 255)


@pytest.mark.parametrize("style, error", [
    ({"zoom_in": 0}, "zoom_in"),
    ({"frame_ms": 0}, "frame_ms"),
    ({"dim": 2}, "dim"),
    ({"accent": "orange"}, "accent"),
    ({"caption_font": "/no/such/font.ttf"}, "caption_font"),
])
def test_bad_style_values_fail_clearly(tmp_path, style, error):
    a = screen(tmp_path / "a.png")
    with pytest.raises(FilmError, match=error):
        film(tmp_path, [{"shot": a, "caption": "x"}], style=style)


def test_short_hex_colours_and_comments_in_style(tmp_path):
    a = screen(tmp_path / "a.png")
    f = film(tmp_path, [{"shot": a}], style={"accent": "#fc0", "_why": "brand colour"})
    assert f.st["accent"] == (255, 204, 0)


def test_no_ring_when_ring_is_zero(tmp_path):
    a = screen(tmp_path / "a.png")
    with_ring = film(tmp_path, [{"shot": a}, {"spotlight": [100, 80, 120, 60], "hold": 777}], loop="cut")
    no_ring = film(tmp_path, [{"shot": a}, {"spotlight": [100, 80, 120, 60], "hold": 777}],
                   loop="cut", style={"ring": 0})
    lit = next(img for img, ms in with_ring.frames if ms == 777)
    bare = next(img for img, ms in no_ring.frames if ms == 777)
    edge = (100 * 2 - 2, 80 * 2 + 20)  # just outside the hole, where the ring would be
    assert lit.getpixel(edge) != bare.getpixel(edge)
    r, g, b = bare.getpixel(edge)
    assert abs(r - b) < 4  # dimmed grey, no amber


def test_output_and_frames_dirs(tmp_path):
    a = screen(tmp_path / "a.png")
    b = screen(tmp_path / "b.png", label="B", mark=(250, 60))
    out, frames = render(tmp_path, [{"shot": a}, {"shot": b, "via": "dip"}])
    many = len(list(frames.glob("*.png")))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"loop": "cut", "steps": [{"shot": a}]}))
    main([str(manifest), "-o", str(tmp_path / "new" / "dir" / "o.webp"), "--frames", str(frames)])
    assert (tmp_path / "new" / "dir" / "o.webp").exists()
    assert len(list(frames.glob("*.png"))) == 1 < many


def test_a_big_change_dips_the_whole_screen_and_a_small_one_crossfades(tmp_path):
    a = screen(tmp_path / "a.png")
    b = screen(tmp_path / "b.png", label="B", colour=(240, 240, 240))
    f = film(tmp_path, [{"shot": a, "hold": 500}, {"shot": b, "hold": 500, "via": "dip"}], loop="cut")
    darkest = f.frames[3][0]  # the end of the fade out: one flat colour, no rectangle
    assert len(darkest.getcolors()) == 1
    c = film(tmp_path, [{"shot": a, "hold": 500}, {"shot": a, "hold": 500, "caption": "Esc"}],
             loop="dip")
    base = Image.open(tmp_path / a).convert("RGB")
    pill = pill_box(c.frames[1][0], base)
    for img, _ in c.frames[2:]:  # the loop's dip touches only the caption
        box = pill_box(img, base)
        assert box is None or (box[0] >= pill[0] and box[1] >= pill[1] and box[2] <= pill[2] and box[3] <= pill[3])
