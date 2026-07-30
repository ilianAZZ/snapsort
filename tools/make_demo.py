"""Generate a fake Snapchat export, used for the screenshots in the README.

The real thing holds personal photos, which have no place in a public
repository. This builds a folder that looks like an extracted export — same
file names, same timestamps, same `memories_history.json` — with silly drawn
stickers instead of memories.

    python3 tools/make_demo.py /tmp/demo-export

Standard library only, like the rest of the project: the PNGs are encoded by
hand with zlib, and the shapes are painted a bounding box at a time.
"""

from __future__ import annotations

import json
import math
import os
import random
import struct
import sys
import zlib
from datetime import datetime, timedelta, timezone

WIDTH, HEIGHT = 540, 960


# ==========================================================================
# Painting
# ==========================================================================

def canvas(top, bottom) -> list[bytearray]:
    """A vertical gradient to paint on."""
    rows = []
    for y in range(HEIGHT):
        colour = bytes(mix(top, bottom, y / HEIGHT))
        rows.append(bytearray(colour * WIDTH))
    return rows


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def ellipse(rows, cx, cy, rx, ry, colour):
    colour = bytes(colour)
    for y in range(max(0, int(cy - ry)), min(HEIGHT, int(cy + ry) + 1)):
        dy = (y - cy) / ry
        if abs(dy) > 1:
            continue
        half = rx * math.sqrt(1 - dy * dy)
        x0, x1 = max(0, int(cx - half)), min(WIDTH, int(cx + half) + 1)
        if x1 > x0:
            rows[y][3 * x0:3 * x1] = colour * (x1 - x0)


def circle(rows, cx, cy, r, colour):
    ellipse(rows, cx, cy, r, r, colour)


def box(rows, x0, y0, x1, y1, colour):
    colour = bytes(colour)
    x0, x1 = max(0, int(x0)), min(WIDTH, int(x1))
    for y in range(max(0, int(y0)), min(HEIGHT, int(y1))):
        if x1 > x0:
            rows[y][3 * x0:3 * x1] = colour * (x1 - x0)


def polygon(rows, points, colour):
    colour = bytes(colour)
    ys = [p[1] for p in points]
    for y in range(max(0, int(min(ys))), min(HEIGHT, int(max(ys)) + 1)):
        crossings = []
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
            if (y1 <= y < y2) or (y2 <= y < y1):
                crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        crossings.sort()
        for a, b in zip(crossings[::2], crossings[1::2]):
            a, b = max(0, int(a)), min(WIDTH, int(b) + 1)
            if b > a:
                rows[y][3 * a:3 * b] = colour * (b - a)


def png(rows: list[bytearray], alpha: bool = False) -> bytes:
    """Encode rows into a PNG — RGB, or RGBA when `alpha` is set."""
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


# ==========================================================================
# A 3×5 pixel font, so the overlays can actually say something
# ==========================================================================

FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011",
    "D": "110101101101110", "E": "111100110100111", "F": "111100110100100",
    "G": "011100101101011", "H": "101101111101101", "I": "111010010010111",
    "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "101111111111101", "O": "010101101101010",
    "P": "110101110100100", "Q": "010101101111011", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101011",
    "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", "0": "111101101101111",
    "1": "010110010010111", "2": "110001010100111", "3": "110001010001110",
    "4": "101101111001001", "5": "111100110001110", "6": "011100110101010",
    "7": "111001010010010", "8": "010101010101010", "9": "010101011001110",
    "!": "010010010000010", "?": "110001010000010", ".": "000000000000010",
    "/": "001001010100100", " ": "000000000000000",
}


def text(rows, message: str, cx, cy, scale, colour):
    """Draw `message` centred on (cx, cy) with the 3×5 font."""
    width = (len(message) * 4 - 1) * scale
    x = cx - width / 2
    for char in message.upper():
        glyph = FONT.get(char, FONT[" "])
        for row in range(5):
            for col in range(3):
                if glyph[row * 3 + col] == "1":
                    box(rows, x + col * scale, cy - 2.5 * scale + row * scale,
                        x + (col + 1) * scale, cy - 2.5 * scale + (row + 1) * scale, colour)
        x += 4 * scale


# ==========================================================================
# The stickers
# ==========================================================================

BLACK, WHITE = (24, 22, 28), (252, 252, 250)


def cat(rows, cx, cy, s, fur):
    for side in (-1, 1):
        polygon(rows, [(cx + side * 0.55 * s, cy - 0.55 * s),
                       (cx + side * 0.95 * s, cy - 1.35 * s),
                       (cx + side * 1.0 * s, cy - 0.35 * s)], fur)
    circle(rows, cx, cy, s, fur)
    for side in (-1, 1):
        circle(rows, cx + side * 0.36 * s, cy - 0.12 * s, 0.14 * s, BLACK)
        box(rows, cx + side * 0.62 * s - 0.4 * s * (side > 0), cy + 0.22 * s,
            cx + side * 1.25 * s - 0.4 * s * (side > 0), cy + 0.26 * s, BLACK)
    polygon(rows, [(cx - 0.12 * s, cy + 0.2 * s), (cx + 0.12 * s, cy + 0.2 * s),
                   (cx, cy + 0.38 * s)], (232, 120, 150))


