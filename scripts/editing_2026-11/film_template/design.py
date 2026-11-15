"""HUD-style slate design (game-UI look): camera slates for every planned shot,
a lower-third nameplate, and a broadcast program slate. Drawn at 2x and
downsampled so every edge is clean. Pillow only."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
S = 2  # supersample
DIN = "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf"
DIN_ALT = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"
BG = (7, 9, 13)
PANEL = (14, 19, 26)
INK = (236, 242, 248)
DIM = (120, 134, 150)
FAINT = (52, 62, 74)

# Neon accents per shot type (marker colours stay Resolve's Blue/Green/Yellow/Purple)
ACCENT = {"interview": (46, 168, 255), "instruction": (46, 168, 255), "broll": (57, 255, 156),
          "transition": (255, 210, 63), "title": (180, 107, 255), "program": (46, 230, 255)}


def _f(path, size, mono_bold=False):
    f = ImageFont.truetype(path, int(size * S))
    if mono_bold:
        try:
            f.set_variation_by_name("Bold")
        except Exception:  # noqa: BLE001
            pass
    return f


def din(size):
    return _f(DIN, size)


def mono(size, bold=False):
    return _f(MONO, size, bold)


def _xy(*v):
    return [int(x * S) for x in v]


def spaced(d, xy, text, font, fill, tracking=0.0, anchor="la"):
    """Letter-spaced text (HUD labels)."""
    if not tracking:
        d.text(_xy(*xy), text, font=font, fill=fill, anchor=anchor)
        return
    widths = [d.textlength(c, font=font) for c in text]
    total = sum(widths) + tracking * S * (len(text) - 1)
    x = xy[0] * S - (total if anchor[0] == "r" else total / 2 if anchor[0] == "m" else 0)
    for c, w in zip(text, widths):
        d.text((x, xy[1] * S), c, font=font, fill=fill, anchor="l" + anchor[1])
        x += w + tracking * S


def background(accent, transparent=False):
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0) if transparent else BG + (255,))
    if transparent:
        return img
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse(_xy(-500, -600, 1100, 700), fill=accent + (70,))
    g.ellipse(_xy(1300, 600, 2500, 1600), fill=accent + (28,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(220 * S)))
    d = ImageDraw.Draw(img)
    for x in range(0, W, 60):
        d.line(_xy(x, 0, x, H), fill=(255, 255, 255, 9), width=1)
    for y in range(0, H, 60):
        d.line(_xy(0, y, W, y), fill=(255, 255, 255, 9), width=1)
    for y in range(0, H, 4):
        d.line(_xy(0, y, W, y), fill=(0, 0, 0, 40), width=S)
    # vignette
    vig = Image.new("L", img.size, 0)
    ImageDraw.Draw(vig).rectangle(_xy(140, 120, W - 140, H - 120), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(180 * S))
    dark = Image.new("RGBA", img.size, (0, 0, 0, 170))
    dark.putalpha(Image.eval(vig, lambda v: int(170 * (1 - v / 255))))
    img.alpha_composite(dark)
    return img


def glow_text(img, xy, text, font, color, anchor="la", radius=14, strength=2):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(_xy(*xy), text, font=font, fill=color + (255,), anchor=anchor)
    blurred = layer.filter(ImageFilter.GaussianBlur(radius * S))
    for _ in range(strength):
        img.alpha_composite(blurred)
    img.alpha_composite(layer)


def brackets(d, box, color, size=26, width=4):
    x0, y0, x1, y1 = box
    for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line(_xy(x, y, x + dx * size, y), fill=color, width=width * S)
        d.line(_xy(x, y, x, y + dy * size), fill=color, width=width * S)


def panel(img, box, accent, cut=34, alpha=225):
    """Dark HUD panel with a chamfered top-right corner, accent edge and corner brackets."""
    x0, y0, x1, y1 = box
    pts = _xy(x0, y0, x1 - cut, y0, x1, y0 + cut, x1, y1, x0, y1)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.polygon(list(zip(pts[::2], pts[1::2])), fill=PANEL + (alpha,), outline=accent + (90,), width=2 * S)
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    brackets(d, (x0 - 6, y0 - 6, x1 + 6, y1 + 6), accent + (255,), 22, 3)
    return d


def stripes(img, box, accent, step=70):
    """Clapper sticks: diagonal stripes clipped to a box."""
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rectangle(_xy(x0, y0, x1, y1), fill=(16, 18, 22, 255))
    hgt = y1 - y0
    x = x0 - hgt
    i = 0
    while x < x1:
        if i % 2 == 0:
            ld.polygon([tuple(_xy(x, y1)), tuple(_xy(x + hgt, y0)), tuple(_xy(x + hgt + step, y0)),
                        tuple(_xy(x + step, y1))], fill=accent + (255,))
        x += step
        i += 1
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rectangle(_xy(x0, y0, x1, y1), fill=255)
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", img.size, 0), mask))
    glow = layer.filter(ImageFilter.GaussianBlur(18 * S))
    img.alpha_composite(glow)
    img.alpha_composite(layer)


def chip(d, xy, text, accent, filled=True, size=26, pad=14):
    f = din(size)
    w = d.textlength(text, font=f) / S
    x, y = xy
    box = (x, y, x + w + 2 * pad, y + size + 14)
    if filled:
        d.rectangle(_xy(*box), fill=accent + (255,))
        d.text(_xy(x + pad, y + 9), text, font=f, fill=BG + (255,))
    else:
        d.rectangle(_xy(*box), outline=FAINT + (255,), width=2 * S)
        d.text(_xy(x + pad, y + 9), text, font=f, fill=DIM + (255,))
    return box[2]


def segbar(d, xy, frac, accent, n=24, seg=22, gap=5, h=22):
    x, y = xy
    on = round(frac * n)
    for i in range(n):
        fill = accent + (255,) if i < on else FAINT + (160,)
        d.polygon([tuple(_xy(x + 6, y)), tuple(_xy(x + seg + 6, y)), tuple(_xy(x + seg, y + h)),
                   tuple(_xy(x, y + h))], fill=fill)
        x += seg + gap


def field(d, xy, label, value, w=380, value_color=INK):
    x, y = xy
    spaced(d, (x, y), label, mono(17, True), DIM + (255,), 3)
    d.text(_xy(x, y + 28), value, font=din(46), fill=value_color + (255,))
    d.line(_xy(x, y + 86, x + w, y + 86), fill=FAINT + (255,), width=2 * S)


def toggles(d, xy, label, options, selected, accent):
    x, y = xy
    spaced(d, (x, y), label, mono(17, True), DIM + (255,), 3)
    cx = x
    for o in options:
        cx = chip(d, (cx, y + 32), o, accent, filled=(o == selected), size=30) + 10


def hud_frame(d, accent, left, right):
    brackets(d, (28, 28, W - 28, H - 28), accent + (200,), 46, 3)
    spaced(d, (60, 44), left, mono(15, True), DIM + (255,), 4)
    spaced(d, (W - 60, 44), right, mono(15, True), DIM + (255,), 4, anchor="ra")


def _finish(img, path):
    img.resize((W, H), Image.LANCZOS).save(path)


def tc(frames, fps=24):
    s, f = divmod(int(frames), fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


# ----------------------------------------------------------------------------- reference panel

def _cover(path, w, h):
    im = Image.open(path).convert("RGB")
    r = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * r)), max(h, round(im.height * r))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h)).convert("RGBA")


def _dashed_rect(d, box, color, dash=14, gap=10, width=2):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        length = abs(bx - ax) + abs(by - ay)
        t = 0
        while t < length:
            u = min(t + dash, length)
            px0 = ax + (bx - ax) * t / length; py0 = ay + (by - ay) * t / length
            px1 = ax + (bx - ax) * u / length; py1 = ay + (by - ay) * u / length
            d.line(_xy(px0, py0, px1, py1), fill=color, width=width * S)
            t += dash + gap


def reference_panel(img, box, a, shot, refs):
    """Reference frames found in pre-production: one hero frame plus up to three thumbnails."""
    x0, y0, x1, y1 = box
    d = panel(img, box, a)
    spaced(d, (x0 + 26, y0 + 22), "REFERENCE", mono(18, True), a + (255,), 6)
    spaced(d, (x1 - 26, y0 + 22), f"{len(refs)} FRAME{'' if len(refs) == 1 else 'S'}", mono(16, True),
           DIM + (255,), 4, anchor="ra")
    hx0, hy0 = x0 + 26, y0 + 62
    hw = x1 - x0 - 52
    hh = round(hw * 9 / 16)
    if not refs:
        _dashed_rect(d, (hx0, hy0, hx0 + hw, hy0 + hh), a + (150,))
        cx, cy = hx0 + hw / 2, hy0 + hh / 2
        d.text(_xy(cx, cy - 34), "+", font=din(90), fill=a + (255,), anchor="mm")
        d.text(_xy(cx, cy + 40), "ADD REFERENCE", font=din(40), fill=INK + (255,), anchor="mm")
        for i, line in enumerate(("Drop images in", f"references/{shot['id']}/")):
            spaced(d, (hx0, hy0 + hh + 30 + i * 30), line, mono(16, True), DIM + (255,), 2)
        return
    hero = _cover(refs[0], hw * S, hh * S)
    img.alpha_composite(hero, (hx0 * S, hy0 * S))
    d = ImageDraw.Draw(img)
    d.rectangle(_xy(hx0, hy0, hx0 + hw, hy0 + hh), outline=a + (255,), width=2 * S)
    brackets(d, (hx0 + 10, hy0 + 10, hx0 + hw - 10, hy0 + hh - 10), INK + (230,), 18, 3)
    spaced(d, (hx0 + 36, hy0 + hh - 40), "REF 01", mono(16, True), INK + (255,), 4)
    tw = (hw - 2 * 14) // 3
    th = round(tw * 9 / 16)
    ty = hy0 + hh + 16
    for i in range(3):
        tx = hx0 + i * (tw + 14)
        if i + 1 < len(refs):
            img.alpha_composite(_cover(refs[i + 1], tw * S, th * S), (tx * S, ty * S))
            d = ImageDraw.Draw(img)
            d.rectangle(_xy(tx, ty, tx + tw, ty + th), outline=a + (200,), width=2 * S)
            spaced(d, (tx + 8, ty + th - 24), f"REF {i + 2:02d}", mono(13, True), INK + (255,), 3)
        else:
            _dashed_rect(d, (tx, ty, tx + tw, ty + th), FAINT + (255,), 8, 8)
            d.text(_xy(tx + tw / 2, ty + th / 2), "+", font=din(40), fill=FAINT + (255,), anchor="mm")


# ----------------------------------------------------------------------------- camera slate

def camera_slate(shot: dict, meta: dict, path: str, max_frames: int, refs: list | None = None) -> None:
    a = ACCENT[shot["kind"]]
    img = background(a)
    d = ImageDraw.Draw(img)
    hud_frame(d, a, f"THEODORE // PRE-PRODUCTION // {meta['production'].upper()}",
              f"SLATE {shot['id']}  ·  PLACEHOLDER")
    stripes(img, (80, 84, W - 80, 150), a)
    d = ImageDraw.Draw(img)

    # header
    spaced(d, (80, 182), "PRODUCTION", mono(17, True), DIM + (255,), 3)
    glow_text(img, (80, 205), meta["production"].upper(), din(78), INK, radius=10, strength=1)
    d = ImageDraw.Draw(img)
    label = {"interview": "INTERVIEW", "instruction": "INSTRUCTION", "broll": "B-ROLL",
             "transition": "TRANSITION", "title": "TITLE / GFX"}[shot["kind"]]
    f = din(34)
    w = d.textlength(label, font=f) / S + 32
    chip(d, (W - 80 - w, 196), label, a, size=34, pad=16)
    rec = "● REC" if shot["kind"] in ("interview", "instruction", "broll") else "● GFX"
    spaced(d, (W - 80 - w - 24, 214), rec, mono(20, True), (255, 70, 80, 255), 3, anchor="ra")

    # roll / scene / take (left column)
    y0, y1 = 300, 590
    boxes = [("ROLL", shot["roll"], (80, y0, 400, y1), INK, 150),
             ("SCENE", shot["scene"], (436, y0, 836, y1), a, 210),
             ("TAKE", shot["take"], (872, y0, 1172, y1), INK, 150)]
    for lab, val, box, col, size in boxes:
        d = panel(img, box, a)
        spaced(d, (box[0] + 26, box[1] + 22), lab, mono(18, True), DIM + (255,), 6)
        glow_text(img, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 + 22), val, din(size), col, anchor="mm",
                  radius=20 if lab == "SCENE" else 8, strength=2 if lab == "SCENE" else 1)
    d = ImageDraw.Draw(img)

    # data rows
    y = 622
    field(d, (80, y), "DIRECTOR", meta["director"], 230)
    field(d, (340, y), "CAMERA / DP", f"{shot['camera']}  ·  {meta['dp']}", 260)
    field(d, (630, y), "DATE", meta["date"], 210)
    field(d, (870, y), "FPS / FORMAT", "23.976 · 1080p", 300)
    y = 740
    toggles(d, (80, y), "INT / EXT", ["INT", "EXT"], None, a)
    toggles(d, (270, y), "DAY / NIGHT", ["DAY", "NIGHT"], None, a)
    toggles(d, (500, y), "SOUND", ["SYNC", "MOS", "NAT", "GFX"], shot["sound"], a)

    reference_panel(img, (1212, 300, W - 80, 846), a, shot, refs or [])
    d = ImageDraw.Draw(img)

    # objective bar
    by = 880
    d = panel(img, (80, by, W - 80, 1010), a, cut=24, alpha=235)
    f = din(56)
    idw = d.textlength(shot["id"], font=f) / S + 40
    d.rectangle(_xy(80, by, 80 + idw, 1010), fill=a + (255,))
    d.text(_xy(80 + idw / 2, by + 70), shot["id"], font=f, fill=BG + (255,), anchor="mm")
    spaced(d, (80 + idw + 30, by + 22), f"OBJECTIVE  ·  {shot['where']}", mono(16, True), a + (255,), 4)
    d.text(_xy(80 + idw + 30, by + 46), shot["desc"].upper(), font=din(56), fill=INK + (255,))
    spaced(d, (W - 110, by + 22), f"PLANNED  {tc(shot['frames'])}", mono(18, True), INK + (255,), 3,
           anchor="ra")
    segbar(d, (W - 110 - 24 * 27 + 5, by + 66), min(1.0, shot["frames"] / max_frames), a)
    _finish(img, path)


# ----------------------------------------------------------------------------- lower third

def lower_third(shot: dict, meta: dict, path: str) -> None:
    a = ACCENT["title"]
    img = background(a, transparent=True)
    x0, y0, x1, y1 = 110, 790, 1080, 950
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.polygon([tuple(_xy(x0, y0)), tuple(_xy(x1, y0)), tuple(_xy(x1 - 40, y1)), tuple(_xy(x0, y1))],
               fill=(9, 12, 18, 225))
    ld.rectangle(_xy(x0, y0, x0 + 10, y1), fill=a + (255,))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle(_xy(x0, y0, x0 + 10, y1), fill=a + (255,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(16 * S)))
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    d.line(_xy(x0, y0, x1, y0), fill=a + (255,), width=3 * S)
    spaced(d, (x0 + 40, y0 + 20), f"{shot['id']}  ·  SCENE {shot['scene']}  ·  LOWER THIRD", mono(16, True),
           a + (255,), 4)
    who = "SUBJECT A" if "subject A" in shot["desc"] else "SUBJECT B" if "subject B" in shot["desc"] else "NAME"
    glow_text(img, (x0 + 40, y0 + 46), f"{who} FULL NAME", din(66), INK, radius=8, strength=1)
    d = ImageDraw.Draw(img)
    spaced(d, (x0 + 42, y0 + 122), "TITLE  /  ROLE  /  ORGANISATION", mono(18, True), DIM + (255,), 3)
    _finish(img, path)


# ----------------------------------------------------------------------------- program slate

def program_slate(meta: dict, path: str, total_frames: int, tracks_a: list[str]) -> None:
    a = ACCENT["program"]
    img = background(a)
    d = ImageDraw.Draw(img)
    hud_frame(d, a, "THEODORE // PROGRAM SLATE", f"VERSION {meta['version']}")
    stripes(img, (80, 84, W - 80, 128), a, step=56)
    d = ImageDraw.Draw(img)
    spaced(d, (80, 160), "PRODUCTION", mono(18, True), DIM + (255,), 4)
    glow_text(img, (78, 200), meta["production"].upper(), din(150), INK, radius=16, strength=2)
    d = ImageDraw.Draw(img)
    spaced(d, (82, 348), meta["title"].upper(), mono(26, True), a + (255,), 5)

    d = panel(img, (80, 400, 1230, 980), a)
    rows = [("CLIENT", meta["client"]), ("DIRECTOR", meta["director"]), ("PRODUCER", meta["producer"]),
            ("EDITOR", meta["editor"]), ("DATE", meta["date"]), ("TRT", tc(total_frames)),
            ("START TC", "01:00:00:00"), ("FRAME RATE", "23.976 NDF"), ("RESOLUTION", "1920 × 1080  ·  16:9"),
            ("COLOUR", "REC.709  ·  GAMMA 2.4"), ("LANGUAGE", meta["language"].upper()),
            ("SUBTITLES", meta["subtitles"].upper())]
    for i, (k, v) in enumerate(rows):
        col, row = divmod(i, 6)
        x, y = 120 + col * 560, 432 + row * 88
        spaced(d, (x, y), k, mono(16, True), DIM + (255,), 4)
        d.text(_xy(x, y + 22), v, font=din(44), fill=INK + (255,))
        d.line(_xy(x, y + 76, x + 500, y + 76), fill=FAINT + (255,), width=2 * S)

    d = panel(img, (1270, 400, W - 80, 980), a)
    spaced(d, (1306, 430), "AUDIO MAP", mono(18, True), a + (255,), 6)
    for i, t in enumerate(tracks_a):
        y = 482 + i * 76
        n, name = t.split(" ", 1)
        d.rectangle(_xy(1306, y, 1376, y + 52), fill=a + (255,))
        d.text(_xy(1341, y + 28), n, font=din(36), fill=BG + (255,), anchor="mm")
        d.text(_xy(1396, y + 8), name, font=din(40), fill=INK + (255,))
        segbar(d, (1640, y + 16), [0.55, 0.7, 0.4, 0.3, 0.6, 0.2][i % 6], a, n=8, seg=14, gap=4, h=20)
    spaced(d, (1306, 482 + 6 * 76 + 6), "STEREO  ·  48 kHz  ·  24-BIT", mono(16, True), DIM + (255,), 3)
    spaced(d, (W / 2, 1022), "PROGRAM STARTS  01:00:00:00", mono(20, True), a + (255,), 8, anchor="ma")
    _finish(img, path)
