"""filmstrip.web end to end: the sample capture through a real headless browser.

Skipped when Playwright or its browser isn't installed.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

pytest.importorskip("playwright")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tides_capture", ROOT / "examples/tides/capture.py")
tides = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tides)


def capture(out):
    try:
        return tides.capture(out)
    except Exception as e:  # a missing browser is a setup problem, not a failure
        if "Executable doesn't exist" in str(e):
            pytest.skip("run: python -m playwright install chromium-headless-shell")
        raise


def test_the_sample_captures_and_films(tmp_path):
    from filmstrip.film import main

    manifest = capture(tmp_path / "cap")
    steps = json.loads(manifest.read_text())["steps"]
    kinds = [next(k for k in s if k in ("shot", "click", "spotlight", "zoom", "pointer")) for s in steps]
    assert kinds.count("shot") == 9 and "spotlight" in kinds and "zoom" in kinds
    out = tmp_path / "tides.webp"
    main([str(manifest), "-o", str(out)])
    im = Image.open(out)
    assert im.n_frames > 50 and im.size == (1960, 1280)


def test_captures_are_deterministic(tmp_path):
    a = capture(tmp_path / "a")
    b = capture(tmp_path / "b")
    for shot in sorted((a.parent / "shots").glob("*.png")):
        other = b.parent / "shots" / shot.name
        assert Image.open(shot).tobytes() == Image.open(other).tobytes(), shot.name
