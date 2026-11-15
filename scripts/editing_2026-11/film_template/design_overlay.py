"""Overlay theme for the shot-breakdown cut: a transparent 1920x1080 PNG per shot (dark gradient on the left and bottom
edges + the Full Frame info) that sits on V2 over the screen recording and changes at every cut. Reuses the Full Frame
helpers so it looks like the third template. Fields come from labels.json; anything not seen clearly is shown as
UNVERIFIED rather than guessed."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

import design_fullframe as F
from design_fullframe import W, H, S, DIM, INK, ave, din, label, mono, _xy, spaced, tc

_M = None


def _shade():
    global _M
    if _M is None:
        _M = F.blend_masks()
    a = (np.clip(_M * 0.80, 0, 1) * 255).astype(np.uint8)
    rgba = np.zeros((H * S, W * S, 4), np.uint8)
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")


def fit(d, text, font_fn, size, max_w, face=None):
    """Shrink a line until it fits max_w (px at 1x)."""
    while size > 12:
        f = font_fn(size, face) if face else font_fn(size)
        if d.textlength(text, font=f) / S <= max_w:
            return f
        size -= 2
    return font_fn(12, face) if face else font_fn(12)


def overlay(shot: dict, lab: dict, meta: dict, path: str, total_shots: int, max_frames: int) -> None:
    a = F.ACCENT["action" if shot.get("kind", "action") == "action" else shot["kind"]]
    img = _shade()
    d = ImageDraw.Draw(img)
    x0 = 80
    verified = bool(lab.get("verified"))
    label(d, (x0, 58), "SHOT BREAKDOWN")
    d.text(_xy(x0, 78), meta["production"], font=ave(26), fill=INK + (255,))
    label(d, (W - 80, 58), f"{shot['id']}   ·   {shot['n']} / {total_shots}", (255, 255, 255), 14, "ra")

    label(d, (x0, 142), "SHOT")
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).text(_xy(x0 - 4, 160), str(shot["n"]), font=din(210), fill=tuple(a) + (150,))
    from PIL import ImageFilter
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(20 * S)))
    d = ImageDraw.Draw(img)
    d.text(_xy(x0 - 4, 160), str(shot["n"]), font=din(210), fill=INK + (255,))

    rows = [("SIZE", lab.get("size")), ("ANGLE", lab.get("angle")), ("MOVEMENT", lab.get("movement")),
            ("LENS FEEL", lab.get("lens")), ("LIGHTING", lab.get("light"))]
    y = 408
    for k, v in rows:
        label(d, (x0, y), k, DIM, 13)
        txt = v if v else "UNVERIFIED"
        col = INK if v else (255, 190, 90)
        f = fit(d, txt, ave, 25, 560, "medium")
        d.text(_xy(x0, y + 18), txt, font=f, fill=tuple(col) + (255,))
        d.line(_xy(x0, y + 54, x0 + 540, y + 54), fill=(255, 255, 255, 40), width=S)
        y += 68

    by = 905
    d.rectangle(_xy(x0, by, x0 + 5, by + 112), fill=tuple(a) + (255,))
    label(d, (x0 + 22, by), f"ACTION  ·  IN {tc(shot['f_in'])}  OUT {tc(shot['f_out'])}", a, 14)
    txt = lab.get("action") or "action UNVERIFIED"
    f = fit(d, txt, ave, 44, 1250, "demi")
    d.text(_xy(x0 + 22, by + 22), txt, font=f, fill=INK + (255,))
    label(d, (x0 + 22, by + 88), f"{shot['frames']} FRAMES  ·  {shot['frames'] * 1001 / 24000:.2f} S", INK, 15)
    bx0, bx1, byy = x0 + 330, 1130, by + 98
    d.line(_xy(bx0, byy, bx1, byy), fill=(255, 255, 255, 70), width=2 * S)
    d.line(_xy(bx0, byy, bx0 + (bx1 - bx0) * min(1.0, shot["frames"] / max_frames), byy), fill=tuple(a) + (255,), width=4 * S)

    chip = "LABELS CHECKED ON SCREEN" if verified else "READ FROM START + END FRAMES  ·  BEST READ"
    label(d, (1840, 940), chip, (120, 255, 170) if verified else (255, 190, 90), 12, "ra")
    snd = lab.get("sound") or "SOUND: NOT VERIFIED (NO AUDIO ACCESS)"
    label(d, (1840, 962), snd[:70].upper(), DIM, 12, "ra")
    img.resize((W, H), Image.LANCZOS).save(path)
