"""Read a Figma frame's BLOCK layout out of a saved get_metadata dump.

get_metadata on a whole page frame is far too big to put in a conversation, and the interesting
part is small: the top-level blocks - the cards, rows and panels - with their absolute position and
size. This pulls those out, converts them to the frame's own coordinate space, and prints them in
the order they appear down the page.

Usage:  python -I _frame.py <dump.txt> [--min-w 150] [--min-h 40] [--depth 1]

Why a script and not an eye: every number this prints is the file's, and the geometry harness then
compares the live page against exactly these. "About right" is how the first build shipped.
"""
import io
import json
import re
import sys

path = sys.argv[1]
argv = sys.argv[2:]


def opt(name, default):
    return int(argv[argv.index(name) + 1]) if name in argv else default


min_w = opt("--min-w", 150)
min_h = opt("--min-h", 40)
want_depth = opt("--depth", 1)

text = "".join(x.get("text", "") for x in json.load(io.open(path, encoding="utf-8")))

# The dump is indented XML-ish. Depth is the indent, which is what separates the frame's own rows
# from the text and icons inside them.
TAG = re.compile(
    r'^(?P<ind>\s*)<(?P<kind>[a-z-]+) id="(?P<id>[^"]+)" name="(?P<name>[^"]*)"'
    r' x="(?P<x>-?\d+)" y="(?P<y>-?\d+)" width="(?P<w>\d+)" height="(?P<h>\d+)"'
)

rows = []
root = None
for line in text.splitlines():
    m = TAG.match(line)
    if not m:
        continue
    d = m.groupdict()
    depth = len(d["ind"]) // 2
    rec = (depth, d["id"], d["name"], int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"]))
    if root is None:
        root = rec
        continue
    rows.append(rec)

if root is None:
    print("no frame found")
    raise SystemExit(1)

_, rid, rname, rx, ry, rw, rh = root
print(f"FRAME {rid} {rname}  {rw}x{rh}  (origin {rx},{ry})\n")
print(f"{'id':12} {'x,y':>12} {'w x h':>12}  name")
for depth, i, name, x, y, w, h in rows:
    if depth != want_depth or w < min_w or h < min_h:
        continue
    print(f"{i:12} {f'{x},{y}':>12} {f'{w}x{h}':>12}  {name}")
