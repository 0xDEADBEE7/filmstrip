"""Capture the invented Tidewatch page beside this file: filmstrip's sample and end-to-end test.

    python examples/tides/capture.py capture/tides
    filmstrip capture/tides/manifest.json -o tides.webp --sheet tides-sheet.png
"""

import sys
from pathlib import Path

from filmstrip.web import Recorder

HERE = Path(__file__).resolve().parent


def capture(out):
    page = (HERE / "tides.html").as_uri()
    with Recorder(out, page, viewport=(980, 640), now="2026-11-27T09:00:00") as rec:
        rec.shot(hold=1300)
        rec.click("#search", hold=300)
        rec.type("#search", "port", per_key=110, hold=900)
        rec.click("text=Portland", hold=1300, via="dip")
        rec.spotlight("#next-high", hold=1400)
        rec.zoom("#chart", hold=1800, part=(0.15, 0, 0.65, 1))
        rec.click("#back", hold=700, via="dip")
        rec.press("Escape", hold=1100, caption="Esc")
        rec.pointer(None)
    return Path(out) / "manifest.json"


if __name__ == "__main__":
    print(capture(sys.argv[1] if len(sys.argv) > 1 else "capture/tides"))
