"""Hollywood theme: a cinematic AAA-game menu rather than a neon HUD.
2.39 letterbox, film grain, warm teal/orange light, real black-and-white clapper
sticks, Didot + Gill Sans with gold hairlines, a filmstrip reference panel and
controller prompts in the letterbox. Same entry points as design.py."""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from design import _cover, _dashed_rect, _xy, spaced, tc  # shared helpers (2x supersampling)

W, H = 1920, 1080
S = 2
BAR = 138                      # 2.39:1 letterbox
SUP = "/System/Library/Fonts/Supplemental/"
BG = (12, 11, 10)
INK = (242, 234, 218)
DIM = (150, 140, 124)
FAINT = (64, 58, 50)
GOLD = (204, 168, 98)
ACCENT = {"interview": (122, 160, 196), "instruction": (122, 160, 196), "broll": (138, 178, 128),
          "transition": (226, 176, 78), "title": (168, 128, 196), "program": GOLD}
LABEL = {"interview": "INTERVIEW", "instruction": "INSTRUCTION", "broll": "B-ROLL",
         "transition": "TRANSITION", "title": "TITLE"}


def _f(name, size, index=0):
    return ImageFont.truetype(SUP + name, int(size * S), index=index)


def didot(size, bold=False):  # Bodoni 72: same high-contrast look as Didot, but lining numerals
    return _f("Bodoni 72.ttc", size, 2 if bold else 0)


def didot_i(size):
    return _f("Bodoni 72.ttc", size, 1)


def gill(size, weight="semibold"):  # Optima: Gill Sans draws "1" like "I", which is unreadable in IDs
    return _f("Optima.ttc", size, {"regular": 0, "bold": 1, "semibold": 0, "light": 0}[weight])


# ----------------------------------------------------------------------------- surfaces

def background(accent, transparent=False):
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0) if transparent else BG + (255,))
    if transparent:
        return img
    light = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(light)
    g.ellipse(_xy(-600, -500, 1000, 760), fill=(255, 150, 60, 62))      # warm key
    g.ellipse(_xy(1150, 520, 2500, 1500), fill=(40, 120, 140, 46))      # teal fill
    g.ellipse(_xy(300, 300, 1100, 900), fill=accent + (16,))
    img.alpha_composite(light.filter(ImageFilter.GaussianBlur(240 * S)))
    vig = Image.new("L", img.size, 0)
    ImageDraw.Draw(vig).ellipse(_xy(-200, 40, W + 200, H - 40), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(220 * S))
    dark = Image.new("RGBA", img.size, (0, 0, 0, 255))
    dark.putalpha(Image.eval(vig, lambda v: int(200 * (1 - v / 255))))
    img.alpha_composite(dark)
    d = ImageDraw.Draw(img)
    d.rectangle(_xy(0, 0, W, BAR), fill=(0, 0, 0, 255))
    d.rectangle(_xy(0, H - BAR, W, H), fill=(0, 0, 0, 255))
    return img


def _finish(img, path, grain=True):
    out = img.resize((W, H), Image.LANCZOS)
    if grain:
        rgb = out.convert("RGB")
        n = Image.effect_noise((W, H), 46).filter(ImageFilter.GaussianBlur(0.6))
        noisy = ImageChops.overlay(rgb, Image.merge("RGB", (n, n, n)))
        out = Image.blend(rgb, noisy, 0.22)
        bars = ImageDraw.Draw(out)  # keep the letterbox pure black
        bars.rectangle((0, 0, W, BAR - 1), fill=(0, 0, 0))
        bars.rectangle((0, H - BAR, W, H), fill=(0, 0, 0))
        _bar_text(out, img.info.get("bars"))
    out.save(path)