def shades(rows, cx, cy, s, skin):
    circle(rows, cx, cy, s, skin)
    box(rows, cx - 0.95 * s, cy - 0.3 * s, cx + 0.95 * s, cy - 0.16 * s, BLACK)
    for side in (-1, 1):
        ellipse(rows, cx + side * 0.44 * s, cy - 0.1 * s, 0.36 * s, 0.26 * s, BLACK)
    ellipse(rows, cx, cy + 0.42 * s, 0.5 * s, 0.34 * s, BLACK)
    box(rows, cx - 0.55 * s, cy + 0.08 * s, cx + 0.55 * s, cy + 0.42 * s, skin)


def ghost(rows, cx, cy, s):
    circle(rows, cx, cy - 0.1 * s, s, WHITE)
    box(rows, cx - s, cy - 0.1 * s, cx + s, cy + 0.8 * s, WHITE)
    for i in range(4):
        circle(rows, cx - 0.75 * s + i * 0.5 * s, cy + 0.8 * s, 0.25 * s, WHITE)
    for side in (-1, 1):
        ellipse(rows, cx + side * 0.34 * s, cy - 0.2 * s, 0.13 * s, 0.19 * s, BLACK)
    ellipse(rows, cx, cy + 0.22 * s, 0.16 * s, 0.22 * s, BLACK)


def pizza(rows, cx, cy, s):
    polygon(rows, [(cx, cy - s), (cx - 0.72 * s, cy + 0.8 * s),
                   (cx + 0.72 * s, cy + 0.8 * s)], (247, 200, 92))
    polygon(rows, [(cx - 0.72 * s, cy + 0.8 * s), (cx + 0.72 * s, cy + 0.8 * s),
                   (cx + 0.82 * s, cy + 1.0 * s), (cx - 0.82 * s, cy + 1.0 * s)],
            (206, 142, 66))
    for dx, dy in ((-0.2, 0.1), (0.24, 0.3), (0.0, 0.55), (-0.3, 0.6), (0.05, -0.25)):
        circle(rows, cx + dx * s, cy + dy * s, 0.11 * s, (206, 62, 62))


def rocket(rows, cx, cy, s):
    polygon(rows, [(cx, cy - 1.1 * s), (cx - 0.38 * s, cy + 0.1 * s),
                   (cx + 0.38 * s, cy + 0.1 * s)], WHITE)
    box(rows, cx - 0.38 * s, cy + 0.1 * s, cx + 0.38 * s, cy + 0.62 * s, WHITE)
    for side in (-1, 1):
        polygon(rows, [(cx + side * 0.38 * s, cy + 0.16 * s),
                       (cx + side * 0.86 * s, cy + 0.72 * s),
                       (cx + side * 0.38 * s, cy + 0.62 * s)], (226, 76, 84))
    circle(rows, cx, cy - 0.24 * s, 0.2 * s, (96, 176, 232))
    polygon(rows, [(cx - 0.24 * s, cy + 0.62 * s), (cx + 0.24 * s, cy + 0.62 * s),
                   (cx, cy + 1.25 * s)], (252, 186, 58))


def dog(rows, cx, cy, s, fur):
    for side in (-1, 1):
        ellipse(rows, cx + side * 0.92 * s, cy + 0.12 * s, 0.28 * s, 0.55 * s,
                mix(fur, BLACK, 0.25))
    circle(rows, cx, cy, s, fur)
    for side in (-1, 1):
        circle(rows, cx + side * 0.34 * s, cy - 0.14 * s, 0.13 * s, BLACK)
    ellipse(rows, cx, cy + 0.34 * s, 0.5 * s, 0.36 * s, mix(fur, WHITE, 0.5))
    ellipse(rows, cx, cy + 0.18 * s, 0.17 * s, 0.13 * s, BLACK)
    ellipse(rows, cx, cy + 0.6 * s, 0.14 * s, 0.2 * s, (226, 110, 130))


SCENES = [
    (lambda r, cx, cy, s: shades(r, cx, cy, s, (252, 208, 72)),
     ((28, 22, 62), (86, 60, 168)), "10/10"),
    (lambda r, cx, cy, s: cat(r, cx, cy, s, (244, 168, 96)),
     ((14, 46, 52), (34, 148, 140)), "SEND HELP"),
    (lambda r, cx, cy, s: pizza(r, cx, cy, s),
     ((48, 16, 34), (188, 62, 96)), "NO REGRETS"),
    (lambda r, cx, cy, s: ghost(r, cx, cy, s),
     ((16, 20, 42), (58, 78, 176)), "BOO"),
    (lambda r, cx, cy, s: rocket(r, cx, cy, s),
     ((10, 12, 26), (48, 40, 96)), "TO THE MOON"),
    (lambda r, cx, cy, s: dog(r, cx, cy, s, (226, 176, 118)),
     ((40, 30, 12), (200, 148, 54)), "WHO DID THIS"),
]


