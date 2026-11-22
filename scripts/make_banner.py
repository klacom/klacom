#!/usr/bin/env python3
"""Render the animated profile banner and the availability card as GIFs.

The art follows the pixel world on klarenzcom.vercel.app: the same colour
ramps, banded sky, sun, clouds, block textures and creatures. Everything is
painted at native pixel resolution, scaled up with nearest-neighbour, and
encoded with ffmpeg so the palette stays exact.

    python scripts/make_banner.py            # writes assets/banner.gif and assets/availability.gif
    python scripts/make_banner.py --preview DIR  # also writes a PNG of the first frame of each
"""
import argparse
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
FONT = HERE / "fonts" / "PixelifySans-Medium.ttf"

SCALE = 3
W, H = 400, 114
FRAMES, FRAME_MS = 80, 100
TEX = 16


def hexc(h):
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# Colour ramps, shadow to highlight.
RAMP = {
    name: [hexc(c) for c in cols]
    for name, cols in {
        "dirt": ["#4a2c1b", "#65402a", "#82573a", "#9c6e48", "#b5855a"],
        "grass": ["#2a6b26", "#3b8a2e", "#53a83a", "#71c149", "#98d95f"],
        "stone": ["#4b4d57", "#62646e", "#7a7c86", "#93959e", "#adafb6"],
        "sand": ["#bb9c61", "#cfb479", "#ddc58e", "#e9d5a5", "#f4e5bf"],
        "water": ["#1d4a9e", "#2860bd", "#3677d4", "#5291e4", "#8ab6f1"],
        "blossom": ["#93396a", "#bd5487", "#da78a3", "#ee9ec0", "#f9c6da"],
        "bark": ["#2f1f12", "#4a3119", "#634324", "#7e5831", "#987042"],
    }.items()
}
INK = hexc("#1d1a26")
INK_LIGHT = hexc("#eef8ff")

# ------------------------------------------------------------------ texture kit
M32 = 0xFFFFFFFF


def mulberry32(seed):
    a = seed & M32

    def r():
        nonlocal a
        a = (a + 0x6D2B79F5) & M32
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296

    return r