def _bar_text(out, bars):
    """Letterbox furniture is drawn after grain at 1x so it stays crisp."""
    if not bars:
        return
    big = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    tl, tr, prompts, br = bars
    spaced(d, (80, 58), tl, gill(17), DIM + (255,), 6)
    spaced(d, (W - 80, 58), tr, gill(17), DIM + (255,), 6, anchor="ra")
    x = 80
    for key, label in prompts:
        r = 19
        cy = H - BAR / 2
        d.ellipse(_xy(x, cy - r, x + 2 * r, cy + r), outline=INK + (230,), width=2 * S)
        d.text(_xy(x + r, cy + 1), key, font=gill(20, "bold"), fill=INK + (255,), anchor="mm")
        spaced(d, (x + 2 * r + 14, cy - 10), label, gill(18), INK + (220,), 5)
        x += 2 * r + 14 + d.textlength(label, font=gill(18)) / S + 5 * len(label) + 44
    spaced(d, (W - 80, H - BAR / 2 - 10), br, gill(17), DIM + (255,), 5, anchor="ra")
    small = big.resize((W, H), Image.LANCZOS)
    base = out.convert("RGBA")
    base.alpha_composite(small)
    out.paste(base.convert("RGB"))


def soft_glow(img, xy, text, font, color, anchor="la", radius=18, alpha=90):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(_xy(*xy), text, font=font, fill=(255, 170, 90, alpha), anchor=anchor)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius * S)))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(_xy(xy[0] + 3, xy[1] + 5), text, font=font, fill=(0, 0, 0, 170), anchor=anchor)
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(4 * S)))
    ImageDraw.Draw(img).text(_xy(*xy), text, font=font, fill=color + (255,), anchor=anchor)


def rule(d, x0, x1, y, color=GOLD, diamond=None, width=1):
    d.line(_xy(x0, y, x1, y), fill=color + (220,), width=max(1, int(width * S)))
    if diamond is not None:
        r = 7
        d.polygon([tuple(_xy(diamond, y - r)), tuple(_xy(diamond + r, y)), tuple(_xy(diamond, y + r)),
                   tuple(_xy(diamond - r, y))], fill=color + (255,))


def clapper(img, x0, x1, y, h=40, open_deg=-3.2):
    """Black-and-white clapper sticks; the top one hinged open."""
    def stick(width):
        s = Image.new("RGBA", (int(width * S), int(h * S)), (245, 242, 236, 255))
        d = ImageDraw.Draw(s)
        step = 64
        x = -h
        i = 0
        while x < width:
            if i % 2 == 0:
                d.polygon([(int(x * S), int(h * S)), (int((x + h) * S), 0), (int((x + h + step) * S), 0),
                           (int((x + step) * S), int(h * S))], fill=(14, 13, 12, 255))
            x += step
            i += 1
        d.rectangle((0, 0, s.width - 1, s.height - 1), outline=(0, 0, 0, 255), width=2 * S)
        return s
    w = x1 - x0
    bottom, top = stick(w), stick(w)
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle(_xy(x0 + 6, y + h + 10, x1 + 6, y + 2 * h + 18), fill=(0, 0, 0, 160))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10 * S)))
    img.alpha_composite(bottom, (int(x0 * S), int((y + h + 8) * S)))
    rot = top.rotate(-open_deg, resample=Image.BICUBIC, expand=True, center=(0, top.height))
    off_y = rot.height - top.height
    img.alpha_composite(rot, (int(x0 * S), int(y * S) - off_y))
    d = ImageDraw.Draw(img)
    d.ellipse(_xy(x0 + 10, y + h - 4, x0 + 30, y + h + 16), fill=(120, 120, 124, 255), outline=(0, 0, 0, 255))


def plate(d, box, color=GOLD, fill=None):
    x0, y0, x1, y1 = box
    if fill:
        d.rectangle(_xy(*box), fill=fill)
    d.rectangle(_xy(*box), outline=color + (255,), width=S)
    d.rectangle(_xy(x0 + 5, y0 + 5, x1 - 5, y1 - 5), outline=color + (110,), width=S)


def label(d, xy, text, color=GOLD, size=16, anchor="la"):
    spaced(d, xy, text, gill(size), color + (255,), 5, anchor=anchor)


def option(d, x, y, opts, selected, a):
    for o in opts:
        f = gill(20)
        w = d.textlength(o, font=f) / S + 4 * len(o) + 26
        if o == selected:
            d.rectangle(_xy(x, y, x + w, y + 36), fill=a + (255,))
            spaced(d, (x + 13, y + 8), o, f, BG + (255,), 4)
        else:
            d.rectangle(_xy(x, y, x + w, y + 36), outline=FAINT + (255,), width=S)
            spaced(d, (x + 13, y + 8), o, f, DIM + (255,), 4)
        x += w + 8
    return x