def scene(index: int) -> bytes:
    draw, (top, bottom), _caption = SCENES[index % len(SCENES)]
    rows = canvas(top, bottom)
    rng = random.Random(index)
    for _ in range(28):                                  # a few sparkles
        circle(rows, rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT),
               rng.uniform(1.5, 4), mix(bottom, WHITE, 0.45))
    draw(rows, WIDTH / 2, HEIGHT * 0.44, WIDTH * 0.26)
    return png(rows)


def overlay(index: int) -> bytes:
    """A Snapchat-style layer: a caption band and a scribble, over transparency.

    The alpha channel matters: the app paints this on top of the media, so
    everything outside the drawing has to be fully transparent.
    """
    caption = SCENES[index % len(SCENES)][2]
    rng = random.Random(index * 31 + 7)
    rows = [bytearray(WIDTH * 4) for _ in range(HEIGHT)]

    def paint(x0, y0, x1, y1, rgba):
        rgba = bytes(rgba)
        x0, x1 = max(0, int(x0)), min(WIDTH, int(x1))
        for y in range(max(0, int(y0)), min(HEIGHT, int(y1))):
            if x1 > x0:
                rows[y][4 * x0:4 * x1] = rgba * (x1 - x0)

    band_y = HEIGHT * 0.66
    paint(0, band_y - 34, WIDTH, band_y + 34, (12, 12, 14, 205))

    # The font helper writes RGB; run it on a scratch mask, then stencil it in.
    mask = [bytearray(WIDTH * 3) for _ in range(HEIGHT)]
    text(mask, caption, WIDTH / 2, band_y, max(3, int(46 / max(len(caption), 1)) + 2),
         (255, 255, 255))
    for y in range(HEIGHT):
        row, source = rows[y], mask[y]
        for x in range(WIDTH):
            if source[3 * x]:
                row[4 * x:4 * x + 4] = b"\xfa\xfa\xf8\xff"

    for _ in range(3):                                   # a yellow doodle
        cx, cy = rng.uniform(0.2, 0.8) * WIDTH, rng.uniform(0.82, 0.92) * HEIGHT
        for k in range(14):
            paint(cx + k * 9 - 60, cy + math.sin(k / 2) * 14,
                  cx + k * 9 - 50, cy + math.sin(k / 2) * 14 + 10, (255, 252, 0, 255))
    return png(rows, alpha=True)


# ==========================================================================
# The export
# ==========================================================================

def main(target: str) -> int:
    memories = os.path.join(target, "memories")
    os.makedirs(memories, exist_ok=True)

    start = datetime(2019, 7, 14, 18, 32, 7, tzinfo=timezone.utc)
    records = []
    rng = random.Random(20190714)

    for i in range(24):
        when = start + timedelta(days=i * 11, minutes=rng.randint(-300, 300))
        uid = "-".join("%04x%04x" % (rng.randrange(1 << 16), rng.randrange(1 << 16))
                       for _ in range(4))
        stem = f"{when.strftime('%Y-%m-%d')}_{uid}"
        parts = [("main", scene(i))]
        if i % 3 == 0:
            parts.append(("overlay", overlay(i)))
        for part, blob in parts:
            path = os.path.join(memories, f"{stem}-{part}.png")
            with open(path, "wb") as fh:
                fh.write(blob)
            # `_scan_dir` reads the mtime as if it were UTC, the way `unzip`
            # leaves it. So we need the instant whose *local* wall clock reads
            # like `when` does in UTC — and the offset moves with daylight
            # saving, so it has to be worked out date by date.
            stamp = when.replace(tzinfo=None).astimezone().timestamp()
            os.utime(path, (stamp, stamp))

        record = {"Date": when.strftime("%Y-%m-%d %H:%M:%S UTC"), "Media Type": "Image"}
        if i % 4 == 0:
            record["Location"] = (f"Latitude, Longitude: "
                                  f"{rng.uniform(43, 51):.4f}, {rng.uniform(-1, 7):.4f}")
        records.append(record)

    json_dir = os.path.join(target, "json")
    os.makedirs(json_dir, exist_ok=True)
    with open(os.path.join(json_dir, "memories_history.json"), "w", encoding="utf-8") as fh:
        json.dump({"Saved Media": records}, fh, indent=1)

    print(f"Demo export written to {target} ({len(records)} memories)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sorter-demo"))