class Paint:
    """A 16x16 tile painter with seeded randomness and tileable value noise."""

    def __init__(self, seed):
        self.r = mulberry32(seed)
        self.px = [None] * (TEX * TEX)
        self.lattices = {}

    def noise(self, period, x, y):
        lat = self.lattices.get(period)
        if lat is None:
            lat = self.lattices[period] = [self.r() for _ in range(period * period)]
        size = TEX / period
        fx, fy = x / size, y / size
        i, j = math.floor(fx), math.floor(fy)
        s = lambda t: t * t * (3 - 2 * t)  # noqa: E731
        u, v = s(fx - i), s(fy - j)
        n = lambda a, b: lat[(b % period) * period + (a % period)]  # noqa: E731
        return (n(i, j) * (1 - u) + n(i + 1, j) * u) * (1 - v) + (n(i, j + 1) * (1 - u) + n(i + 1, j + 1) * u) * v

    def set(self, x, y, c):
        if 0 <= y < TEX:
            self.px[y * TEX + x % TEX] = c

    def image(self):
        img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 0))
        px = img.load()
        for i, c in enumerate(self.px):
            if c is not None:
                px[i % TEX, i // TEX] = (*c, 255)
        return img


def body(t, ramp, y0=0, period=4):
    for y in range(y0, TEX):
        for x in range(TEX):
            n = t.noise(period, x, y)
            v = t.r()
            i = 1 if n < 0.34 else 2 if n < 0.72 else 3
            if v < 0.05:
                i = 0
            elif v < 0.1:
                i = min(4, i + 1)
            t.set(x, y, ramp[i])


def pebbles(t, ramp, count, y0=0):
    for _ in range(count):
        w = 3 if t.r() < 0.45 else 2
        px = math.floor(t.r() * TEX)
        py = y0 + math.floor(t.r() * max(1, 14 - y0))
        for dy in range(2):
            for dx in range(w):
                t.set(px + dx, py + dy, ramp[3])
        t.set(px, py, ramp[4])
        for dx in range(w):
            t.set(px + dx, py + 2, ramp[1])
        for dy in range(2):
            t.set(px + w, py + dy, ramp[1])


def paint_grass(t):
    body(t, RAMP["dirt"], 2)
    pebbles(t, RAMP["dirt"], 5, 4)
    g = RAMP["grass"]
    for x in range(TEX):
        edge = 3 + math.floor(t.noise(4, x, 0) * 2.99)
        if t.r() < 0.16:
            edge += 2
        for y in range(edge):
            if y == 0:
                c = g[4] if t.r() < 0.55 else g[3]
            elif y == 1:
                c = g[4] if t.r() < 0.3 else g[3]
            else:
                c = g[1] if y == edge - 1 else g[2]
            t.set(x, y, c)
        t.set(x, edge, RAMP["dirt"][0])


def paint_dirt(t):
    body(t, RAMP["dirt"])
    pebbles(t, RAMP["dirt"], 6)


def paint_stone(t):
    s = RAMP["stone"]
    for y in range(TEX):
        for x in range(TEX):
            n = t.noise(4, x, y) * 0.7 + t.noise(8, x, y) * 0.3 + (t.r() - 0.5) * 0.1
            t.set(x, y, s[1 if n < 0.3 else 2 if n < 0.68 else 3 if n < 0.88 else 4])
    for _ in range(3):
        x, y = math.floor(t.r() * TEX), 2 + math.floor(t.r() * 12)
        for _ in range(3 + math.floor(t.r() * 3)):
            t.set(x, y, s[0])
            t.set(x, y + 1, s[4])
            x += 1


def paint_sand(t):
    s = RAMP["sand"]
    for y in range(TEX):
        for x in range(TEX):
            n = t.noise(8, x, y)
            v = t.r()
            t.set(x, y, s[1 if v < 0.06 else 4 if v < 0.1 else 2 if n < 0.45 else 3])
    for k in range(4):
        y = 2 + k * 4 + math.floor(t.r() * 2)
        x0 = math.floor(t.r() * TEX)
        for i in range(5):
            t.set(x0 + i, y, s[3])


def paint_leaves(ramp):
    def painter(t):
        for y in range(TEX):
            for x in range(TEX):
                t.set(x, y, ramp[1])
        for gy in range(4):
            for gx in range(4):
                cx = gx * 4 + math.floor(t.r() * 2)
                cy = gy * 4 + math.floor(t.r() * 2)
                for dx, dy, i in ((0, 0, 4), (1, 0, 3), (2, 0, 3), (0, 1, 3), (1, 1, 2), (2, 1, 2), (1, 2, 0)):
                    t.set(cx + dx, cy + dy, ramp[i])
        for _ in range(12):
            x, y = math.floor(t.r() * TEX), math.floor(t.r() * TEX)
            t.set(x, y, None if t.r() < 0.55 else ramp[0])

    return painter


def paint_log(t):
    b = RAMP["bark"]
    for y in range(TEX):
        for x in range(TEX):
            c = x % 4
            broken = t.noise(8, x, y) > 0.72
            i = 2
            if c == 0 and not broken:
                i = 0 if t.r() < 0.5 else 1
            elif c == 2 and not broken:
                i = 4 if t.r() < 0.2 else 3
            elif t.r() < 0.08:
                i = 1
            t.set(x, y, b[i])


def tile(seed, painter):
    t = Paint(seed)
    painter(t)
    return t.image()


TILES = {
    "grass": tile(211, paint_grass),
    "dirt": tile(8130, paint_dirt),
    "stone": tile(16049, paint_stone),
    "sand": tile(23968, paint_sand),
    "blossom": tile(31887, paint_leaves(RAMP["blossom"])),
    "log": tile(39806, paint_log),
}

_WATER = Paint(47725)


def water_tile(frame):
    """Water slides two tileable noise layers past each other; one full cycle per loop."""
    k = (frame * TEX // FRAMES) % TEX
    img = Image.new("RGBA", (TEX, TEX))
    px = img.load()
    w = RAMP["water"]
    for y in range(TEX):
        for x in range(TEX):
            n = _WATER.noise(4, x - k, y) * 0.75 + _WATER.noise(8, x + k, y) * 0.25
            i = 4 if y == 0 else 1 if n < 0.4 else 2 if n < 0.72 else 3
            px[x, y] = (*w[i], 255)
    return img


# ------------------------------------------------------------------ sprites
def sprite(w, h, draw):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()

    def p(x, y, c):
        if 0 <= x < w and 0 <= y < h:
            px[x, y] = (*(hexc(c) if isinstance(c, str) else c), 255)

    def r(x, y, ww, hh, c):
        for yy in range(y, y + hh):
            for xx in range(x, x + ww):
                p(xx, yy, c)

    draw(r, p)
    return img


def flower(petal, centre):
    g = RAMP["grass"]
    pc = hexc(petal)
    dark = tuple(round(v * 0.72) for v in pc)

    def draw(r, p):
        for i in range(6):
            p(7, 15 - i, g[1] if i % 2 else g[2])
        p(6, 13, g[3])
        p(8, 12, g[3])
        for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            p(7 + dx, 8 + dy, pc)
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            p(7 + dx, 8 + dy, dark)
        p(7, 8, centre)

    return sprite(16, 16, draw)


def tallgrass(seed):
    """Two poses of one tuft: as grown, and with the tips bent by the wind."""
    rnd = mulberry32(seed)
    blades = [(1 + math.floor(rnd() * 14), 3 + math.floor(rnd() * 5), -1 if rnd() < 0.5 else 1) for _ in range(7)]
    g = RAMP["grass"]

    def pose(wind):
        def draw(r, p):
            for x, h, lean in blades:
                tip = 1 if wind else lean
                for i in range(h):
                    c = g[4] if i == h - 1 else g[2] if i % 2 else g[3]
                    p(x + (tip if i > h - 3 else 0), 15 - i, c)

        return sprite(16, 16, draw)

    return pose(False), pose(True)


def sheep(pose):
    Wo, w, s, F, f, L, l, E = "#efe9dc", "#d6cdb9", "#b5aa93", "#5b4a43", "#7d685e", "#4f4038", "#3a2e28", "#18121a"
    hd = 3 if pose == "eat" else 0

    def draw(r, p):
        r(2, 9, 2, 3, l)
        r(8, 9, 2, 3, l)
        r(4, 9, 2, 4, L)
        r(10, 9, 2, 4, L)
        r(1, 2, 12, 6, Wo)
        r(2, 1, 10, 1, Wo)
        r(0, 3, 1, 4, Wo)
        r(1, 8, 12, 1, s)
        r(1, 7, 12, 1, w)
        for x, y in ((3, 3), (6, 2), (9, 4), (4, 5), (8, 6), (11, 3), (2, 6)):
            p(x, y, w)
        r(12, 3 + hd, 4, 5, F)
        r(12, 2 + hd, 3, 1, Wo)
        p(11, 3 + hd, Wo)
        p(11, 5 + hd, f)
        r(12, 6 + hd, 2, 1, f)
        if pose == "blink":
            r(13, 4 + hd, 2, 1, "#2a201c")
        else:
            p(13, 4 + hd, "#ffffff")
            p(14, 4 + hd, E)
        p(15, 6 + hd, "#d99c9c")

    return sprite(16, 13, draw)


def pig(pose):
    P, pp, d, S, n, E, hi = "#f2a8a3", "#dd8c89", "#c0737a", "#f7c4bf", "#8c3d45", "#1a1218", "#f9c8c3"
    step = int(pose[4]) if pose.startswith("walk") else -1
    back = -1 if step == 0 else 1 if step == 2 else 0

    def draw(r, p):
        r(3 - back, 8, 2, 3, d)
        r(9 + back, 8, 2, 3, d)
        r(4 + back, 8, 2, 4, pp)
        r(10 - back, 8, 2, 4, pp)
        r(1, 3, 12, 5, P)
        r(2, 2, 10, 1, P)
        r(2, 2, 8, 1, hi)
        r(1, 7, 12, 1, pp)
        r(11, 1, 4, 6, P)
        r(11, 0, 2, 1, d)
        p(12, 1, d)
        r(14, 3, 2, 3, S)
        p(15, 4, n)
        p(0, 3, d)
        p(0, 2, d)
        p(1, 1, d)
        p(13, 2, d if pose == "blink" else E)

    return sprite(16, 12, draw)


def me(blink=False):
    """Klarenz: glasses, a blue crew-neck sweatshirt with long sleeves, and dark beige pants."""
    shirt, shirt_hi, shirt_lo, rib = "#629bc4", "#78afd3", "#5589b0", "#4f7fa6"
    pants, pants_hi, pants_lo, pants_gap = "#9a8466", "#ab9575", "#7f6b51", "#6b5943"
    shoes = "#2b2320"

    def draw(r, p):
        # Sweatshirt: body, lit from the left, with a ribbed collar and waistband.
        r(1, 8, 6, 5, shirt)
        r(1, 8, 2, 4, shirt_hi)
        r(3, 8, 2, 1, rib)
        r(1, 12, 6, 1, shirt_lo)
        # Long sleeves down to the wrists, then hands.
        r(0, 8, 1, 4, shirt)
        r(7, 8, 1, 4, shirt_lo)
        p(0, 12, "#d5a07c")
        p(7, 12, "#c58d68")
        # Pants, with a shadow between the legs.
        r(1, 13, 6, 3, pants)
        r(1, 13, 1, 3, pants_hi)
        r(6, 13, 1, 3, pants_lo)
        r(3, 15, 2, 1, pants_gap)
        r(1, 16, 2, 1, shoes)
        r(5, 16, 2, 1, shoes)
        # Head.
        r(0, 0, 8, 8, "#d5a07c")
        r(1, 2, 6, 2, "#dda982")
        p(2, 2, "#e1af87")
        p(3, 2, "#e5b68e")
        r(6, 2, 1, 2, "#d39b76")
        r(0, 3, 1, 4, "#c58d68")
        r(7, 3, 1, 4, "#bb805f")
        p(1, 6, "#ce956f")
        p(6, 6, "#ce956f")
        r(0, 7, 8, 1, "#c68b64")
        r(2, 7, 4, 1, "#d19a74")
        r(3, 7, 2, 1, "#dda982")
        # Hair.
        r(0, 0, 8, 2, "#242522")
        r(0, 0, 1, 3, "#1d1e1c")
        r(7, 0, 1, 3, "#1d1e1c")
        for x, y, ww in ((1, 0, 2), (4, 0, 2), (2, 1, 2)):
            r(x, y, ww, 1, "#30312c")
        # Glasses.
        for x, y, ww in ((1, 3, 2), (5, 3, 2), (0, 4, 1), (3, 4, 2), (7, 4, 1)):
            r(x, y, ww, 1, "#514c40")
        r(1, 5, 2, 1, "#6b6250")
        r(5, 5, 2, 1, "#6b6250")
        p(1, 4, "#c5dfdd")
        p(6, 4, "#c5dfdd")
        eye = "#514c40" if blink else "#302b23"
        p(2, 4, eye)
        p(5, 4, eye)
        r(3, 5, 2, 1, "#bf825f")
        r(3, 6, 2, 1, "#a66b58")

    img = sprite(8, 17, draw)
    return img.resize((16, 34), Image.NEAREST)


# ------------------------------------------------------------------ scene
GROUND_Y = H - 2 * TEX  # top of the low ground
LEVEL = [2] * 25
for c in range(17, 25):
    LEVEL[c] = 3
POOL = range(9, 12)
POOL_EDGE = (8, 12)
TREE_COL = 23


def surface(col):
    return H - TEX * LEVEL[col]


def row_y(row):
    return H - TEX * (row + 1)


BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]


def sky_layer():
    """Banded daytime sky, dithered only along the seams, with two rows of far hills and the sun."""
    top, mid, low = hexc("#3f97e0"), hexc("#7cc0ef"), hexc("#d4eef9")
    bands = 9
    cols = [mix(top, mid, v / 0.55) if v < 0.55 else mix(mid, low, (v - 0.55) / 0.45) for v in (i / bands for i in range(bands + 1))]
    hw, hh = W // 2, GROUND_Y // 2 + 1
    small = Image.new("RGB", (hw, hh))
    px = small.load()
    for y in range(hh):
        fb = min(1, y / (hh - 1)) * bands
        bi = math.floor(fb)
        frac = max(0, min(1, (fb - bi - 0.62) * 2.6))
        for x in range(hw):
            th = (BAYER[(y % 4) * 4 + x % 4] + 0.5) / 16
            px[x, y] = cols[min(bands, bi + (1 if frac > th else 0))]
    d = ImageDraw.Draw(small)
    for amp, freq, seed, col in ((6, 0.035, 3, mix(low, (120, 150, 170), 0.45)), (4, 0.06, 9, mix(low, (80, 128, 104), 0.62))):
        for i in range(hw // 2 + 2):
            v = math.sin(i * freq + seed) * 0.5 + math.sin(i * freq * 2.3 + seed * 2) * 0.3 + math.sin(i * freq * 0.4) * 0.2
            hgt = max(1, round((v * 0.5 + 0.55) * amp))
            d.rectangle((i * 2, hh - hgt * 2, i * 2 + 1, hh - 1), fill=col)
    sky = Image.new("RGB", (W, H), low)
    sky.paste(small.resize((hw * 2, hh * 2), Image.NEAREST), (0, 0))

    # The sun: a solid square inside two translucent halos.
    sx, sy = 298, 20
    spx = sky.load()
    for half, col, a in ((15, (255, 246, 200), 0.2), (12, (255, 236, 150), 0.4), (9, (255, 250, 214), 1.0)):
        for y in range(sy - half, sy + half):
            for x in range(sx - half, sx + half):
                spx[x, y] = mix(spx[x, y], col, a)
    return sky


CLOUDS = [  # x, y, width, drift amplitude, phase
    (150, 7, 45, 5, 0.0),
    (232, 62, 42, 4, 0.35),
    (-6, 4, 33, 3, 0.7),
]


def draw_clouds(img, f):
    d = ImageDraw.Draw(img)
    under, lit = (214, 228, 244), (255, 255, 255)
    for x0, y, w, amp, phase in CLOUDS:
        x = x0 + round(amp * math.sin(2 * math.pi * (f / FRAMES + phase)))
        d.rectangle((x, y + 3, x + w - 1, y + 7), fill=under)
        d.rectangle((x + 3, y, x + w - 4, y + 4), fill=lit)
        d.rectangle((x + 9, y - 3, x + w - 13, y - 1), fill=lit)


def terrain_layer():
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for c in range(25):
        for r in range(LEVEL[c]):
            if c in POOL:
                name = "sand" if r == 0 else None
            elif c in POOL_EDGE and r == LEVEL[c] - 1:
                name = "sand"
            elif r == LEVEL[c] - 1:
                name = "grass"
            else:
                name = "dirt"
            if name:
                layer.alpha_composite(TILES[name], (c * TEX, row_y(r)))
    # Cherry blossom tree on the plateau.
    base = LEVEL[TREE_COL]
    for r in (base, base + 1):
        layer.alpha_composite(TILES["log"], (TREE_COL * TEX, row_y(r)))
    for r, cols in ((base + 1, (21, 22, 24)), (base + 2, (21, 22, 23, 24)), (base + 3, (22, 23, 24))):
        for c in cols:
            layer.alpha_composite(TILES["blossom"], (c * TEX, row_y(r)))
    # Flowers that stay still.
    for col, petal, centre in ((3, "#e8524d", "#ffe08a"), (7, "#f5c542", "#c9862e"), (15, "#f08ac0", "#fff4e0"), (21, "#6c8fe0", "#fff1b0")):
        layer.alpha_composite(flower(petal, centre), (col * TEX + 2, surface(col) - TEX))
    return layer


TUFTS = [(1, 11, 0), (5, 23, 3), (13, 31, 6), (16, 47, 1), (20, 59, 4)]  # column, seed, phase
TUFT_POSES = {seed: tallgrass(seed) for _, seed, _ in TUFTS}
PETALS = [(340, 0), (352, 20), (331, 40), (362, 60)]  # start x, start frame


def text_layer():
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.fontmode = "1"
    big, small = ImageFont.truetype(str(FONT), 20), ImageFont.truetype(str(FONT), 10)
    lines = [((14, 12), "Klarenz Cobie Manrique", big, 2), ((15, 37), "Full-stack developer · web, mobile & games", small, 1)]
    for (x, y), text, font, off in lines:
        d.text((x + off, y + off), text, font=font, fill=(*INK_LIGHT, 255))
        d.text((x, y), text, font=font, fill=(*INK, 255))
    return layer


def banner_frame(f, sky, terrain, text):
    img = sky.copy()
    draw_clouds(img, f)
    img = img.convert("RGBA")
    img.alpha_composite(terrain)
    water = water_tile(f)
    for c in POOL:
        img.alpha_composite(water, (c * TEX, row_y(1)))
    # Grass tufts lean with the wind for a moment, each at its own time.
    for col, seed, phase in TUFTS:
        windy = ((f // 10) + phase) % 8 < 2
        img.alpha_composite(TUFT_POSES[seed][1 if windy else 0], (col * TEX, surface(col) - TEX))
    # Sheep grazing on the left.
    pose = "blink" if f in (48, 49) else "eat" if (f % 40) < 24 else "open"
    img.alpha_composite(sheep(pose), (30, GROUND_Y - 13))
    # Pig wandering on the plateau: walks right, rests, walks back, rests.
    plateau = surface(17)
    if f < 30:
        x, pose, flip = 274 + f // 2, f"walk{(f // 2) % 4}", False
    elif f < 40:
        x, pose, flip = 289, "blink" if f in (34, 35) else "stand", False
    elif f < 70:
        x, pose, flip = 289 - (f - 40) // 2, f"walk{((f - 40) // 2) % 4}", True
    else:
        x, pose, flip = 274, "stand", True
    p = pig(pose)
    img.alpha_composite(p.transpose(Image.FLIP_LEFT_RIGHT) if flip else p, (x, plateau - 12))
    # Klarenz, blinking once a loop.
    img.alpha_composite(me(blink=f in (60, 61)), (312, plateau - 34))
    # Blossom petals drifting down to the plateau.
    pd = ImageDraw.Draw(img)
    for x0, start in PETALS:
        k = (f - start) % FRAMES
        if k < 30:
            x = x0 - k // 3 + round(1.5 * math.sin(k / 4))
            y = surface(21) - 17 + round(k * 0.55)
            pd.point((x, y), fill=RAMP["blossom"][4])
            pd.point((x + 1, y), fill=RAMP["blossom"][3])
        elif k < 42:
            x = x0 - 10
            pd.point((x, plateau - 1), fill=RAMP["blossom"][3])
            pd.point((x + 1, plateau - 1), fill=RAMP["blossom"][2])
    img.alpha_composite(text)
    return img.convert("RGB")


# ------------------------------------------------------------------ availability card
CARD_W, CARD_H = 400, 58
GREEN = [hexc(c) for c in ("#1f6a2c", "#3fae4a", "#8ee07a", "#cdf5c0")]


def card_frame(f):
    img = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    # A panel like the portfolio's buttons: light face, dark outline, hard shadow.
    d.rectangle((2, 2, CARD_W - 1, CARD_H - 1), fill=(43, 40, 56, 255))
    d.rectangle((0, 0, CARD_W - 3, CARD_H - 3), fill=(*INK, 255))
    d.rectangle((1, 1, CARD_W - 4, CARD_H - 4), fill=(246, 243, 234, 255))
    d.rectangle((1, 1, CARD_W - 4, 1), fill=(255, 255, 255, 255))
    # Status light: an 8-pixel circle with a halo that pulses.
    cx, cy = 14, 17

    def disc(radius_rows, col):
        for dy, half in radius_rows:
            d.line((cx - half, cy + dy, cx + half - 1, cy + dy), fill=(*col, 255))

    if f == 0:
        disc([(-6, 2), (-5, 4), (-4, 5), (-3, 6), (-2, 6), (-1, 6), (0, 6), (1, 6), (2, 5), (3, 4), (4, 2)], GREEN[3])
    disc([(-4, 2), (-3, 3), (-2, 4), (-1, 4), (0, 4), (1, 4), (2, 3), (3, 2)], GREEN[0])
    disc([(-3, 2), (-2, 3), (-1, 3), (0, 3), (1, 3), (2, 2)], GREEN[1])
    d.rectangle((cx - 2, cy - 2, cx - 1, cy - 1), fill=(*GREEN[2], 255))
    big, small = ImageFont.truetype(str(FONT), 20), ImageFont.truetype(str(FONT), 10)
    d.text((26, 6), "Open to OJT internships", font=big, fill=(*INK, 255))
    d.text((26, 31), "Dec 1, 2026 – Feb 13, 2027 · can start earlier", font=small, fill=(*INK, 255))
    d.text((26, 42), "Onsite or hybrid in Metro Manila, or remote (UTC+8)", font=small, fill=(86, 80, 104, 255))
    # Hours on the right.
    hours = "486"
    hw = d.textlength(hours, font=big)
    d.text((CARD_W - 16 - hw, 11), hours, font=big, fill=(*INK, 255))
    lw = d.textlength("OJT hours", font=small)
    d.text((CARD_W - 16 - lw, 36), "OJT hours", font=small, fill=(86, 80, 104, 255))
    return img


# ------------------------------------------------------------------ output
def encode(frames, out, frame_ms):
    """Write frames to a looping GIF through ffmpeg's palette pass, with no dithering."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required: https://ffmpeg.org/download.html")
    colours = set()
    for fr in frames:
        colours.update(c for _, c in fr.convert("RGBA").getcolors(1 << 16))
    print(f"{out.name}: {len(frames)} frames, {len(colours)} colours")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for i, fr in enumerate(frames):
            fr.resize((fr.width * SCALE, fr.height * SCALE), Image.NEAREST).save(tmp / f"f{i:03d}.png")
        rate = f"1000/{frame_ms}"
        common = [ffmpeg, "-v", "error", "-y", "-framerate", rate, "-i", str(tmp / "f%03d.png")]
        subprocess.run([*common, "-vf", "palettegen=max_colors=256:stats_mode=full", str(tmp / "palette.png")], check=True)
        subprocess.run([*common, "-i", str(tmp / "palette.png"), "-lavfi", "paletteuse=dither=none:diff_mode=rectangle", "-loop", "0", str(out)], check=True)
    print(f"  {out.stat().st_size / 1024:.0f} KB -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", metavar="DIR", type=Path, help="also save the first frame of each as PNG in DIR")
    args = ap.parse_args()

    sky, terrain, text = sky_layer(), terrain_layer(), text_layer()
    banner = [banner_frame(f, sky, terrain, text) for f in range(FRAMES)]
    card = [card_frame(f) for f in range(2)]
    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        banner[0].resize((W * SCALE, H * SCALE), Image.NEAREST).save(args.preview / "banner-preview.png")
        card[0].resize((CARD_W * SCALE, CARD_H * SCALE), Image.NEAREST).save(args.preview / "availability-preview.png")
    encode(banner, ASSETS / "banner.gif", FRAME_MS)
    encode(card, ASSETS / "availability.gif", 700)


if __name__ == "__main__":
    main()