# ----------------------------------------------------------------------------- reference filmstrip

def _strip(d, box, holes=True):
    x0, y0, x1, y1 = box
    d.rectangle(_xy(*box), fill=(6, 6, 6, 255))
    if holes:
        for x in range(int(x0) + 12, int(x1) - 12, 26):
            for yy in (y0 + 7, y1 - 19):
                d.rounded_rectangle(_xy(x, yy, x + 13, yy + 12), 2 * S, fill=(44, 40, 34, 255))


def reference_panel(img, box, a, shot, refs):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    label(d, (x0, y0), "REFERENCE")
    spaced(d, (x1, y0), f"{len(refs)} STILL{'' if len(refs) == 1 else 'S'}", gill(16), DIM + (255,), 5, anchor="ra")
    rule(d, x0, x1, y0 + 30, GOLD)
    sy = y0 + 46
    hw = x1 - x0
    hh = round((hw - 0) * 9 / 16) + 38
    _strip(d, (x0, sy, x1, sy + hh))
    fx0, fy0, fx1, fy1 = x0 + 10, sy + 27, x1 - 10, sy + hh - 27
    if refs:
        img.alpha_composite(_cover(refs[0], int((fx1 - fx0) * S), int((fy1 - fy0) * S)), (int(fx0 * S), int(fy0 * S)))
        d = ImageDraw.Draw(img)
        spaced(d, (fx0 + 14, fy1 - 30), "REF 01", gill(15), INK + (255,), 5)
    else:
        d.rectangle(_xy(fx0, fy0, fx1, fy1), fill=(20, 18, 16, 255))
        _dashed_rect(d, (fx0 + 16, fy0 + 14, fx1 - 16, fy1 - 14), GOLD + (120,), 10, 10, 1)
        cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
        d.text(_xy(cx, cy - 26), "+", font=didot(64), fill=GOLD + (255,), anchor="mm")
        d.text(_xy(cx, cy + 34), "Add reference", font=didot_i(34), fill=INK + (255,), anchor="mm")
        spaced(d, (cx, cy + 74), f"DROP STILLS IN  references/{shot['id']}/", gill(13), DIM + (255,), 3, anchor="ma")
    ty = sy + hh + 14
    tw = (hw - 16) / 3
    th = round(tw * 9 / 16) - 12
    for i in range(3):
        tx = x0 + i * (tw + 8)
        _strip(d, (tx, ty, tx + tw, ty + th), holes=False)
        if i + 1 < len(refs):
            img.alpha_composite(_cover(refs[i + 1], int((tw - 8) * S), int((th - 8) * S)),
                                (int((tx + 4) * S), int((ty + 4) * S)))
            d = ImageDraw.Draw(img)
            spaced(d, (tx + 10, ty + th - 24), f"REF {i + 2:02d}", gill(13), INK + (255,), 4)
        else:
            d.text(_xy(tx + tw / 2, ty + th / 2), "+", font=didot(30), fill=FAINT + (255,), anchor="mm")


# ----------------------------------------------------------------------------- camera slate

