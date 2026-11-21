"""Compare each captured frame with its Figma export and say WHERE they differ.

A single percentage tells you a frame is wrong but not what to change, so this also prints the worst
bands and blocks in design pixels - coordinates that can be looked up in the frame.

Both sides are PNG at the same size, so nothing is rescaled and nothing is compression noise.
"""
import json
import pathlib
import sys

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
FRAMES = json.loads((HERE / "_frames.json").read_text())
only = sys.argv[1] if len(sys.argv) > 1 else None

print(f'{"frame":14} {"size":11} {"mean/255":>9} {"%>40":>7} {"%>80":>7}')
print("-" * 54)
rows = []
for key, (w, h) in FRAMES.items():
    if only and only != key:
        continue
    f = HERE / "_compare" / "figma" / f"{key}.png"
    d = HERE / "_compare" / "demo" / f"{key}.png"
    if not f.exists() or not d.exists():
        print(f"{key:14} MISSING ({'figma' if not f.exists() else 'demo'})")
        continue
    fi = Image.open(f).convert("RGB")
    de = Image.open(d).convert("RGB")
    if fi.size != de.size:
        print(f"{key:14} SIZE MISMATCH figma {fi.size} vs demo {de.size}")
        de = de.resize(fi.size, Image.LANCZOS)
    a = np.asarray(fi, dtype=np.int16)
    b = np.asarray(de, dtype=np.int16)
    diff = np.abs(a - b).mean(axis=2)
    rows.append((key, diff))
    print(f"{key:14} {fi.size[0]}x{fi.size[1]:<6} {diff.mean():9.2f} "
          f"{100*(diff>40).mean():6.2f}% {100*(diff>80).mean():6.2f}%")
    (HERE / "_compare" / "diff").mkdir(exist_ok=True)
    Image.fromarray(np.clip(diff * 3, 0, 255).astype("uint8")).save(
        HERE / "_compare" / "diff" / f"{key}.png")

for key, diff in rows:
    H, W = diff.shape
    band = diff.mean(axis=1)
    worst_y = int(np.argmax(np.convolve(band, np.ones(40) / 40, mode="same")))
    print(f"\n{key}: worst horizontal band near y={worst_y} (mean {band[worst_y]:.1f})")
    sc = []
    for r in range(10):
        for c in range(10):
            blk = diff[r*H//10:(r+1)*H//10, c*W//10:(c+1)*W//10]
            sc.append((blk.mean(), r*H//10, (r+1)*H//10, c*W//10, (c+1)*W//10))
    sc.sort(reverse=True)
    for s, y0, y1, x0, x1 in sc[:3]:
        print(f"   mean {s:5.1f}  y {y0}-{y1}  x {x0}-{x1}")
