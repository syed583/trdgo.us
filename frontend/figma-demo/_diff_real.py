"""Compare each REAL page with its Figma export, and say where they differ.

READ THE CAVEAT BEFORE THE NUMBERS. There is no backend attached, so every data panel on the real
side shows its empty state while the Figma side is full of sample values. A whole-image percentage
therefore measures mostly "the design has data and this capture does not", which is not a defect.

So this reports two things separately:
  * CHROME - the left rail and the top bar, which carry no page data and are identical on every
    frame. This is a real fidelity measure and should be low.
  * PAGE   - everything right of the rail and below the top bar, reported only as context.

Both sides are PNG at the same canvas size, so nothing is rescaled and nothing is JPEG noise.
"""
import json
import pathlib

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
PAGES = json.loads((HERE / "_pages.json").read_text())

RAIL_W = 247      # --nu-rail-w
BAR_H = 64        # --nu-bar-h

print(f'{"page":12} {"size":10} {"thm":4} {"chrome mean":>12} {"chrome %>40":>12} {"page mean":>10}')
print("-" * 62)
for key, (route, w, h, theme) in PAGES.items():
    fp = HERE / "_compare" / "figma" / f"{key}.png"
    rp = HERE / "_compare" / "real" / f"{key}.png"
    if not (fp.exists() and rp.exists()):
        print(f"{key:12} MISSING {'figma' if not fp.exists() else 'real'}")
        continue
    a = np.asarray(Image.open(fp).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(rp).convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        print(f"{key:12} size mismatch figma={a.shape[1]}x{a.shape[0]} real={b.shape[1]}x{b.shape[0]}")
        continue
    d = np.abs(a - b).max(axis=2)
    rail = d[:, :RAIL_W]
    bar = d[:BAR_H, RAIL_W:]
    chrome = np.concatenate([rail.ravel(), bar.ravel()])
    body = d[BAR_H:, RAIL_W:]
    print(f"{key:12} {w}x{h:<5} {theme[:4]:4} {chrome.mean():12.1f} {100*(chrome>40).mean():11.1f}% {body.mean():10.1f}")
