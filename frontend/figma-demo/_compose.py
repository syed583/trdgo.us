"""Compose the Figma export and the live capture into one side-by-side image per page.

Both halves are already at the frame's own canvas size, so neither is rescaled here - if the two
are different sizes that is a fact about the pair and the image says so in its caption rather than
hiding it behind a resize.

    python -I _compose.py [pageKey ...]
"""
import io
import json
import sys

from PIL import Image, ImageDraw

SPEC = json.load(io.open("_frames.json", encoding="utf-8"))
keys = [k for k in sys.argv[1:] if k in SPEC] or [k for k in SPEC if not k.startswith("_")]

for key in keys:
    spec = SPEC[key]
    try:
        a = Image.open(f"_compare/figma/{key}.png").convert("RGB")
        b = Image.open(f"_compare/live/{key}.png").convert("RGB")
    except FileNotFoundError as exc:
        print(f"{key}: {exc}")
        continue
    gap, band = 8, 22
    h = max(a.height, b.height)
    out = Image.new("RGB", (a.width + b.width + gap, h + band), "white")
    out.paste(a, (0, band))
    out.paste(b, (a.width + gap, band))
    d = ImageDraw.Draw(out)
    d.text((6, 6), f"FIGMA frame  {a.width}x{a.height} {spec['theme']}", fill="red")
    d.text((a.width + gap + 8, 6),
           f"LIVE {spec['route']}  {b.width}x{b.height} {spec['theme']}", fill="red")
    path = f"_compare/pairs/{key}_sxs.png"
    out.save(path)
    print(f"{path}  {out.size}")
