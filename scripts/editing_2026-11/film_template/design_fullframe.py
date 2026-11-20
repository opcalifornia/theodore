"""Full-frame theme: the reference still IS the frame. The slate information sits on the left and along the bottom,
frosted into the picture by a blurred + darkened gradient so the scene stays visible through it. With no reference
yet the background is a neutral cinematic placeholder and a prompt shows where to add one. Same entry points as
design.py / design_hollywood.py."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from design import ACCENT as _ACC, DIM, INK, _cover, _dashed_rect, _xy, spaced, tc  # shared helpers (2x supersampling)

ACCENT = dict(_ACC, action=(255, 128, 64), dialogue=(46, 168, 255))

W, H, S = 1920, 1080, 2
SUP = "/System/Library/Fonts/Supplemental/"
DIN = SUP + "DIN Condensed Bold.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"
AVENIR = SUP + "Avenir Next.ttc"
LEFT_W, BOTTOM_H = 700, 330                 # how far the frosted blend reaches in from the left / up from the bottom
LABEL = {"interview": "INTERVIEW", "instruction": "INSTRUCTION", "broll": "B-ROLL",
         "transition": "TRANSITION", "title": "TITLE", "action": "ACTION", "dialogue": "DIALOGUE"}


def din(size):
    return ImageFont.truetype(DIN, int(size * S))


def ave(size, face="demi"):
    return ImageFont.truetype(AVENIR, int(size * S), index={"demi": 2, "regular": 7, "heavy": 8, "medium": 5, "bold": 0}[face])


def mono(size):
    f = ImageFont.truetype(MONO, int(size * S))
    try:
        f.set_variation_by_name("Bold")
    except Exception:  # noqa: BLE001
        pass
    return f


# ----------------------------------------------------------------------------- scene + blend

def placeholder_scene(accent, seed=0):
    """Neutral cinematic stand-in (soft light, grain-free) used until a reference still is added."""
    y, x = np.mgrid[0:H * S, 0:W * S].astype(np.float32)
    x /= W * S
    y /= H * S
    base = np.zeros((H * S, W * S, 3), np.float32)
    base += np.array([14, 15, 18], np.float32)
    for cx, cy, r, col, a in ((0.78, 0.30, 0.55, (255, 168, 96), 0.36), (0.55, 0.80, 0.70, (40, 110, 140), 0.30),
                              (0.95, 0.95, 0.5, accent, 0.18)):
        d = np.sqrt(((x - cx) * 1.78) ** 2 + (y - cy) ** 2) / r
        base += np.clip(1 - d, 0, 1)[..., None] ** 2 * np.array(col, np.float32) * a
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def blend_masks():
    y, x = np.mgrid[0:H * S, 0:W * S].astype(np.float32)
    left = np.clip(1 - x / (LEFT_W * S), 0, 1) ** 1.35
    bottom = np.clip((y - (H - BOTTOM_H) * S) / (BOTTOM_H * S), 0, 1) ** 1.25
    return 1 - (1 - left) * (1 - bottom)


_MASK = None


def frosted(scene: Image.Image) -> Image.Image:
    """Blur + darken the scene under the info zones so type stays readable and the picture still shows through."""
    global _MASK
    if _MASK is None:
        _MASK = blend_masks()
    m = _MASK[..., None]
    a = np.asarray(scene.convert("RGB"), np.float32)
    blur = np.asarray(scene.convert("RGB").filter(ImageFilter.GaussianBlur(26 * S)), np.float32)
    out = a * (1 - 0.55 * m) + blur * (0.55 * m)            # frost
    out *= (1 - 0.74 * m)                                    # darken toward the edge
    yy, xx = np.mgrid[0:H * S, 0:W * S].astype(np.float32)   # gentle overall vignette
    v = ((xx / (W * S) - 0.55) ** 2 + (yy / (H * S) - 0.45) ** 2) ** 0.5
    out *= (1 - 0.35 * np.clip(v - 0.35, 0, 1))[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def base_image(refs, accent):
    scene = _cover(refs[0], W * S, H * S) if refs else placeholder_scene(accent)
    img = frosted(scene)
    grid = Image.new("RGBA", img.size, (0, 0, 0, 0))     # thirds: composition guides, only over the
    g = ImageDraw.Draw(grid)                             # clear part of the picture (fade out under the info zones)
    for gx in (W / 3, 2 * W / 3):
        g.line(_xy(gx, 0, gx, H), fill=(255, 255, 255, 40), width=S)
    for gy in (H / 3, 2 * H / 3):
        g.line(_xy(0, gy, W, gy), fill=(255, 255, 255, 40), width=S)
    alpha = np.asarray(grid.getchannel("A"), np.float32) * np.clip(1 - 1.6 * _MASK, 0, 1)
    grid.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    img.alpha_composite(grid)
    return img


def label(d, xy, text, color=DIM, size=14, anchor="la"):
    spaced(d, xy, text, mono(size), tuple(color) + (255,), 4, anchor=anchor)


def toggle(d, x, y, opts, selected, a):
    for o in opts:
        f = din(24)
        w = d.textlength(o, font=f) / S + 24
        if o == selected:
            d.rounded_rectangle(_xy(x, y, x + w, y + 34), 5 * S, fill=tuple(a) + (255,))
            d.text(_xy(x + 12, y + 5), o, font=f, fill=(10, 12, 14, 255))
        else:
            d.rounded_rectangle(_xy(x, y, x + w, y + 34), 5 * S, outline=(255, 255, 255, 90), width=S)
            d.text(_xy(x + 12, y + 5), o, font=f, fill=(255, 255, 255, 170))
        x += w + 8
    return x


def _finish(img, path):
    img.resize((W, H), Image.LANCZOS).convert("RGB").save(path)


# ----------------------------------------------------------------------------- camera slate

def camera_slate(shot: dict, meta: dict, path: str, max_frames: int, refs: list | None = None) -> None:
    refs = refs or []
    a = ACCENT[shot["kind"]]
    img = base_image(refs, a)
    d = ImageDraw.Draw(img)
    x0 = 80

    label(d, (x0, 58), "PRODUCTION")
    d.text(_xy(x0, 78), meta["production"], font=ave(30), fill=INK + (255,))
    label(d, (W - 80, 58), f"SLATE  {shot['id']}   ·   23.976  ·  1080p", (255, 255, 255, 170)[:3], 14, "ra")

    d.rectangle(_xy(x0, 142, x0 + 5, 182), fill=tuple(a) + (255,))
    d.text(_xy(x0 + 20, 140), LABEL[shot["kind"]], font=din(40), fill=tuple(a) + (255,))
    label(d, (x0 + 20 + d.textlength(LABEL[shot["kind"]], font=din(40)) / S + 18, 156), shot["where"].upper(), DIM, 13)

    label(d, (x0, 214), "SCENE")
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).text(_xy(x0 - 4, 244), shot["scene"], font=din(250), fill=tuple(a) + (150,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(22 * S)))
    d = ImageDraw.Draw(img)
    d.text(_xy(x0 - 4, 244), shot["scene"], font=din(250), fill=INK + (255,))

    for cx, lab, val in ((x0, "ROLL", shot["roll"]), (x0 + 250, "TAKE", shot["take"])):
        label(d, (cx, 470), lab)
        d.text(_xy(cx, 490), val, font=din(84), fill=INK + (255,))
    d.line(_xy(x0, 600, x0 + 480, 600), fill=(255, 255, 255, 60), width=S)

    for cx, cy, lab, val in ((x0, 618, "CAMERA  ·  DP", f"{shot['camera']}  ·  {meta['dp']}"),
                             (x0 + 250, 618, "DIRECTOR", meta["director"]),
                             (x0, 690, "DATE", meta["date"]), (x0 + 250, 690, "FORMAT", "23.976 · 1080p")):
        label(d, (cx, cy), lab, DIM, 13)
        d.text(_xy(cx, cy + 20), val, font=ave(24, "medium"), fill=INK + (255,))
    label(d, (x0, 760), "INT / EXT")
    toggle(d, x0, 780, ["INT", "EXT"], None, a)
    label(d, (x0 + 200, 760), "DAY / NIGHT")
    toggle(d, x0 + 200, 780, ["DAY", "NIGHT"], None, a)
    label(d, (x0, 832), "SOUND")
    toggle(d, x0, 852, ["SYNC", "MOS", "NAT", "GFX"], shot["sound"], a) if False else None

    # bottom strip
    y = 905
    d.rectangle(_xy(x0, y, x0 + 5, y + 110), fill=tuple(a) + (255,))
    label(d, (x0 + 22, y), f"OBJECTIVE  ·  {shot['id']}", a, 14)
    d.text(_xy(x0 + 22, y + 22), shot["desc"], font=ave(46), fill=INK + (255,))
    label(d, (x0 + 22, y + 88), f"PLANNED  {tc(shot['frames'])}", INK, 15)
    bx0, bx1, by = x0 + 330, 1130, y + 98
    d.line(_xy(bx0, by, bx1, by), fill=(255, 255, 255, 70), width=2 * S)
    frac = min(1.0, shot["frames"] / max_frames)
    d.line(_xy(bx0, by, bx0 + (bx1 - bx0) * frac, by), fill=tuple(a) + (255,), width=4 * S)

    # extra references (REF 02..04) bottom right; the first is the background itself
    tw, th = 176, 99
    names = shot.get("ref_labels") or [f"REF {i + 1:02d}" for i in range(4)]
    label(d, (1840, 886), ("BACKGROUND = " + names[0] + "   ·   THUMBNAILS: " + " / ".join(names[1:4]))[:90] if refs else "ADD REFERENCE STILLS", DIM, 12, "ra")
    for i in range(3):
        tx = 1840 - (3 - i) * (tw + 12) + 12
        ty = 906
        if i + 1 < len(refs):
            img.alpha_composite(_cover(refs[i + 1], tw * S, th * S), (tx * S, ty * S))
            d = ImageDraw.Draw(img)
            d.rectangle(_xy(tx, ty, tx + tw, ty + th), outline=tuple(a) + (200,), width=S)
            label(d, (tx + 8, ty + th - 22), names[i + 1] if i + 1 < len(names) else f"REF {i + 2:02d}", INK, 11)
        else:
            d.rounded_rectangle(_xy(tx, ty, tx + tw, ty + th), 4 * S, fill=(0, 0, 0, 90))
            _dashed_rect(d, (tx, ty, tx + tw, ty + th), (255, 255, 255, 90), 8, 7, 1)
            d.text(_xy(tx + tw / 2, ty + th / 2), "+", font=din(34), fill=(255, 255, 255, 120), anchor="mm")
    label(d, (1840, 1012), f"references/{shot['id']}/", DIM, 11, "ra")
    # sound toggle drawn last so it sits in the left column above the strip
    toggle(d, x0, 852, ["SYNC", "MOS", "NAT", "GFX"], shot["sound"], a)
    if not refs:
        d.text(_xy(1130, 470), "+", font=din(120), fill=(255, 255, 255, 150), anchor="mm")
        spaced(d, (1130, 548), "DROP A REFERENCE STILL HERE", mono(15), (255, 255, 255, 190), 6, anchor="ma")
        spaced(d, (1130, 576), f"references/{shot['id']}/  ·  becomes this frame's background", mono(13),
               (255, 255, 255, 130), 3, anchor="ma")
    _finish(img, path)


# ----------------------------------------------------------------------------- lower third

def lower_third(shot: dict, meta: dict, path: str) -> None:
    a = ACCENT["title"]
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    yy, xx = np.mgrid[0:H * S, 0:W * S].astype(np.float32)
    m = np.clip(1 - xx / (1100 * S), 0, 1) ** 1.4 * np.clip((yy - 700 * S) / (260 * S), 0, 1)
    shade = np.zeros((H * S, W * S, 4), np.uint8)
    shade[..., 3] = (m * 210).astype(np.uint8)
    img.alpha_composite(Image.fromarray(shade, "RGBA"))
    d = ImageDraw.Draw(img)
    x0, y0 = 110, 800
    who = "Subject A" if "subject A" in shot["desc"] else "Subject B" if "subject B" in shot["desc"] else "Name"
    d.rectangle(_xy(x0, y0 + 4, x0 + 6, y0 + 112), fill=tuple(a) + (255,))
    d.text(_xy(x0 + 26, y0 - 6), f"{who} Full Name", font=ave(58), fill=(255, 255, 255, 255))
    spaced(d, (x0 + 28, y0 + 82), "TITLE  ·  ROLE  ·  ORGANISATION", mono(16), (255, 255, 255, 200), 5)
    spaced(d, (x0 + 28, y0 - 28), f"{shot['id']}  ·  SCENE {shot['scene']}", mono(13), tuple(a) + (255,), 4)
    img.resize((W, H), Image.LANCZOS).save(path)


# ----------------------------------------------------------------------------- program slate

def program_slate(meta: dict, path: str, total_frames: int, tracks_a: list[str]) -> None:
    a = ACCENT["program"]
    img = base_image([], a)
    d = ImageDraw.Draw(img)
    x0 = 80
    label(d, (x0, 58), "A THEODORE PRODUCTION")
    label(d, (W - 80, 58), f"PROGRAM SLATE   ·   {meta['version'].upper()}", (255, 255, 255), 14, "ra")
    d.rectangle(_xy(x0, 108, x0 + 5, 232), fill=tuple(a) + (255,))
    d.text(_xy(x0 + 22, 100), meta["production"], font=ave(56, "heavy"), fill=INK + (255,))
    label(d, (x0 + 24, 186), meta["title"].upper(), a, 16)
    rows = [("CLIENT", meta["client"]), ("DIRECTOR", meta["director"]), ("PRODUCER", meta["producer"]),
            ("EDITOR", meta["editor"]), ("DATE", meta["date"]), ("VERSION", meta["version"]),
            ("RUN TIME", tc(total_frames)), ("START", "01:00:00:00"), ("FRAME RATE", "23.976 NDF"),
            ("PICTURE", "1920 × 1080"), ("COLOUR", "Rec.709 · 2.4"), ("LANGUAGE", meta["language"]),
            ("SUBTITLES", meta["subtitles"])]
    for i, (k, v) in enumerate(rows):
        y = 262 + i * 44
        label(d, (x0, y + 8), k, DIM, 13)
        d.text(_xy(x0 + 190, y), v, font=ave(26, "medium"), fill=INK + (255,))
        d.line(_xy(x0, y + 38, x0 + 470, y + 38), fill=(255, 255, 255, 36), width=S)
    # bottom: audio map
    y = 930
    label(d, (x0, y - 28), "AUDIO MAP  ·  STEREO  ·  48 kHz  ·  24-BIT", DIM, 13)
    cx = x0
    for t in tracks_a:
        n, name = t.split(" ", 1)
        d.rounded_rectangle(_xy(cx, y, cx + 50, y + 44), 5 * S, fill=tuple(a) + (255,))
        d.text(_xy(cx + 25, y + 24), n, font=din(30), fill=(10, 12, 14, 255), anchor="mm")
        d.text(_xy(cx + 62, y + 5), name, font=din(32), fill=INK + (255,))
        cx += 62 + d.textlength(name, font=din(32)) / S + 38
    label(d, (W - 80, 972), "PROGRAM BEGINS  01:00:00:00", INK, 16, "ra")
    spaced(d, (1130, 470), "KEY ART / LOGO", mono(15), (255, 255, 255, 170), 6, anchor="ma")
    _dashed_rect(d, (820, 330, 1440, 610), (255, 255, 255, 80), 12, 10, 1)
    _finish(img, path)