def camera_slate(shot: dict, meta: dict, path: str, max_frames: int, refs: list | None = None) -> None:
    a = ACCENT[shot["kind"]]
    img = background(a)
    clapper(img, 80, W - 80, 150)
    d = ImageDraw.Draw(img)

    # title block
    label(d, (80, 268), "PRODUCTION")
    soft_glow(img, (78, 292), meta["production"].upper(), didot(52), INK, radius=16, alpha=60)
    d = ImageDraw.Draw(img)
    tag = LABEL[shot["kind"]]
    f = gill(20)
    tw = d.textlength(tag, font=f) / S + 5 * len(tag) + 34
    d.rectangle(_xy(1160 - tw, 286, 1160, 326), fill=a + (255,))
    spaced(d, (1160 - tw + 17, 296), tag, f, BG + (255,), 5)

    # slate table: roll | scene | take
    ty0, ty1 = 372, 600
    rule(d, 80, 1160, ty0, GOLD)
    rule(d, 80, 1160, ty0 + 6, GOLD, width=0.5)
    rule(d, 80, 1160, ty1, GOLD)
    cols = [(80, 380, "ROLL", shot["roll"], 112), (380, 860, "SCENE", shot["scene"], 196),
            (860, 1160, "TAKE", shot["take"], 112)]
    for cx0, cx1, lab, val, size in cols:
        if cx0 > 80:
            d.line(_xy(cx0, ty0 + 20, cx0, ty1 - 20), fill=GOLD + (140,), width=S)
        label(d, ((cx0 + cx1) / 2, ty0 + 22), lab, anchor="ma")
        if lab == "SCENE":
            soft_glow(img, ((cx0 + cx1) / 2, (ty0 + ty1) / 2 + 30), val, didot(size, True), INK, anchor="mm",
                      radius=26, alpha=110)
        else:
            soft_glow(img, ((cx0 + cx1) / 2, (ty0 + ty1) / 2 + 26), val, didot(size), INK, anchor="mm",
                      radius=10, alpha=30)
        d = ImageDraw.Draw(img)

    # credits row
    y = 624
    for x, lab, val in ((80, "DIRECTOR", meta["director"]), (330, "CAMERA  ·  DP", f"{shot['camera']} · {meta['dp']}"),
                        (640, "DATE", meta["date"]), (890, "FRAME RATE", "23.976 · 1080p")):
        label(d, (x, y), lab, DIM, 15)
        d.text(_xy(x, y + 26), val, font=didot(34), fill=INK + (255,))
    y = 728
    label(d, (80, y), "INT · EXT", DIM, 15)
    option(d, 80, y + 26, ["INT", "EXT"], None, a)
    label(d, (290, y), "DAY · NIGHT", DIM, 15)
    option(d, 290, y + 26, ["DAY", "NIGHT"], None, a)
    label(d, (540, y), "SOUND", DIM, 15)
    option(d, 540, y + 26, ["SYNC", "MOS", "NAT", "GFX"], shot["sound"], a)

    reference_panel(img, (1220, 268, W - 80, 820), a, shot, refs or [])
    d = ImageDraw.Draw(img)

    # objective
    oy = 838
    rule(d, 80, W - 80, oy, GOLD, diamond=W / 2)
    f = didot(34, True)
    idw = d.textlength(shot["id"], font=f) / S + 44
    plate(d, (80, oy + 22, 80 + idw, oy + 86), GOLD, fill=(0, 0, 0, 140))
    d.text(_xy(80 + idw / 2, oy + 56), shot["id"], font=f, fill=GOLD + (255,), anchor="mm")
    label(d, (110 + idw, oy + 22), f"OBJECTIVE  ·  {shot['where']}".upper(), a, 15)
    d.text(_xy(110 + idw, oy + 42), shot["desc"], font=didot(40), fill=INK + (255,))
    spaced(d, (W - 80, oy + 24), f"PLANNED   {tc(shot['frames'])}", gill(18), INK + (255,), 4, anchor="ra")
    frac = min(1.0, shot["frames"] / max_frames)
    bx0, bx1, by = W - 480, W - 80, oy + 70
    d.line(_xy(bx0, by, bx1, by), fill=FAINT + (255,), width=2 * S)
    d.line(_xy(bx0, by, bx0 + (bx1 - bx0) * frac, by), fill=a + (255,), width=4 * S)
    xm = bx0 + (bx1 - bx0) * frac
    d.polygon([tuple(_xy(xm, by - 8)), tuple(_xy(xm + 8, by)), tuple(_xy(xm, by + 8)), tuple(_xy(xm - 8, by))],
              fill=INK + (255,))

    img.info["bars"] = (f"A THEODORE PRODUCTION  ·  {meta['production'].upper()}",
                        f"SLATE  {shot['id']}  ·  SCENE {shot['scene']}  ·  TAKE {shot['take']}",
                        [("A", "ACTION"), ("B", "CUT"), ("Y", "REFERENCE"), ("X", "SHOT LIST")],
                        "23.976  ·  1920 × 1080  ·  2.39 PROTECT")
    _finish(img, path)


# ----------------------------------------------------------------------------- lower third

def lower_third(shot: dict, meta: dict, path: str) -> None:
    img = background(GOLD, transparent=True)
    x0, y0 = 120, 770
    grad = Image.new("L", (1000 * S, 170 * S), 0)
    gd = ImageDraw.Draw(grad)
    for x in range(grad.width):
        gd.line((x, 0, x, grad.height), fill=int(190 * max(0.0, 1 - x / grad.width) ** 1.4))
    shade = Image.new("RGBA", grad.size, (0, 0, 0, 255))
    shade.putalpha(grad.filter(ImageFilter.GaussianBlur(30 * S)))
    img.alpha_composite(shade, ((x0 - 40) * S, (y0 - 20) * S))
    d = ImageDraw.Draw(img)
    who = "Subject A" if "subject A" in shot["desc"] else "Subject B" if "subject B" in shot["desc"] else "Name"
    soft_glow(img, (x0, y0), f"{who} Full Name", didot(64), INK, radius=12, alpha=50)
    d = ImageDraw.Draw(img)
    rule(d, x0, x0 + 520, y0 + 88, GOLD, diamond=x0 + 520)
    spaced(d, (x0, y0 + 104), "TITLE  ·  ROLE  ·  ORGANISATION", gill(18), INK + (220,), 6)
    spaced(d, (x0, y0 - 28), f"{shot['id']}  ·  SCENE {shot['scene']}", gill(14), GOLD + (230,), 5)
    _finish(img, path, grain=False)


# ----------------------------------------------------------------------------- program slate

def program_slate(meta: dict, path: str, total_frames: int, tracks_a: list[str]) -> None:
    img = background(GOLD)
    d = ImageDraw.Draw(img)
    spaced(d, (W / 2, 178), "A  THEODORE  PRODUCTION", gill(20), GOLD + (255,), 10, anchor="ma")
    soft_glow(img, (W / 2, 278), meta["production"].upper(), didot(108), INK, anchor="mm", radius=30, alpha=90)
    d = ImageDraw.Draw(img)
    rule(d, W / 2 - 420, W / 2 + 420, 350, GOLD, diamond=W / 2)
    spaced(d, (W / 2, 372), meta["title"].upper(), gill(20), INK + (230,), 8, anchor="ma")

    rows = [("CLIENT", meta["client"]), ("DIRECTOR", meta["director"]), ("PRODUCER", meta["producer"]),
            ("EDITOR", meta["editor"]), ("DATE", meta["date"]), ("VERSION", meta["version"]),
            ("RUNNING TIME", tc(total_frames)), ("START", "01:00:00:00"), ("FRAME RATE", "23.976 NDF"),
            ("PICTURE", "1920 × 1080  ·  2.39 protect"), ("COLOUR", "Rec.709  ·  Gamma 2.4"),
            ("LANGUAGE", meta["language"]), ("SUBTITLES", meta["subtitles"])]

    def leader_row(x0, x1, y, k, v):
        spaced(d, (x0, y + 8), k, gill(17), GOLD + (255,), 5)
        kw = d.textlength(k, font=gill(17)) / S + 5 * len(k)
        vf = didot(30)
        vw = d.textlength(v, font=vf) / S
        d.text(_xy(x1, y), v, font=vf, fill=INK + (255,), anchor="ra")
        x = x0 + kw + 14
        while x < x1 - vw - 14:
            d.ellipse(_xy(x, y + 22, x + 2.6, y + 24.6), fill=FAINT + (255,))
            x += 9

    for i, (k, v) in enumerate(rows):
        col, row = divmod(i, 7)
        leader_row(150 + col * 560, 150 + col * 560 + 500, 430 + row * 62, k, v)
    ax0, ax1 = 1290, W - 150
    label(d, (ax0, 430), "AUDIO")
    rule(d, ax0, ax1, 462, GOLD)
    for i, t in enumerate(tracks_a):
        n, name = t.split(" ", 1)
        y = 482 + i * 62
        d.text(_xy(ax0, y), n, font=didot(30, True), fill=GOLD + (255,))
        d.text(_xy(ax0 + 70, y), {"SFX": "SFX"}.get(name, name.title()), font=didot(30), fill=INK + (255,))
    spaced(d, (ax0, 482 + 6 * 62 + 4), "STEREO  ·  48 kHz  ·  24-BIT", gill(15), DIM + (255,), 4)
    img.info["bars"] = ("PROGRAM SLATE", f"VERSION  {meta['version'].upper()}",
                        [("A", "PRESS START")], "PROGRAM BEGINS  01:00:00:00")
    _finish(img, path)
